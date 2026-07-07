"""Lightweight website contact-page scrape — no LLM. Fallback before Gemini."""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

import httpx

_PHONE = re.compile(
    r"(?:\+358|0)\s*[\d\s\-]{6,14}\d"
)
_TITLE_HINTS = re.compile(
    r"(cto|teknologiajohtaja|tutkimusjohtaja|r&d|innovaatio|engineering|kehitysjohtaja|"
    r"toimitusjohtaja|ceo|chief technology)",
    re.I,
)
_PATHS = (
    "/yhteystiedot",
    "/contact",
    "/contacts",
    "/team",
    "/management",
    "/about-us/contact",
    "/fi/yhteystiedot",
    "/en/contact",
)


def _guess_domain_url(domain: str, company_url: str | None) -> str | None:
    if company_url and company_url.startswith("http"):
        return company_url.rstrip("/")
    d = (domain or "").strip()
    if not d:
        return None
    if not d.startswith("http"):
        d = f"https://{d.lstrip('www.')}"
    return d.rstrip("/")


def _fetch(url: str, timeout: float = 12.0) -> str:
    with httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": "AetherProspectBot/1.0 (+contact enrichment)"},
    ) as client:
        r = client.get(url)
        r.raise_for_status()
        return r.text


def scrape_phones_from_site(
    *,
    company_name: str,
    domain: str = "",
    company_url: str | None = None,
) -> list[dict]:
    """Return [{contact_phone, phone_type, phone_source_url, contact_title?}, ...]."""
    base = _guess_domain_url(domain, company_url)
    if not base:
        return []
    found: list[dict] = []
    seen_urls: set[str] = set()
    for path in _PATHS:
        url = urljoin(base + "/", path.lstrip("/"))
        if url in seen_urls:
            continue
        seen_urls.add(url)
        try:
            html = _fetch(url)
        except Exception:
            continue
        phones = list(dict.fromkeys(_PHONE.findall(html)))
        if not phones:
            continue
        title_match = _TITLE_HINTS.search(html)
        for raw in phones[:3]:
            phone = re.sub(r"\s+", " ", raw).strip()
            pt = "mobile" if re.search(r"\+358\s*4|\+3584|^04", phone.replace(" ", "")) else "direct"
            if phone.replace(" ", "").startswith("+3589") or phone.startswith("010"):
                pt = "switchboard"
            found.append(
                {
                    "contact_phone": phone,
                    "phone_type": pt,
                    "phone_source_url": url,
                    "contact_title": title_match.group(0) if title_match else None,
                    "confidence": 0.55,
                }
            )
        if found:
            break
    return found[:2]
