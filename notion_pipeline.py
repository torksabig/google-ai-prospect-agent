"""Export verified prospects to organized pipeline CSV + Notion database."""

from __future__ import annotations

import csv
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)

NOTION_VERSION = "2022-06-28"

# CRM export columns (tab-separated paste format matching Notion Pipeline DB)
CRM_FIELDNAMES = [
    "name",
    "account_owner",
    "deal_stage",
    "email",
    "phone_number",
    "notes",
    "date_added",
    "lead_source",
]

# Legacy two-column company export (kept for normalize_sync_rows fallback)
COMPANY_FIELDNAMES = ["company_name", "description"]

DEFAULT_ACCOUNT_OWNER = "Teodor Pavel-Hiidenlampi"
DEFAULT_LEAD_SOURCE = "Outbound"
DEFAULT_DEAL_STAGE = "New"

# Full contact-level detail for internal review
PIPELINE_FIELDNAMES = [
    "lead_key",
    "company_name",
    "company_domain",
    "industry",
    "estimated_revenue",
    "employee_count",
    "country",
    "contact_name",
    "contact_title",
    "contact_email",
    "contact_phone",
    "phone_type",
    "phone_verification_status",
    "verified_for_outreach",
    "company_url",
    "phone_source_url",
    "contact_brief",
    "confidence",
    "pipeline_stage",
    "source",
    "account_owner",
    "notes",
    "date_added",
    "lead_source",
    "synced_at",
]

# Notion DB property names — full template (see NOTION_PIPELINE.md)
NOTION_PROPS = {
    "lead_key": "Lead Key",
    "name": "Name",
    "company": "Company",
    "contact_name": "Contact Name",
    "title": "Title",
    "phone": "Phone",
    "industry": "Industry",
    "revenue": "Revenue",
    "country": "Country",
    "website": "Website",
    "brief": "Brief",
    "description": "Brief",
    "stage": "Stage",
    "source": "Source",
    "outreach_ok": "Outreach OK",
    "phone_status": "Phone Status",
}

PROPERTY_MAP_PATH = Path(__file__).resolve().parent / "notion_property_map.json"
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")


def load_property_map() -> dict[str, Any]:
    pmap: dict[str, Any] = {
        "properties": dict(NOTION_PROPS),
        "explicit_keys": set(NOTION_PROPS.keys()),
        "embed_in_notes": [],
        "lead_key_notes_prefix": "[lk:{value}]",
        "lead_key_query": "lead_key",
        "stage_value_map": {},
        "source_value_map": {},
        "type_overrides": {},
    }
    if PROPERTY_MAP_PATH.exists():
        data = json.loads(PROPERTY_MAP_PATH.read_text(encoding="utf-8"))
        explicit: set[str] = set()
        if isinstance(data.get("properties"), dict):
            for key, col in data["properties"].items():
                if col:
                    pmap["properties"][key] = col
                    explicit.add(key)
        pmap["explicit_keys"] = explicit or pmap["explicit_keys"]
        for key in (
            "embed_in_notes",
            "lead_key_notes_prefix",
            "lead_key_query",
            "stage_value_map",
            "source_value_map",
            "type_overrides",
        ):
            if key in data:
                pmap[key] = data[key]
    return pmap


def _prop_col(key: str, pmap: dict[str, Any]) -> str | None:
    if key not in (pmap.get("explicit_keys") or set()):
        return None
    return (pmap.get("properties") or {}).get(key)


def _pipeline_field(pr: dict[str, str], field: str) -> str:
    if field == "company":
        return (pr.get("company_name") or "").strip()
    if field == "phone_status":
        return (pr.get("phone_verification_status") or "").strip()
    if field == "outreach_ok":
        return (pr.get("verified_for_outreach") or "").strip()
    return (pr.get(field) or "").strip()


def _description_text(pr: dict[str, str]) -> str:
    return (
        (pr.get("description") or "").strip()
        or (pr.get("contact_brief") or "").strip()
    )


