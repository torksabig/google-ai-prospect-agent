"""Prospect discovery helpers. LLM-backed search is disabled; free workflows remain."""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from config import get_settings

log = logging.getLogger(__name__)

SEARCH_PROMPT = """You are a B2B prospect research agent for {product_context}
Find REAL companies and callable decision-makers from public web sources.

## Target criteria
- Country / location: {country}
- Industry / sector: {industry}
- Revenue range: {revenue}
- Primary ICP titles: {titles}
- Secondary titles (for 2nd contact): {secondary_titles}
- Companies to return: {limit}
- Callable contacts per company: {contacts_per_company}
{exclude_note}
{phone_priority}

## Your task
1. Run multiple Google searches in Finnish (for Finland) or local language + English.
   Example queries: site:.fi "{industry}" liikevaihto, "{industry}" Finland revenue CTO phone
2. For each company, verify from official website, team page, press release, or business directory.
3. Company must fit ICP: industry + revenue band + location.
4. Per company find up to {contacts_per_company} DIFFERENT people you can call about the product:
   - Contact 1: best primary ICP (R&D / CTO / innovation / engineering leader)
   - Contact 2: secondary buyer (CEO, COO, ops, or second technical leader) — not the same person
5. For EACH person: hunt direct/mobile phone on team pages, press releases, IR contacts.
   Use switchboard/main line only if no direct/mobile found after search — mark phone_type=switchboard.

## Output
Return ONLY a JSON object:
{{
  "companies": [
    {{
      "company_name": "string",
      "company_domain": "string or null",
      "industry": "string",
      "estimated_revenue": "string or null",
      "employee_count": "string or null",
      "company_url": "string or null",
      "contacts": [
        {{
          "contact_name": "string or null",
          "contact_title": "string or null",
          "contact_phone": "string or null",
          "phone_type": "direct|mobile|switchboard|unknown",
          "phone_source_url": "string or null",
          "contact_brief": "string — 2-4 sentence Finnish paragraph in the format: '<Name> on <Company>:n <Title>. Yritys ... <Title>na hänellä on keskeinen rooli ... Hänen mobiilinumeronsa/suora numeronsa on julkisesti saatavilla ...'. Mention only sourced facts; do not invent product names or responsibilities.",
          "evidence_urls": ["url"],
          "confidence": 0.0
        }}
      ]
    }}
  ],
  "search_notes": "string — what you searched, gaps, data quality warnings"
}}

Legacy single-contact fields (contact_name, contact_phone, etc.) on the company object are OK only if
contacts array is omitted — prefer contacts array with {contacts_per_company} entries.

## Rules
- NEVER invent names, titles, or phone numbers.
{phone_rules}
- Skip companies with zero callable phone for any contact when phone is required.
- confidence < 0.5 if phone is switchboard only or title uncertain.
"""

PHONE_PRIORITY_BLOCK = """
## Phone priority (STRICT)
- ONLY return contacts who hold one of the target titles (or closest R&D/innovation equivalent).
- PRIORITIZE direct dial and mobile numbers tied to the NAMED person (+358 4x… mobile, direct desk line).
- Do NOT return switchboard/main line unless no direct number exists after exhaustive search.
- Search queries must include person name + puhelin / phone / mobile when hunting direct lines.
- Rank results: mobile > direct > switchboard. Exclude switchboard-only rows if direct exists elsewhere.
"""

PHONE_RULES_DEFAULT = (
    "- Include companies where at least one contact has a verifiable phone (direct/mobile preferred).\n"
    "- Switchboard is OK when no direct/mobile exists — label phone_type=switchboard.\n"
    "- Aim for {contacts_per_company} contacts per company when data exists."
)
PHONE_RULES_STRICT = (
    "- Only include companies where you found a DIRECT or MOBILE phone for the named title-holder.\n"
    "- Skip companies with only switchboard unless you name the specific extension owner.\n"
    "- Aim for {contacts_per_company} contacts per company when data exists."
)

PHONE_RANK = {"mobile": 0, "direct": 1, "unknown": 2, "switchboard": 3}
_DIRECT_TYPES = {"mobile", "direct"}
_PHONE_DIGITS = re.compile(r"\d{6,}")


def _normalize_company(row: dict) -> dict:
    out = dict(row)
    phone = (out.get("contact_phone") or "").strip()
    out["contact_phone"] = phone
    pt = (out.get("phone_type") or "unknown").strip().lower()
    if phone and pt == "unknown":
        compact = phone.replace(" ", "").replace("-", "")
        if compact.startswith("+3584") or compact.startswith("04") or compact.startswith("+35850"):
            pt = "mobile"
        else:
            pt = "direct"
    if not phone:
        pt = "unknown"
    out["phone_type"] = pt
    if isinstance(out.get("evidence_urls"), str):
        out["evidence_urls"] = [u.strip() for u in out["evidence_urls"].split(";") if u.strip()]
    return out


