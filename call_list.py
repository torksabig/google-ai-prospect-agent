"""Daily call list CSV — outreach-ready leads for phone outreach."""

from __future__ import annotations

import csv
import re
from datetime import date
from pathlib import Path
from typing import Any

from pipeline_filter import filter_outreach_ready, outreach_reject_reason

CALL_LIST_COLUMNS = [
    "date_added",
    "company_name",
    "contact_name",
    "contact_title",
    "contact_phone",
    "phone_type",
    "industry",
    "company_url",
    "contact_brief",
    "verified_for_outreach",
    "phone_verification_status",
    "source_run_id",
]

DEFAULT_CALL_LIST_PATH = Path("output/call_list.csv")


def _normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("358"):
        return digits
    if digits.startswith("0") and len(digits) >= 9:
        return "358" + digits[1:]
    return digits


def dedupe_key(row: dict[str, Any]) -> str:
    """Unique key: phone + company + contact."""
    phone = _normalize_phone(row.get("contact_phone", ""))
    company = (row.get("company_name") or "").strip().lower()
    contact = (row.get("contact_name") or "").strip().lower()
    return f"{phone}|{company}|{contact}"


def row_to_call_list_entry(
    row: dict[str, Any],
    *,
    source_run_id: str = "",
    date_added: str | None = None,
) -> dict[str, str]:
    return {
        "date_added": date_added or date.today().isoformat(),
        "company_name": (row.get("company_name") or "").strip(),
        "contact_name": (row.get("contact_name") or "").strip(),
        "contact_title": (row.get("contact_title") or "").strip(),
        "contact_phone": (row.get("contact_phone") or "").strip(),
        "phone_type": (row.get("phone_type") or "").strip(),
        "industry": (row.get("industry") or "").strip(),
        "company_url": (row.get("company_url") or "").strip(),
        "contact_brief": (row.get("contact_brief") or "").strip(),
        "verified_for_outreach": (row.get("verified_for_outreach") or "").strip(),
        "phone_verification_status": (row.get("phone_verification_status") or "").strip(),
        "source_run_id": source_run_id,
    }


def _load_existing_keys(path: Path) -> set[str]:
    if not path.exists():
        return set()
    with path.open(encoding="utf-8-sig", newline="") as f:
        return {dedupe_key(row) for row in csv.DictReader(f)}


def _write_call_list(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CALL_LIST_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(row)


def append_to_call_list(
    rows: list[dict[str, Any]],
    path: str | Path = DEFAULT_CALL_LIST_PATH,
    *,
    dedupe: bool = True,
    source_run_id: str = "",
    require_verification: bool | None = None,
) -> tuple[int, int]:
    """Append outreach-ready rows. Returns (added, skipped)."""
    target = Path(path)
    qualified = filter_outreach_ready(rows, require_verification=require_verification).kept
    if not qualified:
        return 0, 0

    existing_keys = _load_existing_keys(target) if dedupe and target.exists() else set()
    to_append: list[dict[str, str]] = []
    skipped = 0
    seen_batch: set[str] = set()

    for row in qualified:
        entry = row_to_call_list_entry(row, source_run_id=source_run_id)
        key = dedupe_key(entry)
        if dedupe and (key in existing_keys or key in seen_batch):
            skipped += 1
            continue
        seen_batch.add(key)
        existing_keys.add(key)
        to_append.append(entry)

    if not to_append and not target.exists():
        return 0, skipped

    target.parent.mkdir(parents=True, exist_ok=True)
    write_header = not target.exists()
    with target.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CALL_LIST_COLUMNS, extrasaction="ignore")
        if write_header:
            w.writeheader()
        for row in to_append:
            w.writerow(row)

    return len(to_append), skipped


def rebuild_call_list(
    rows: list[dict[str, Any]],
    path: str | Path = DEFAULT_CALL_LIST_PATH,
    *,
    source_run_id: str = "",
    require_verification: bool | None = None,
) -> tuple[int, int]:
    """Overwrite call list with outreach-ready rows from source (deduped)."""
    qualified = filter_outreach_ready(rows, require_verification=require_verification).kept
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    skipped = 0
    for row in qualified:
        entry = row_to_call_list_entry(row, source_run_id=source_run_id)
        key = dedupe_key(entry)
        if key in seen:
            skipped += 1
            continue
        seen.add(key)
        out.append(entry)
    _write_call_list(Path(path), out)
    return len(out), skipped


def qualified_rows_from_source(
    rows: list[dict[str, Any]],
    *,
    require_verification: bool | None = None,
) -> list[dict[str, Any]]:
    """Return outreach-ready rows; useful for reporting reject reasons."""
    return filter_outreach_ready(rows, require_verification=require_verification).kept


def reject_reason(row: dict[str, Any], *, require_verification: bool = False) -> str | None:
    return outreach_reject_reason(row, require_verification=require_verification)