def _build_notes_body(pr: dict[str, str], pmap: dict[str, Any]) -> str:
    """Company description for Notion Notes/Brief, with optional lead_key prefix."""
    desc = _description_text(pr)
    key = pr.get("lead_key", "")
    prefix_tpl = pmap.get("lead_key_notes_prefix") or "[lk:{value}]"
    embed = pmap.get("embed_in_notes") or []
    if not embed and desc:
        if key:
            marker = prefix_tpl.format(value=key)
            return f"{marker}\n{desc}"[:2000]
        return desc[:2000]
    lines: list[str] = []
    if key:
        lines.append(prefix_tpl.format(value=key))
    for field in embed:
        if field == "lead_key":
            continue
        val = _pipeline_field(pr, field)
        if val:
            lines.append(f"{field}: {val}")
    if desc:
        lines.append(desc)
    return "\n".join(lines)[:2000]


def _parse_revenue_number(text: str) -> float | None:
    cleaned = (text or "").replace("EUR", "").replace("€", "").strip()
    if not cleaned:
        return None
    for token in cleaned.replace(",", "").split():
        try:
            return float(token)
        except ValueError:
            continue
    return None


def _domain_key(row: dict[str, Any]) -> str:
    d = (row.get("company_domain") or "").strip().lower()
    d = d.replace("https://", "").replace("http://", "").split("/")[0]
    if d.startswith("www."):
        d = d[4:]
    if d:
        return d
    return (row.get("company_name") or "").strip().lower()


def company_lead_key(row: dict[str, Any]) -> str:
    """Dedup key: company (domain preferred, else company name)."""
    return _domain_key(row)


def contact_lead_key(row: dict[str, Any]) -> str:
    """Dedup key: one row per contact at a company."""
    company = company_lead_key(row)
    contact = (row.get("contact_name") or "").strip().lower()
    phone = (row.get("contact_phone") or "").strip()
    if contact:
        return f"{company}|{contact}"
    if phone:
        return f"{company}|{phone}"
    return company


def lead_key(row: dict[str, Any]) -> str:
    return contact_lead_key(row)


def _account_owner_name() -> str:
    return (os.environ.get("NOTION_ACCOUNT_OWNER") or DEFAULT_ACCOUNT_OWNER).strip()


def _owner_user_id() -> str:
    return (os.environ.get("NOTION_OWNER_USER_ID") or "").strip()


def _format_date_added(dt: datetime | None = None) -> str:
    """Human-readable date like 'June 29, 2026'."""
    dt = dt or datetime.now(timezone.utc)
    return f"{dt.strftime('%B')} {dt.day}, {dt.year}"


def _date_added_iso(dt: datetime | None = None) -> str:
    dt = dt or datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%d")


def _clean_domain(row: dict[str, Any]) -> str:
    domain = (row.get("company_domain") or row.get("company_url") or "").strip()
    domain = domain.replace("https://", "").replace("http://", "").split("/")[0]
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def _company_genitive(company: str) -> str:
    company = company.strip()
    if not company:
        return "yrityksen"
    return f"{company}:n"


def _phone_context_sentence(row: dict[str, Any]) -> str:
    phone = (row.get("contact_phone") or "").strip()
    if not phone:
        return ""
    phone_type = (row.get("phone_type") or "unknown").strip().lower()
    source = (row.get("phone_source_url") or "").strip()
    if phone_type == "mobile":
        label = "mobiilinumeronsa"
    elif phone_type == "direct":
        label = "suora numeronsa"
    else:
        label = "puhelinnumeronsa"
    if source:
        return f"Hänen {label} on julkisesti saatavilla lähdesivulla."
    return f"Hänen {label} on mukana prospektidatassa."


def _role_context_sentence(title: str) -> str:
    lower = title.lower()
    if lower in {"ceo", "cto", "coo", "cfo", "cio"}:
        role = f"{title}:na"
    elif "," in title or " and " in lower or " / " in title:
        return (
            "Tässä roolissa hän on relevantti yhteyshenkilö teknologiaan, kehitykseen "
            "tai operatiivisiin ratkaisuihin liittyvissä keskusteluissa."
        )
    elif lower.endswith(("manager", "director", "officer", "engineer")):
        role = f"{title}ina"
    else:
        role = f"{title}na"
    return (
        f"{role} hänellä on keskeinen rooli teknologiaan, kehitykseen tai "
        "operatiivisiin ratkaisuihin liittyvissä keskusteluissa."
    )