def _has_verifiable_phone(row: dict, *, direct_only: bool = False) -> bool:
    phone = (row.get("contact_phone") or "").strip()
    if not phone or not _PHONE_DIGITS.search(phone.replace(" ", "")):
        return False
    pt = (row.get("phone_type") or "unknown").lower()
    if direct_only:
        return pt in _DIRECT_TYPES
    return pt in _DIRECT_TYPES | {"switchboard"}


def _has_website(row: dict) -> bool:
    url = (row.get("company_url") or "").strip()
    domain = (row.get("company_domain") or "").strip()
    return bool(url.startswith("http") or (domain and "." in domain))


_COMPANY_FIELDS = (
    "company_name",
    "company_domain",
    "industry",
    "estimated_revenue",
    "employee_count",
    "company_url",
)
_CONTACT_FIELDS = (
    "contact_name",
    "contact_title",
    "contact_phone",
    "phone_type",
    "phone_source_url",
    "contact_brief",
    "evidence_urls",
    "confidence",
)


def expand_companies_to_rows(
    companies: list[dict],
    *,
    contacts_per_company: int = 1,
) -> list[dict]:
    """Flatten company objects (with contacts[] or legacy single contact) to one row per person."""
    rows: list[dict] = []
    for co in companies:
        if not isinstance(co, dict):
            continue
        base = {k: co.get(k) for k in _COMPANY_FIELDS}
        contacts = co.get("contacts")
        if isinstance(contacts, list) and contacts:
            for contact in contacts[: max(1, contacts_per_company)]:
                if not isinstance(contact, dict):
                    continue
                row = {**base}
                for k in _CONTACT_FIELDS:
                    if k in contact:
                        row[k] = contact[k]
                rows.append(row)
            continue
        row = {**base}
        for k in _CONTACT_FIELDS:
            if k in co:
                row[k] = co[k]
        rows.append(row)
    return rows


def _company_passes_filters(
    company: dict,
    *,
    contacts_per_company: int,
    direct_phone_first: bool,
    require_phone: bool,
) -> bool:
    rows = expand_companies_to_rows([company], contacts_per_company=contacts_per_company)
    if not rows:
        return False
    if not require_phone:
        return _has_website(company) or any(_has_website(r) for r in rows)
    with_phone = [r for r in rows if _has_verifiable_phone(r, direct_only=direct_phone_first)]
    if direct_phone_first:
        return len(with_phone) >= 1
    return len(with_phone) >= 1


def filter_companies(
    companies: list[dict],
    *,
    direct_phone_first: bool = False,
    require_phone: bool = True,
    contacts_per_company: int = 1,
) -> list[dict]:
    """Filter company-level results, then expand to contact rows."""
    kept: list[dict] = []
    for co in companies:
        if not isinstance(co, dict):
            continue
        if _company_passes_filters(
            co,
            contacts_per_company=contacts_per_company,
            direct_phone_first=direct_phone_first,
            require_phone=require_phone,
        ):
            kept.append(co)
    rows = expand_companies_to_rows(kept, contacts_per_company=contacts_per_company)
    normalized = [_normalize_company(r) for r in rows]
    if require_phone:
        if direct_phone_first:
            return [c for c in normalized if _has_verifiable_phone(c, direct_only=True)]
        return [c for c in normalized if _has_verifiable_phone(c, direct_only=False)]
    return [c for c in normalized if _has_website(c)]


def _sort_by_phone_priority(companies: list[dict]) -> list[dict]:
    def key(row: dict) -> tuple:
        pt = (row.get("phone_type") or "unknown").lower()
        conf = float(row.get("confidence") or 0)
        return (PHONE_RANK.get(pt, 2), -conf)

    return sorted(companies, key=key)


