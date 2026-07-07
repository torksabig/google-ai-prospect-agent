"""Outreach-ready contact filtering for pipeline export."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

_PHONE_DIGITS = re.compile(r"\d{6,}")

# Single-token names that are titles/roles, not people.
_TITLE_KEYWORDS = frozenset(
    {
        "ceo",
        "cto",
        "coo",
        "cfo",
        "cio",
        "cmo",
        "cso",
        "cpo",
        "vp",
        "svp",
        "evp",
        "director",
        "manager",
        "president",
        "chairman",
        "chairwoman",
        "founder",
        "owner",
        "partner",
        "toimitusjohtaja",
        "teknologiajohtaja",
        "tutkimusjohtaja",
        "kehitysjohtaja",
        "liiketoimintajohtaja",
        "myyntijohtaja",
        "innovaatiojohtaja",
        "johtaja",
        "hallitus",
    }
)

_GENERIC_NAMES = frozenset(
    {
        "contact",
        "unknown",
        "n/a",
        "na",
        "none",
        "-",
        "—",
        "tbd",
        "info",
        "sales",
        "support",
        "admin",
        "yhteyshenkilö",
        "yhteys",
        "asiakaspalvelu",
        "customer service",
        "customer support",
        "reception",
        "front desk",
        "sales team",
        "support team",
    }
)

_ALLOWED_VERIFY_STATUSES = frozenset(
    {
        "person_page_match",
        "dial_confirmed",
    }
)

REJECT_REASONS = (
    "empty_contact_name",
    "title_only_name",
    "generic_contact_name",
    "company_like_contact_name",
    "empty_contact_phone",
    "invalid_phone",
    "verification_failed",
)

_COMPANY_SUFFIXES = frozenset(
    {
        "oy",
        "oyj",
        "ltd",
        "inc",
        "llc",
        "plc",
        "ab",
        "gmbh",
        "group",
        "company",
        "co",
    }
)


def _ascii_fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def phone_digits(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


_PLACEHOLDER_PHONE = re.compile(r"^\s*0\s+0\s")


def is_invalid_phone(phone: str) -> bool:
    """Reject placeholders, all-zero numbers, and junk low-signal patterns."""
    raw = (phone or "").strip()
    if not raw:
        return True
    if _PLACEHOLDER_PHONE.match(raw):
        return True
    digits = phone_digits(raw)
    if not digits or len(digits) < 6:
        return True
    if not _PHONE_DIGITS.search(digits):
        return True
    if set(digits) <= {"0"}:
        return True
    zero_ratio = digits.count("0") / len(digits)
    if zero_ratio >= 0.6:
        return True
    if not raw.startswith("+") and digits.startswith("00") and len(digits) < 10:
        return True
    return False


def is_title_only_name(name: str) -> bool:
    """True when contact_name is a single title token, not a person."""
    cleaned = _ascii_fold((name or "").strip().lower())
    cleaned = re.sub(r"[^a-z0-9äöåü\s-]", "", cleaned).strip()
    if not cleaned:
        return False
    tokens = [t for t in re.split(r"[\s-]+", cleaned) if t]
    if len(tokens) != 1:
        return False
    token = tokens[0]
    return token in _TITLE_KEYWORDS


def is_generic_contact_name(name: str) -> bool:
    cleaned = _ascii_fold((name or "").strip().lower())
    cleaned = re.sub(r"[^a-z0-9äöå\s-]", "", cleaned).strip()
    return cleaned in _GENERIC_NAMES


def _name_like_tokens(text: str) -> list[str]:
    cleaned = _ascii_fold((text or "").strip().lower())
    cleaned = re.sub(r"[^a-z0-9\s-]", " ", cleaned)
    tokens = [t for t in re.split(r"[\s.-]+", cleaned) if len(t) > 1]
    return [t for t in tokens if t not in _COMPANY_SUFFIXES]


def looks_like_company_contact_name(name: str, company_name: str, company_domain: str = "") -> bool:
    """Reject contact names that are really the company or part of it."""
    name_tokens = _name_like_tokens(name)
    if not name_tokens:
        return False

    company_tokens = set(_name_like_tokens(company_name))
    domain_head = (company_domain or "").strip().lower().split("/")[0].split(":")[0]
    domain_label = domain_head.split(".")[0] if domain_head else ""
    company_tokens.update(_name_like_tokens(domain_label))

    if not company_tokens:
        return False

    return set(name_tokens).issubset(company_tokens)


def _has_verification_fields(rows: list[dict[str, Any]]) -> bool:
    for row in rows:
        if (row.get("phone_verification_status") or "").strip():
            return True
        if (row.get("verified_for_outreach") or "").strip():
            return True
    return False


def passes_verification(row: dict[str, Any]) -> bool:
    if (row.get("verified_for_outreach") or "").strip().lower() == "yes":
        return True
    status = (row.get("phone_verification_status") or "").strip()
    return status in _ALLOWED_VERIFY_STATUSES


def outreach_reject_reason(
    row: dict[str, Any],
    *,
    require_verification: bool = False,
) -> str | None:
    """Return reject reason code, or None if row is outreach-ready."""
    name = (row.get("contact_name") or "").strip()
    phone = (row.get("contact_phone") or "").strip()

    if not name:
        return "empty_contact_name"
    if is_title_only_name(name):
        return "title_only_name"
    if is_generic_contact_name(name):
        return "generic_contact_name"
    if looks_like_company_contact_name(
        name,
        (row.get("company_name") or "").strip(),
        (row.get("company_domain") or "").strip(),
    ):
        return "company_like_contact_name"
    if not phone:
        return "empty_contact_phone"
    if is_invalid_phone(phone):
        return "invalid_phone"
    if require_verification and not passes_verification(row):
        return "verification_failed"
    return None


@dataclass
class FilterResult:
    kept: list[dict[str, Any]] = field(default_factory=list)
    removed: list[dict[str, Any]] = field(default_factory=list)
    reason_counts: dict[str, int] = field(default_factory=dict)

    @property
    def kept_count(self) -> int:
        return len(self.kept)

    @property
    def removed_count(self) -> int:
        return len(self.removed)


def filter_outreach_ready(
    rows: list[dict[str, Any]],
    *,
    require_verification: bool | None = None,
) -> FilterResult:
    """Keep rows that pass outreach ICP criteria; tally reject reasons."""
    if require_verification is None:
        require_verification = _has_verification_fields(rows)

    kept: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    reason_counts: dict[str, int] = {k: 0 for k in REJECT_REASONS}

    for row in rows:
        reason = outreach_reject_reason(row, require_verification=require_verification)
        if reason:
            removed.append(row)
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
        else:
            kept.append(row)

    # Drop zero-count reasons for cleaner reporting.
    reason_counts = {k: v for k, v in reason_counts.items() if v}
    return FilterResult(kept=kept, removed=removed, reason_counts=reason_counts)


def format_filter_report(result: FilterResult, *, total: int | None = None) -> str:
    """Human-readable summary for CLI output."""
    total = total if total is not None else result.kept_count + result.removed_count
    lines = [
        f"Outreach filter: kept {result.kept_count}, removed {result.removed_count} (of {total})",
    ]
    if result.reason_counts:
        lines.append("Removed by reason:")
        for reason in REJECT_REASONS:
            count = result.reason_counts.get(reason)
            if count:
                lines.append(f"  {reason}: {count}")
    return "\n".join(lines)