def _synthesize_narrative(row: dict[str, Any]) -> str:
    """Fallback Finnish narrative when contact_brief is empty."""
    name = (row.get("contact_name") or "").strip()
    title = (row.get("contact_title") or "").strip()
    company = (row.get("company_name") or "").strip()
    industry = (row.get("industry") or "").strip()
    sentences: list[str] = []
    if name and company and title:
        sentences.append(f"{name} on {_company_genitive(company)} {title}.")
    elif name and title:
        sentences.append(f"{name} toimii roolissa {title}.")
    elif name and company:
        sentences.append(f"{name} on tunnistettu yhteyshenkilö yrityksessä {company}.")
    if company and industry:
        sentences.append(f"Yritys toimii {industry}-alalla.")
    elif company:
        sentences.append(
            "Yritys sopii haun kohdeyrityslistalle saatavilla olevan lähdedatan perusteella."
        )
    if title:
        sentences.append(_role_context_sentence(title))
    phone_sentence = _phone_context_sentence(row)
    if phone_sentence:
        sentences.append(phone_sentence)
    return " ".join(sentences).strip()


def _prospect_website(row: dict[str, Any]) -> str:
    url = (row.get("company_url") or row.get("company_domain") or "").strip()
    if url and not url.startswith("http"):
        url = f"https://{url}"
    return url or "—"


def _build_prospect_footer(row: dict[str, Any]) -> str:
    """Structured contact block appended after narrative."""
    name = (row.get("contact_name") or "—").strip()
    title = (row.get("contact_title") or "—").strip()
    phone = (row.get("contact_phone") or "—").strip()
    phone_type = (row.get("phone_type") or "unknown").strip()
    lines = [
        f"**{name}** — {title}",
        "",
        f"Website: {_prospect_website(row)}",
        "",
        f"Puhelin: {phone} ({phone_type})",
    ]
    vstat = (row.get("phone_verification_status") or "").strip()
    if vstat:
        reason = (row.get("phone_verification_reason") or "").strip()
        verify = f"Verify: {vstat}"
        if reason:
            verify += f" — {reason}"
        lines.append("")
        lines.append(verify)
    return "\n".join(lines)


def build_prospect_notes(row: dict[str, Any]) -> str:
    """User-visible notes: contact_brief narrative + structured footer."""
    narrative = (row.get("contact_brief") or "").strip()
    if not narrative:
        narrative = _synthesize_narrative(row)
    footer = _build_prospect_footer(row)
    if narrative:
        return f"{narrative}\n\n{footer}"
    return footer


def _lead_key_marker(key: str, pmap: dict[str, Any] | None = None) -> str:
    if not key:
        return ""
    pmap = pmap or load_property_map()
    prefix_tpl = pmap.get("lead_key_notes_prefix") or "[lk:{value}]"
    return prefix_tpl.format(value=key)


def _strip_lead_key_marker(text: str) -> str:
    """Remove hidden dedup marker from user-visible notes."""
    if not text:
        return ""
    cleaned = re.sub(r"\s*\[lk:[^\]]+\]\s*", " ", text).strip()
    return re.sub(r"\n\s*\[lk:[^\]]+\]\s*\n?", "\n", cleaned).strip()


def _notes_for_notion(display: str, key: str, pmap: dict[str, Any] | None = None) -> str:
    """Append hidden [lk:...] at end of Notes for Notion dedup."""
    display = _strip_lead_key_marker(display)
    marker = _lead_key_marker(key, pmap)
    if marker:
        combined = f"{display} {marker}".strip() if display else marker
        return combined[:2000]
    return display[:2000]


def _extract_email(row: dict[str, Any]) -> str:
    email = (row.get("contact_email") or row.get("email") or "").strip()
    if email:
        return email
    for field in ("contact_brief", "evidence_urls"):
        text = (row.get(field) or "").strip()
        match = EMAIL_RE.search(text)
        if match:
            return match.group(0)
    return ""


def _has_meaningful_contact(row: dict[str, Any]) -> bool:
    return bool((row.get("contact_name") or "").strip())


def _row_confidence(row: dict[str, Any]) -> float:
    try:
        return float(row.get("confidence") or 0)
    except (TypeError, ValueError):
        return 0.0


def _synthesize_description(row: dict[str, Any]) -> str:
    """Best available blurb for row ranking: contact_brief or narrative fallback."""
    brief = (row.get("contact_brief") or "").strip()
    if brief:
        return brief
    return _synthesize_narrative(row)


