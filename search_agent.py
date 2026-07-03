"""Gemini + Google Search grounding (tekoälyhaku) prospect discovery — no lead lists."""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from config import get_settings

log = logging.getLogger(__name__)

SEARCH_PROMPT = """You are a B2B prospect research agent. Use Google Search (tekoälyhaku) to find REAL companies and contacts.

## Target criteria
- Country: {country}
- Industry / sector: {industry}
- Revenue range: {revenue}
- Decision-maker titles to find: {titles}
- Number of companies to return: {limit}
{phone_priority}

## Your task
1. Run multiple Google searches in Finnish (for Finland) or local language + English.
   Example queries: site:.fi "{industry}" liikevaihto, "{industry}" Finland revenue CEO phone, yritys tutkimusjohtaja puhelin
2. For each company, verify from official website, team page, press release, or business directory.
3. Find the best-matching decision maker for the requested titles (innovation/R&D/CTO preferred over generic CEO if specified).
4. Extract DIRECT phone numbers when publicly listed (mobile +358…, direct line). Mark switchboard separately.

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
      "contact_name": "string or null",
      "contact_title": "string or null",
      "contact_phone": "string or null",
      "phone_type": "direct|mobile|switchboard|unknown",
      "phone_source_url": "string or null",
      "company_url": "string or null",
      "contact_brief": "string — 2-4 sentence narrative: why this person owns innovation/R&D, their scope, phone context. Finnish for Finland.",
      "evidence_urls": ["url", ...],
      "confidence": 0.0
    }}
  ],
  "search_notes": "string — what you searched, gaps, data quality warnings"
}}

## Rules
- NEVER invent names, titles, or phone numbers.
{phone_rules}
- If fewer than {limit} companies have verified phones, return what you found honestly.
- confidence < 0.5 if phone is switchboard only or contact title is uncertain.
"""

PHONE_PRIORITY_BLOCK = """
## Phone priority (STRICT)
- ONLY return contacts who hold one of the target titles (or closest R&D/innovation equivalent).
- PRIORITIZE direct dial and mobile numbers tied to the NAMED person (+358 4x… mobile, direct desk line).
- Do NOT return switchboard/main line unless no direct number exists after exhaustive search.
- Search queries must include person name + puhelin / phone / mobile when hunting direct lines.
- Rank results: mobile > direct > switchboard. Exclude switchboard-only rows if direct exists elsewhere.
"""

PHONE_RULES_DEFAULT = "- Only include companies where you found at least one verifiable phone (direct/mobile preferred)."
PHONE_RULES_STRICT = (
    "- Only include companies where you found a DIRECT or MOBILE phone for the named title-holder.\n"
    "- Skip companies with only switchboard unless you name the specific extension owner."
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


def filter_companies(companies: list[dict], *, direct_phone_first: bool) -> list[dict]:
    normalized = [_normalize_company(c) for c in companies if isinstance(c, dict)]
    if direct_phone_first:
        return [c for c in normalized if _has_verifiable_phone(c, direct_only=True)]
    return [c for c in normalized if _has_verifiable_phone(c, direct_only=False)]


def _sort_by_phone_priority(companies: list[dict]) -> list[dict]:
    def key(row: dict) -> tuple:
        pt = (row.get("phone_type") or "unknown").lower()
        conf = float(row.get("confidence") or 0)
        return (PHONE_RANK.get(pt, 2), -conf)

    return sorted(companies, key=key)


CONTACT_DEEP_PROMPT = """Use Google Search to find the best decision maker and DIRECT phone for this company.

Company: {company_name}
Domain: {domain}
Country: {country}
Target titles: {titles}
Industry context: {industry}

Search team pages, LinkedIn snippets in Google, press releases, Finnish/YTJ/PRH public pages if relevant.

Return ONLY JSON:
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
        for attempt in range(4):
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
                if "400" in msg or "INVALID_ARGUMENT" in msg:
                    raise
                log.warning("Grounded search failed (%s): %s", model, exc)
                break
    if last_exc:
        raise last_exc
    return None, None


def _grounded_generate(prompt: str, *, json_mode: bool = True) -> tuple[str | None, Any]:
    from config import get_settings, resolve_provider

    provider = resolve_provider(get_settings())
    if provider == "openrouter":
        return _grounded_generate_openrouter(prompt, json_mode=json_mode)
    return _grounded_generate_google(prompt, json_mode=json_mode)


def search_prospects(
    *,
    country: str,
    industry: str,
    revenue: str,
    titles: str,
    limit: int = 20,
    direct_phone_first: bool = False,
) -> dict[str, Any]:
    """Discover companies + contacts via Google AI Search only."""
    phone_priority = PHONE_PRIORITY_BLOCK if direct_phone_first else ""
    phone_rules = PHONE_RULES_STRICT if direct_phone_first else PHONE_RULES_DEFAULT
    prompt = SEARCH_PROMPT.format(
        country=country,
        industry=industry,
        revenue=revenue,
        titles=titles,
        limit=limit,
        phone_priority=phone_priority,
        phone_rules=phone_rules,
    )
    text, grounding = _grounded_generate(prompt)
    if not text:
        return {"companies": [], "search_notes": "No response from model", "grounding": None}

    data = _parse_json(text)
    if not data:
        log.warning("Could not parse JSON from model response (%d chars)", len(text or ""))
        data = {"companies": [], "search_notes": "JSON parse failed — see raw_response in meta file"}
    companies = filter_companies(data.get("companies") or [], direct_phone_first=direct_phone_first)
    data["companies"] = _sort_by_phone_priority(companies)[:limit]
    data["_raw_response"] = text
    data["_grounding"] = str(grounding) if grounding else None
    return data


def enrich_contact(
    *,
    company_name: str,
    domain: str,
    country: str,
    industry: str,
    titles: str,
) -> dict[str, Any]:
    """Second-pass grounded search for one company's contact + phone."""
    prompt = CONTACT_DEEP_PROMPT.format(
        company_name=company_name,
        domain=domain or "",
        country=country,
        industry=industry,
        titles=titles,
    )
    text, _ = _grounded_generate(prompt)
    if not text:
        return {}
    return _parse_json(text) or {}
