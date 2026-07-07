"""Free Finnish company discovery via PRH/YTJ open API (no LLM, no API key)."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)

API = "https://avoindata.prh.fi/opendata-ytj-api/v3/companies"

# TOL 2008 — industrial / tech segments relevant to Aether ICP
INDUSTRIAL_BUSINESS_LINES = (
    "27120",  # electric motors, generators
    "28222",  # lifting and handling equipment
    "28230",  # office machinery
    "28240",  # power-driven hand tools
    "28250",  # non-domestic cooling and ventilation
    "28290",  # other general-purpose machinery
    "28300",  # agricultural and forestry machinery
    "28990",  # other special-purpose machinery
    "33120",  # repair of machinery
    "62010",  # computer programming
    "62020",  # computer consultancy
    "71121",  # engineering design for industrial processes
)


def _text_value(value: Any, key: str = "value") -> str:
    """Return a clean string from PRH scalar-or-object fields."""
    if isinstance(value, dict):
        value = value.get(key) or value.get("name") or value.get("code") or ""
    if value is None:
        return ""
    return str(value).strip()


def _parse_company(item: dict[str, Any]) -> dict[str, str] | None:
    names = item.get("names") or []
    name = ""
    for n in names:
        if n.get("type") == "1" or n.get("name"):
            name = _text_value(n.get("name"))
            if name:
                break
    if not name:
        return None
    bid = _text_value(item.get("businessId"))
    addr = ""
    for a in item.get("addresses") or []:
        if a.get("type") in {1, 2}:
            city = _text_value((a.get("postOffices") or [{}])[0].get("city"))
            addr = city
            break
    lines = item.get("businessLines") or []
    industry = ""
    if lines:
        line = lines[0]
        industry = _text_value(line.get("name") or line.get("code"))
    return {
        "company_name": name,
        "business_id": bid,
        "company_domain": "",
        "company_url": "",
        "industry": industry,
        "estimated_revenue": "",
        "employee_count": "",
        "country": "Finland",
        "city": addr,
    }


def discover_prh(
    *,
    business_line: str | None = None,
    location: str | None = None,
    name_contains: str | None = None,
    max_companies: int = 200,
    page_pause: float = 0.4,
) -> list[dict]:
    """Paginate PRH API until max_companies or no more pages."""
    rows: list[dict] = []
    page = 1
    params: dict[str, Any] = {}
    if business_line:
        params["businessLine"] = business_line
    if location:
        params["location"] = location
    if name_contains:
        params["name"] = name_contains

    with httpx.Client(timeout=30.0) as client:
        while len(rows) < max_companies:
            params["page"] = page
            try:
                r = client.get(API, params=params)
                if r.status_code == 429:
                    log.warning("PRH rate limit — sleep 60s")
                    time.sleep(60)
                    continue
                r.raise_for_status()
                data = r.json()
            except Exception as exc:
                log.error("PRH request failed: %s", exc)
                break
            companies = data.get("companies") or []
            if not companies:
                break
            for item in companies:
                row = _parse_company(item)
                if row:
                    rows.append(row)
                if len(rows) >= max_companies:
                    break
            total_pages = data.get("totalPages") or 1
            if page >= total_pages:
                break
            page += 1
            time.sleep(page_pause)
    return rows[:max_companies]


def discover_aether_industrial(*, max_companies: int = 200) -> list[dict]:
    """Rotate TOL codes to build industrial company list without LLM."""
    per_line = max(20, max_companies // len(INDUSTRIAL_BUSINESS_LINES) + 1)
    seen: set[str] = set()
    out: list[dict] = []
    for code in INDUSTRIAL_BUSINESS_LINES:
        if len(out) >= max_companies:
            break
        batch = discover_prh(business_line=code, max_companies=per_line)
        for row in batch:
            key = row.get("business_id") or row.get("company_name")
            if key in seen:
                continue
            seen.add(key)
            out.append(row)
    return out[:max_companies]