def _pick_best_row(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Prefer row with contact_brief, then highest confidence."""
    with_brief = [r for r in rows if (r.get("contact_brief") or "").strip()]
    pool = with_brief or rows
    return max(pool, key=lambda r: (_row_confidence(r), len(_synthesize_description(r))))


def pipeline_row(
    row: dict[str, Any],
    *,
    source: str = DEFAULT_LEAD_SOURCE,
    sync_dt: datetime | None = None,
) -> dict[str, str]:
    company = (row.get("company_name") or "").strip()
    sync_dt = sync_dt or datetime.now(timezone.utc)
    pmap = load_property_map()
    lk = contact_lead_key(row)
    notes_display = build_prospect_notes(row)[:2000]
    notes_notion = _notes_for_notion(notes_display, lk, pmap)
    return {
        "lead_key": lk,
        "company_name": company,
        "description": notes_display,
        "notes": notes_display,
        "notes_notion": notes_notion,
        "company_domain": _clean_domain(row),
        "industry": (row.get("industry") or "").strip(),
        "estimated_revenue": (row.get("estimated_revenue") or "").strip(),
        "employee_count": (row.get("employee_count") or "").strip(),
        "country": (row.get("country") or "Finland").strip(),
        "contact_name": (row.get("contact_name") or "").strip(),
        "contact_title": (row.get("contact_title") or "").strip(),
        "contact_email": _extract_email(row),
        "contact_phone": (row.get("contact_phone") or "").strip(),
        "phone_type": (row.get("phone_type") or "").strip(),
        "phone_verification_status": (row.get("phone_verification_status") or "").strip(),
        "phone_verification_reason": (row.get("phone_verification_reason") or "").strip(),
        "verified_for_outreach": (row.get("verified_for_outreach") or "").strip(),
        "company_url": (row.get("company_url") or row.get("company_domain") or "").strip(),
        "phone_source_url": (row.get("phone_source_url") or "").strip(),
        "contact_brief": (row.get("contact_brief") or "").strip(),
        "confidence": str(row.get("confidence") or ""),
        "pipeline_stage": DEFAULT_DEAL_STAGE,
        "source": source,
        "account_owner": _account_owner_name(),
        "date_added": _format_date_added(sync_dt),
        "date_added_iso": _date_added_iso(sync_dt),
        "lead_source": DEFAULT_LEAD_SOURCE,
        "synced_at": sync_dt.strftime("%Y-%m-%d %H:%M UTC"),
    }


def _group_by_company(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = company_lead_key(row)
        if not key:
            continue
        groups.setdefault(key, []).append(row)
    return groups


def rows_to_pipeline(
    rows: list[dict[str, Any]], *, source: str = DEFAULT_LEAD_SOURCE
) -> tuple[list[dict[str, str]], int]:
    """One pipeline row per contact (skips rows without contact_name)."""
    sync_dt = datetime.now(timezone.utc)
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    skipped_no_contact = 0
    for row in rows:
        if not _has_meaningful_contact(row):
            skipped_no_contact += 1
            continue
        key = contact_lead_key(row)
        if key in seen:
            continue
        seen.add(key)
        out.append(pipeline_row(row, source=source, sync_dt=sync_dt))
    out.sort(key=lambda r: ((r.get("company_name") or "").lower(), (r.get("contact_name") or "").lower()))
    return out, skipped_no_contact


def pipeline_to_crm_row(pr: dict[str, str]) -> dict[str, str]:
    notes = _strip_lead_key_marker(pr.get("notes", "") or pr.get("description", ""))
    return {
        "name": pr.get("company_name", ""),
        "account_owner": pr.get("account_owner", _account_owner_name()),
        "deal_stage": pr.get("pipeline_stage", DEFAULT_DEAL_STAGE),
        "email": pr.get("contact_email", ""),
        "phone_number": pr.get("contact_phone", ""),
        "notes": notes,
        "date_added": pr.get("date_added", _format_date_added()),
        "lead_source": pr.get("lead_source", DEFAULT_LEAD_SOURCE),
    }


def rows_to_company_export(
    pipeline: list[dict[str, str]],
) -> list[dict[str, str]]:
    return [
        {
            "company_name": pr.get("company_name", ""),
            "description": pr.get("description", "") or pr.get("contact_brief", ""),
        }
        for pr in pipeline
        if (pr.get("company_name") or "").strip()
    ]


def write_contacts_only_csv(rows: list[dict[str, Any]], path: Path) -> int:
    """Write input rows that have contact_name to a review CSV."""
    filtered = [r for r in rows if _has_meaningful_contact(r)]
    if not filtered:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(filtered[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for row in filtered:
            w.writerow(row)
    return len(filtered)


def write_pipeline_csv(
    rows: list[dict[str, Any]],
    path: Path,
    *,
    source: str = DEFAULT_LEAD_SOURCE,
) -> tuple[Path, int, int]:
    """Write CRM-format pipeline CSV/TSV plus detailed contact CSV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    pipeline, skipped_no_contact = rows_to_pipeline(rows, source=source)
    crm_rows = [pipeline_to_crm_row(pr) for pr in pipeline]

    company_path = path.parent / "notion_pipeline.csv"
    with company_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CRM_FIELDNAMES, extrasaction="ignore")
        w.writeheader()
        for row in crm_rows:
            w.writerow(row)

    tsv_path = path.parent / "notion_pipeline.tsv"
    with tsv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        for row in crm_rows:
            w.writerow([row.get(col, "") for col in CRM_FIELDNAMES])

    # Legacy alias
    legacy_tsv = path.parent / "pipeline.tsv"
    legacy_tsv.write_text(tsv_path.read_text(encoding="utf-8"), encoding="utf-8")

    detailed_path = path.parent / "notion_pipeline_detailed.csv"
    with detailed_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=PIPELINE_FIELDNAMES, extrasaction="ignore")
        w.writeheader()
        for pr in pipeline:
            w.writerow({k: pr.get(k, "") for k in PIPELINE_FIELDNAMES})

    if path != company_path:
        path.write_text(company_path.read_text(encoding="utf-8"), encoding="utf-8")

    log.info(
        "Pipeline: %d exported, %d skipped (no contact) → %s, %s, %s",
        len(crm_rows),
        skipped_no_contact,
        company_path,
        tsv_path,
        detailed_path,
    )
    return company_path, len(crm_rows), skipped_no_contact