CONTACT_DEEP_PROMPT = """Use Google Search to find decision makers and phones for this company.

Company: {company_name}
Domain: {domain}
Country: {country}
Primary titles: {titles}
Secondary titles: {secondary_titles}
Industry context: {industry}
Contacts already found (find DIFFERENT people): {existing_contacts}

Find up to {need_contacts} callable ICP contacts with phones (direct/mobile preferred; switchboard if needed).

Return ONLY JSON:
{{
  "contacts": [
    {{
      "contact_name": "string or null",
      "contact_title": "string or null",
      "contact_phone": "string or null",
      "phone_type": "direct|mobile|switchboard|unknown",
      "phone_source_url": "string or null",
      "contact_brief": "string",
      "evidence_urls": ["url"],
      "confidence": 0.0
    }}
  ]
}}

Write contact_brief as a 2-4 sentence Finnish paragraph:
- Start with "{contact_name} on {company_name}:n {contact_title}."
- Explain why the company and role fit the ICP using sourced facts.
- End with phone context, e.g. "Hänen mobiilinumeronsa on julkisesti saatavilla yrityksen yhteystietosivulla."
Never invent data. Omit phone if not found in sources.
"""


def _parse_json(text: str) -> dict[str, Any] | None:
    text = (text or "").strip()
    if not text:
        return None
    # Strip markdown fences if present
    if "```" in text:
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"\s*```\s*$", "", text, flags=re.MULTILINE).strip()
    candidates = [text]
    m = re.search(r"\{[\s\S]*\"companies\"[\s\S]*\}", text)
    if m:
        candidates.insert(0, m.group(0))
    for candidate in candidates:
        try:
            data = json.loads(candidate)
            if isinstance(data, dict) and "companies" in data:
                return data
        except json.JSONDecodeError:
            continue
    companies: list[dict] = []
    for mobj in re.finditer(
        r"\{\s*\"company_name\"\s*:\s*\"[^\"]+\"[\s\S]*?\}(?=\s*,\s*\{|\s*\])",
        text,
    ):
        try:
            row = json.loads(mobj.group(0))
            if isinstance(row, dict) and row.get("company_name"):
                companies.append(row)
        except json.JSONDecodeError:
            continue
    if companies:
        notes_m = re.search(r"\"search_notes\"\s*:\s*\"([^\"]*)\"", text)
        return {
            "companies": companies,
            "search_notes": notes_m.group(1) if notes_m else "Recovered from partial JSON",
        }
    return None


def _grounded_generate_openrouter(prompt: str, *, json_mode: bool = True) -> tuple[str | None, Any]:
    """OpenRouter + openrouter:web_search (tekoälyhaku equivalent)."""
    import httpx

    from config import get_settings, resolve_api_key, resolve_provider

    s = get_settings()
    if resolve_provider(s) != "openrouter":
        raise RuntimeError("OpenRouter provider not configured")
    key = resolve_api_key(s)
    if not key:
        raise RuntimeError("GAP_OPENROUTER_API_KEY not set in .env")

    body: dict[str, Any] = {
        "model": s.openrouter_model,
        "messages": [{"role": "user", "content": prompt}],
        "tools": [
            {
                "type": "openrouter:web_search",
                "openrouter:web_search": {
                    "engine": "native",
                    "max_results": 8,
                    "search_context_size": "high",
                },
            }
        ],
        "temperature": 0,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}

    with httpx.Client(timeout=300.0) as client:
        resp = client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://aether-applied.local",
                "X-Title": "google-ai-prospect-agent",
            },
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()

    choices = data.get("choices") or []
    if not choices:
        return None, data
    message = choices[0].get("message") or {}
    content = (message.get("content") or "").strip()
    return content or None, data


def _grounded_generate_google(prompt: str, *, json_mode: bool = True) -> tuple[str | None, Any]:
    """Call Gemini with Google Search grounding."""
    from google import genai
    from google.genai import types

    from config import get_settings, resolve_api_key

    s = get_settings()
    key = resolve_api_key(s)
    if not key:
        raise RuntimeError("GAP_GEMINI_API_KEY not set in .env")

    client = genai.Client(api_key=key)
    config: dict[str, Any] = {
        "tools": [types.Tool(google_search=types.GoogleSearch())],
        "temperature": 0,
    }
    # Google Search grounding cannot be combined with response_mime_type=json.
    models = [s.gemini_model]
    last_exc: Exception | None = None
    for model in models:
        for attempt in range(6):
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(**config),
                )
                meta = getattr(response, "candidates", None)
                grounding = None
                if meta and meta[0]:
                    grounding = getattr(meta[0], "grounding_metadata", None)
                return (response.text or "").strip() or None, grounding
            except Exception as exc:
                last_exc = exc
                msg = str(exc)
                if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                    wait = 15 * (attempt + 1)
                    log.warning("Quota/rate limit on %s — wait %ss", model, wait)
                    time.sleep(wait)
                    continue
                if "500" in msg or "INTERNAL" in msg or "503" in msg or "UNAVAILABLE" in msg:
                    wait = 10 * (attempt + 1)
                    log.warning("Transient error on %s (attempt %d) — wait %ss", model, attempt + 1, wait)
                    time.sleep(wait)
                    continue
                if "400" in msg or "INVALID_ARGUMENT" in msg:
                    raise
                log.warning("Grounded search failed (%s): %s", model, exc)
                break
    if last_exc:
        raise last_exc
    return None, None


