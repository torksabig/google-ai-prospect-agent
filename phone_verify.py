"""Second-pass phone verification — rule-based, no API required.

Statuses:
  dial_confirmed              manual / field override
  person_page_match           name + phone tied on person/team page
  company_switchboard_only    main line only
  no_phone_found              missing number
  role_verified_phone_unverified  contact role OK, phone not person-tied
  direct_claim_unverified     mobile/direct claimed but weak page link
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

PHONE_STATUSES = (
    "dial_confirmed",
    "person_page_match",
    "company_switchboard_only",
    "no_phone_found",
    "role_verified_phone_unverified",
    "direct_claim_unverified",
)

_OUTREACH_OK = frozenset({"dial_confirmed", "person_page_match"})

_PERSON_PAGE_HINTS = re.compile(
    r"(contact|team|management|johto|hallitus|ir-contact|ir_contacts|executive|"
    r"leadership|people|yhteystiedot|ota-yhteytta|about-us|sijoittajat)",
    re.I,
)
_PHONE_DIGITS = re.compile(r"\d{6,}")
_SWITCHBOARD_HINTS = re.compile(r"(switchboard|vaihde|main line|päänumero|asiakaspalvelu)", re.I)


def _ascii_fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def _name_tokens(name: str) -> list[str]:
    raw = _ascii_fold((name or "").lower())
    raw = re.sub(r"[^a-z\s-]", " ", raw)
    parts = [p for p in re.split(r"[\s-]+", raw) if len(p) > 2]
    # drop common titles
    skip = {"dr", "mr", "mrs", "ms", "ceo", "cto", "cfo"}
    return [p for p in parts if p not in skip]


def _urls_from_row(row: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    src = (row.get("phone_source_url") or "").strip()
    if src:
        urls.extend(u.strip() for u in src.split(",") if u.strip())
    ev = row.get("evidence_urls")
    if isinstance(ev, list):
        urls.extend(str(u).strip() for u in ev if u)
    elif ev:
        urls.extend(u.strip() for u in str(ev).split(";") if u.strip())
    return list(dict.fromkeys(urls))


def _url_mentions_name(url: str, name: str) -> bool:
    if not url or not name:
        return False
    u = _ascii_fold(url.lower())
    tokens = _name_tokens(name)
    if not tokens:
        return False
    # surname match is enough on person pages
    if tokens[-1] in u:
        return True
    return sum(1 for t in tokens if t in u) >= min(2, len(tokens))


def _looks_like_person_page(url: str) -> bool:
    return bool(url and _PERSON_PAGE_HINTS.search(url))


def _is_switchboard_row(row: dict[str, Any], phone_type: str) -> bool:
    if phone_type == "switchboard":
        return True
    brief = (row.get("contact_brief") or "") + " " + (row.get("phone_verification_reason") or "")
    return bool(_SWITCHBOARD_HINTS.search(brief))


def verified_for_outreach_status(status: str) -> bool:
    return status in _OUTREACH_OK


def verify_phone_row(row: dict[str, Any]) -> dict[str, Any]:
    """Return verification fields to merge into prospect row."""
    if (row.get("phone_verification_status") or "").strip() == "dial_confirmed":
        return {
            "phone_verification_status": "dial_confirmed",
            "phone_verification_reason": (row.get("phone_verification_reason") or "Manual dial confirmed").strip(),
            "phone_owner_match": "yes",
            "phone_page_match": "yes",
            "verified_for_outreach": "yes",
        }

    phone = (row.get("contact_phone") or "").strip()
    phone_type = (row.get("phone_type") or "unknown").strip().lower()
    contact_name = (row.get("contact_name") or "").strip()
    confidence = float(row.get("confidence") or 0)
    urls = _urls_from_row(row)

    if not phone or not _PHONE_DIGITS.search(phone.replace(" ", "")):
        return {
            "phone_verification_status": "no_phone_found",
            "phone_verification_reason": "No phone number on row",
            "phone_owner_match": "no",
            "phone_page_match": "no",
            "verified_for_outreach": "no",
        }

    if _is_switchboard_row(row, phone_type):
        return {
            "phone_verification_status": "company_switchboard_only",
            "phone_verification_reason": "Switchboard or main company line — not direct to named contact",
            "phone_owner_match": "no",
            "phone_page_match": "no",
            "verified_for_outreach": "no",
        }

    name_on_url = any(_url_mentions_name(u, contact_name) for u in urls)
    person_pages = [u for u in urls if _looks_like_person_page(u)]
    person_page_named = any(_looks_like_person_page(u) and _url_mentions_name(u, contact_name) for u in urls)

    if person_page_named or (name_on_url and phone_type in {"mobile", "direct"}):
        src = urls[0] if urls else "evidence"
        return {
            "phone_verification_status": "person_page_match",
            "phone_verification_reason": f"Contact name appears on phone source page ({src})",
            "phone_owner_match": "yes",
            "phone_page_match": "yes",
            "verified_for_outreach": "yes",
        }

    if phone_type == "mobile" and person_pages and confidence >= 0.9 and contact_name:
        return {
            "phone_verification_status": "person_page_match",
            "phone_verification_reason": f"Mobile on official contact page ({person_pages[0]})",
            "phone_owner_match": "yes",
            "phone_page_match": "yes",
            "verified_for_outreach": "yes",
        }

    if person_pages and phone_type == "mobile":
        return {
            "phone_verification_status": "direct_claim_unverified",
            "phone_verification_reason": "Mobile on team/contact page but weak name match on URL",
            "phone_owner_match": "no",
            "phone_page_match": "partial",
            "verified_for_outreach": "no",
        }

    if phone_type in {"mobile", "direct"}:
        if urls:
            return {
                "phone_verification_status": "direct_claim_unverified",
                "phone_verification_reason": "Direct/mobile listed but not tied to named person page",
                "phone_owner_match": "no",
                "phone_page_match": "no",
                "verified_for_outreach": "no",
            }
        return {
            "phone_verification_status": "direct_claim_unverified",
            "phone_verification_reason": "Direct/mobile without phone_source_url",
            "phone_owner_match": "no",
            "phone_page_match": "no",
            "verified_for_outreach": "no",
        }

    if contact_name and confidence >= 0.6:
        return {
            "phone_verification_status": "role_verified_phone_unverified",
            "phone_verification_reason": "Contact role confidence OK; phone not verified for this person",
            "phone_owner_match": "no",
            "phone_page_match": "no",
            "verified_for_outreach": "no",
        }

    return {
        "phone_verification_status": "direct_claim_unverified",
        "phone_verification_reason": "Could not tie phone to named contact",
        "phone_owner_match": "no",
        "phone_page_match": "no",
        "verified_for_outreach": "no",
    }


def verify_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        merged = dict(row)
        merged.update(verify_phone_row(row))
        out.append(merged)
    return out