def _notion_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _rt(text: str) -> dict[str, Any]:
    if not text:
        return {"rich_text": []}
    return {"rich_text": [{"type": "text", "text": {"content": text[:2000]}}]}


def _page_props(pr: dict[str, str], pmap: dict[str, Any] | None = None) -> dict[str, Any]:
    if pmap is None:
        pmap = load_property_map()
    stage_value_map = pmap.get("stage_value_map") or {}
    source_value_map = pmap.get("source_value_map") or {}
    type_overrides = pmap.get("type_overrides") or {}

    company = (pr.get("company_name") or pr.get("name") or "").strip() or "Unknown"
    phone = pr.get("contact_phone", "") or pr.get("phone_number", "")
    email = (pr.get("contact_email") or pr.get("email") or "").strip()
    url = pr.get("company_url", "")
    if url and not url.startswith("http"):
        url = f"https://{url}"

    props: dict[str, Any] = {}

    def set_rt(key: str, text: str) -> None:
        col = _prop_col(key, pmap)
        if col and text:
            props[col] = _rt(text)

    name_col = _prop_col("name", pmap)
    if name_col:
        props[name_col] = {
            "title": [{"type": "text", "text": {"content": company[:2000]}}]
        }

    desc_col = _prop_col("description", pmap) or _prop_col("brief", pmap)
    display = _strip_lead_key_marker(
        pr.get("notes", "") or pr.get("description", "") or ""
    )
    if not display:
        display = build_prospect_notes(pr)[:2000]
    key = pr.get("lead_key", "")
    notes_body = pr.get("notes_notion") or _notes_for_notion(display, key, pmap)
    if desc_col and notes_body:
        props[desc_col] = _rt(notes_body)

    stage_col = _prop_col("stage", pmap)
    if stage_col:
        raw_stage = pr.get("pipeline_stage") or pr.get("deal_stage") or DEFAULT_DEAL_STAGE
        stage_name = stage_value_map.get(raw_stage, raw_stage)
        props[stage_col] = {"select": {"name": stage_name}}

    source_col = _prop_col("source", pmap)
    if source_col:
        raw_source = pr.get("lead_source") or pr.get("source") or DEFAULT_LEAD_SOURCE
        source_name = source_value_map.get(raw_source, raw_source)
        if type_overrides.get("source") == "multi_select":
            props[source_col] = {"multi_select": [{"name": source_name}]}
        else:
            props[source_col] = {"select": {"name": source_name}}

    owner_col = _prop_col("account_owner", pmap)
    owner_id = _owner_user_id()
    if owner_col and owner_id:
        props[owner_col] = {"people": [{"id": owner_id}]}

    email_col = _prop_col("email", pmap)
    if email_col and email:
        props[email_col] = {"email": email}

    date_col = _prop_col("date_added", pmap)
    if date_col:
        iso = (pr.get("date_added_iso") or "").strip()
        if not iso and pr.get("date_added"):
            try:
                parsed = datetime.strptime(pr["date_added"], "%B %d, %Y")
                iso = parsed.strftime("%Y-%m-%d")
            except ValueError:
                iso = _date_added_iso()
        if not iso:
            iso = _date_added_iso()
        props[date_col] = {"date": {"start": iso}}

    phone_col = _prop_col("phone", pmap)
    if phone_col and phone:
        props[phone_col] = {"phone_number": phone}

    website_col = _prop_col("website", pmap)
    if website_col and url.startswith("http"):
        props[website_col] = {"url": url}

    return props