def _grounded_generate(prompt: str, *, json_mode: bool = True) -> tuple[str | None, Any]:
    raise RuntimeError(
        "LLM-backed search is disabled in this repo. Use discover-prh + "
        "enrich --scrape-first + verify instead."
    )


def search_prospects(
    *,
    country: str,
    industry: str,
    revenue: str,
    titles: str,
    limit: int = 20,
    secondary_titles: str = "",
    contacts_per_company: int = 1,
    product_context: str = "Aether Applied B2B industrial outreach.",
    direct_phone_first: bool = False,
    require_phone: bool = True,
    exclude_companies: list[str] | None = None,
) -> dict[str, Any]:
    """Legacy entrypoint. LLM-backed search is disabled in this repo."""
    phone_priority = PHONE_PRIORITY_BLOCK if direct_phone_first else ""
    phone_rules_tpl = PHONE_RULES_STRICT if direct_phone_first else PHONE_RULES_DEFAULT
    phone_rules = phone_rules_tpl.format(contacts_per_company=contacts_per_company)
    if not require_phone:
        phone_rules = (
            "- Include companies with a verified official website (company_url or company_domain).\n"
            "- Phone numbers optional but include when found.\n"
            f"- Aim for {contacts_per_company} contacts per company when possible."
        )
    exclude_note = ""
    if exclude_companies:
        exclude_note = (
            "\n## Already found (DO NOT repeat)\n"
            + ", ".join(exclude_companies[:50])
        )
    prompt = SEARCH_PROMPT.format(
        product_context=product_context,
        country=country,
        industry=industry,
        revenue=revenue,
        titles=titles,
        secondary_titles=secondary_titles or titles,
        limit=limit,
        contacts_per_company=contacts_per_company,
        exclude_note=exclude_note,
        phone_priority=phone_priority,
        phone_rules=phone_rules,
    )
    text, grounding = _grounded_generate(prompt)
    if not text:
        return {
            "companies": [],
            "search_notes": "No response from model (try smaller --batch-size)",
            "grounding": None,
        }

    data = _parse_json(text)
    if not data:
        log.warning("Could not parse JSON from model response (%d chars)", len(text or ""))
        data = {"companies": [], "search_notes": "JSON parse failed"}
    companies = filter_companies(
        data.get("companies") or [],
        direct_phone_first=direct_phone_first,
        require_phone=require_phone,
        contacts_per_company=contacts_per_company,
    )
    data["companies"] = _sort_by_phone_priority(companies)[: limit * max(1, contacts_per_company)]
    data["_company_count"] = len({(c.get("company_name") or "").strip() for c in data["companies"]})
    data["_raw_response"] = text
    data["_grounding"] = str(grounding) if grounding else None
    return data


def enrich_contacts(
    *,
    company_name: str,
    domain: str,
    country: str,
    industry: str,
    titles: str,
    secondary_titles: str = "",
    existing_contacts: list[dict] | None = None,
    need_contacts: int = 1,
) -> list[dict]:
    """Second-pass grounded search for additional contacts + phones."""
    existing = existing_contacts or []
    names = ", ".join(
        f"{c.get('contact_name', '?')} ({c.get('contact_title', '')})" for c in existing if c.get("contact_name")
    ) or "none"
    prompt = CONTACT_DEEP_PROMPT.format(
        company_name=company_name,
        domain=domain or "",
        country=country,
        industry=industry,
        titles=titles,
        secondary_titles=secondary_titles or titles,
        existing_contacts=names,
        need_contacts=need_contacts,
    )
    text, _ = _grounded_generate(prompt)
    if not text:
        return []
    parsed = _parse_json(text) or {}
    contacts = parsed.get("contacts")
    if isinstance(contacts, list):
        return [c for c in contacts if isinstance(c, dict)]
    if parsed.get("contact_name") or parsed.get("contact_phone"):
        return [parsed]
    return []


def enrich_contact(
    *,
    company_name: str,
    domain: str,
    country: str,
    industry: str,
    titles: str,
    secondary_titles: str = "",
) -> dict[str, Any]:
    """Second-pass grounded search for one company's contact + phone (legacy)."""
    found = enrich_contacts(
        company_name=company_name,
        domain=domain,
        country=country,
        industry=industry,
        titles=titles,
        secondary_titles=secondary_titles,
        need_contacts=1,
    )
    return found[0] if found else {}