def _query_by_lead_key(
    client: httpx.Client,
    *,
    token: str,
    database_id: str,
    key: str,
    pmap: dict[str, Any] | None = None,
) -> str | None:
    if pmap is None:
        pmap = load_property_map()

    query_key = pmap.get("lead_key_query") or "lead_key"
    lead_key_col = _prop_col(query_key, pmap)
    prefix_tpl = pmap.get("lead_key_notes_prefix") or "[lk:{value}]"
    marker = prefix_tpl.format(value=key)

    if query_key == "lead_key" and lead_key_col:
        body = {
            "filter": {
                "property": lead_key_col,
                "rich_text": {"equals": key},
            },
            "page_size": 1,
        }
    elif lead_key_col:
        body = {
            "filter": {
                "property": lead_key_col,
                "rich_text": {"contains": marker},
            },
            "page_size": 1,
        }
    else:
        return None

    r = client.post(
        f"https://api.notion.com/v1/databases/{database_id}/query",
        headers=_notion_headers(token),
        json=body,
    )
    if r.status_code == 429:
        time.sleep(2)
        return _query_by_lead_key(
            client, token=token, database_id=database_id, key=key, pmap=pmap
        )
    r.raise_for_status()
    results = r.json().get("results") or []
    if results:
        return results[0]["id"]
    return None


def sync_pipeline_to_notion(
    pipeline_rows: list[dict[str, str]],
    *,
    database_id: str,
    token: str,
    state_path: Path | None = None,
) -> dict[str, int]:
    """Upsert pipeline rows into Notion. Returns counts."""
    if not token or not database_id:
        raise ValueError("NOTION_API_KEY and NOTION_DATABASE_ID required")

    state: dict[str, str] = {}
    if state_path and state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))

    created = updated = skipped = skipped_no_contact = errors = 0
    pmap = load_property_map()

    with httpx.Client(timeout=30.0) as client:
        for pr in pipeline_rows:
            if not (pr.get("contact_name") or "").strip():
                skipped_no_contact += 1
                continue
            key = pr.get("lead_key", "")
            if not key:
                skipped += 1
                continue
            try:
                page_id = state.get(key) or _query_by_lead_key(
                    client,
                    token=token,
                    database_id=database_id,
                    key=key,
                    pmap=pmap,
                )
                props = _page_props(pr, pmap)
                if page_id:
                    r = client.patch(
                        f"https://api.notion.com/v1/pages/{page_id}",
                        headers=_notion_headers(token),
                        json={"properties": props},
                    )
                    if r.status_code == 200:
                        updated += 1
                        state[key] = page_id
                    else:
                        log.warning("Notion update %s: %s", key, r.text[:200])
                        errors += 1
                else:
                    r = client.post(
                        "https://api.notion.com/v1/pages",
                        headers=_notion_headers(token),
                        json={
                            "parent": {"database_id": database_id},
                            "properties": props,
                        },
                    )
                    if r.status_code == 200:
                        created += 1
                        state[key] = r.json()["id"]
                    else:
                        log.warning("Notion create %s: %s", key, r.text[:200])
                        errors += 1
                time.sleep(0.35)  # ~3 req/s — under Notion rate limits
            except Exception as exc:
                log.warning("Notion sync failed for %s: %s", key, exc)
                errors += 1

    if state_path:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    if skipped_no_contact:
        log.info("Notion sync skipped %d rows without contact person", skipped_no_contact)
    return {
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "skipped_no_contact": skipped_no_contact,
        "errors": errors,
    }


def normalize_sync_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Accept CRM export, detailed pipeline CSV, or legacy company CSV for Notion sync."""
    out: list[dict[str, str]] = []
    for row in rows:
        pr = dict(row)
        # CRM export columns → internal keys
        if (pr.get("name") or "").strip() and not (pr.get("company_name") or "").strip():
            pr["company_name"] = pr["name"].strip()
        if (pr.get("deal_stage") or "").strip() and not (pr.get("pipeline_stage") or "").strip():
            pr["pipeline_stage"] = pr["deal_stage"].strip()
        if (pr.get("email") or "").strip() and not (pr.get("contact_email") or "").strip():
            pr["contact_email"] = pr["email"].strip()
        if (pr.get("phone_number") or "").strip() and not (pr.get("contact_phone") or "").strip():
            pr["contact_phone"] = pr["phone_number"].strip()
        if (pr.get("notes") or "").strip() and not (pr.get("description") or "").strip():
            pr["description"] = pr["notes"].strip()
        if (pr.get("lead_source") or "").strip() and not (pr.get("source") or "").strip():
            pr["source"] = pr["lead_source"].strip()

        company = (pr.get("company_name") or "").strip()
        if not company:
            continue

        notes = _strip_lead_key_marker((pr.get("notes") or pr.get("description") or "").strip())
        if notes and not (pr.get("contact_name") or "").strip():
            for line in notes.splitlines():
                line = line.strip()
                if line.startswith("**") and " — " in line:
                    segment = line.strip("*").strip()
                    contact, title = segment.split(" — ", 1)
                    pr["contact_name"] = contact.strip()
                    pr["contact_title"] = title.strip()
                    break
            if not (pr.get("contact_name") or "").strip():
                marker = "Key contact: "
                if marker in notes:
                    segment = notes.split(marker, 1)[1].split("\n")[0].split(";")[0].strip()
                    if " — " in segment:
                        contact, title = segment.split(" — ", 1)
                        pr["contact_name"] = contact.strip()
                        pr["contact_title"] = title.strip()
                    else:
                        pr["contact_name"] = segment

        if not (pr.get("lead_key") or "").strip():
            pr["lead_key"] = contact_lead_key(pr)
        pmap = load_property_map()
        if not notes or "Website:" not in notes:
            notes = build_prospect_notes(pr)[:2000]
        pr["notes"] = notes
        pr["description"] = notes
        pr["notes_notion"] = _notes_for_notion(notes, pr["lead_key"], pmap)
        if not (pr.get("contact_email") or "").strip():
            pr["contact_email"] = _extract_email(pr)
        if not (pr.get("pipeline_stage") or "").strip():
            pr["pipeline_stage"] = DEFAULT_DEAL_STAGE
        if not (pr.get("lead_source") or "").strip():
            pr["lead_source"] = DEFAULT_LEAD_SOURCE
        if not (pr.get("account_owner") or "").strip():
            pr["account_owner"] = _account_owner_name()
        if not (pr.get("date_added") or "").strip():
            pr["date_added"] = _format_date_added()
        if not (pr.get("date_added_iso") or "").strip():
            pr["date_added_iso"] = _date_added_iso()
        if not (pr.get("contact_name") or "").strip():
            continue
        out.append(pr)
    return out


def load_notion_config() -> tuple[str, str]:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env")
    token = os.environ.get("NOTION_API_KEY", "").strip()
    db_id = os.environ.get("NOTION_DATABASE_ID", "").strip()
    return token, db_id


def load_notion_config_full() -> tuple[str, str, str, str]:
    """token, database_id, account_owner, owner_user_id"""
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env")
    token = os.environ.get("NOTION_API_KEY", "").strip()
    db_id = os.environ.get("NOTION_DATABASE_ID", "").strip()
    return token, db_id, _account_owner_name(), _owner_user_id()
