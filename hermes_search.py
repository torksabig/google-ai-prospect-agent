"""Hermes Google Search prospecting — free web search + page scrape, no LLM by default."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx

from website_scrape import scrape_phones_from_site

log = logging.getLogger(__name__)

PHONE_RE = re.compile(r"(?:\+358|0)\s*[\d\s\-]{6,14}\d")
NAME_RE = re.compile(
    r"(?:^|[>\s])([A-ZÄÖÅ][a-zäöå]+(?:\s+[A-ZÄÖÅ][a-zäöå]+){1,3})(?:\s*[,\-–—|]\s*|\s*</)",
)
TITLE_RE = re.compile(
    r"(engineering manager|insinööripäällikkö|cto|teknologiajohtaja|head of r&d|"
    r"tutkimusjohtaja|vp engineering|toimitusjohtaja|ceo|chief technology|"
    r"kehitysjohtaja|innovaatiojohtaja|director|johtaja)",
    re.I,
)
SKIP_DOMAINS = frozenset(
    {
        "facebook.com",
        "linkedin.com",
        "twitter.com",
        "x.com",
        "instagram.com",
        "youtube.com",
        "wikipedia.org",
        "finder.fi",
        "proff.fi",
        "asiakastieto.fi",
        "ytj.fi",
        "google.com",
        "duckduckgo.com",
    }
)

INDUSTRY_FI: dict[str, list[str]] = {
    "energy": ["energia", "energiatekniikka"],
    "manufacturing": ["valmistus", "teollisuus", "konepaja"],
    "mining": ["kaivos", "louhinta"],
    "proptech": ["proptech", "kiinteistöteknologia", "kiinteistö"],
    "industrial": ["teollisuusautomaatio", "teollisuus"],
    "automation": ["automaatio", "teollisuusautomaatio"],
}

TITLE_FI: dict[str, list[str]] = {
    "engineering manager": ["Engineering Manager", "insinööripäällikkö"],
    "cto": ["CTO", "teknologiajohtaja"],
    "head of r&d": ["Head of R&D", "tutkimusjohtaja", "tutkimus- ja kehitysjohtaja"],
    "vp engineering": ["VP Engineering", "VP Engineering"],
    "ceo": ["CEO", "toimitusjohtaja"],
    "director": ["director", "johtaja"],
}

OUTPUT_SCHEMA_KEYS = (
    "company_name",
    "company_domain",
    "industry",
    "estimated_revenue",
    "employee_count",
    "contact_name",
    "contact_title",
    "contact_phone",
    "phone_type",
    "phone_source_url",
    "company_url",
    "contact_brief",
    "confidence",
    "evidence_urls",
    "country",
    "source",
)


@dataclass
class HermesSearchSpec:
    country: str = "Finland"
    industries: list[str] = field(default_factory=lambda: ["energy", "manufacturing"])
    revenue: str = "10M - 5B EUR"
    titles: list[str] = field(
        default_factory=lambda: [
            "Engineering Manager",
            "CTO",
            "Head of R&D",
            "VP Engineering",
            "CEO",
        ]
    )
    company_limit: int = 20
    contacts_per_company: int = 2
    queries_per_combo: int = 2
    max_results_per_query: int = 8
    pause_seconds: float = 1.5
    provider: str = "google"  # google | serper | gemini


def _split_csv(value: str) -> list[str]:
    return [p.strip() for p in value.split(",") if p.strip()]


def _industry_terms(industries: list[str]) -> list[str]:
    terms: list[str] = []
    for ind in industries:
        key = ind.strip().lower()
        if key in INDUSTRY_FI:
            terms.extend(INDUSTRY_FI[key])
        else:
            terms.append(ind)
    return list(dict.fromkeys(terms))


def _title_terms(titles: list[str]) -> list[str]:
    terms: list[str] = []
    for title in titles:
        key = title.strip().lower()
        if key in TITLE_FI:
            terms.extend(TITLE_FI[key])
        else:
            terms.append(title)
    return list(dict.fromkeys(terms))


def build_search_queries(spec: HermesSearchSpec) -> list[str]:
    """Finnish Google-style query templates (site:.fi + title + phone/contact + industry)."""
    industries = _industry_terms(spec.industries)
    titles = _title_terms(spec.titles)
    templates = (
        'site:.fi "{title}" "puhelin" "{industry}"',
        'site:.fi "{title}" "yhteystiedot" "{industry}"',
        'site:.fi "{title}" "puhelinnumero" "{industry}"',
        'site:.fi "toimitusjohtaja" "puhelin" "{industry}"',
    )
    queries: list[str] = []
    for industry in industries:
        for title in titles:
            for tpl in templates[: spec.queries_per_combo]:
                queries.append(tpl.format(title=title, industry=industry))
    return list(dict.fromkeys(queries))


def _domain_from_url(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def _should_skip_url(url: str) -> bool:
    domain = _domain_from_url(url)
    if not domain or not domain.endswith(".fi"):
        return True
    return any(domain == s or domain.endswith("." + s) for s in SKIP_DOMAINS)


def _search_ddgs(query: str, *, max_results: int) -> list[dict[str, str]]:
    try:
        from ddgs import DDGS
    except ImportError:
        log.warning("ddgs not installed — pip install ddgs")
        return []
    out: list[dict[str, str]] = []
    try:
        with DDGS() as ddgs:
            for hit in ddgs.text(query, region="fi-fi", max_results=max_results):
                url = (hit.get("href") or hit.get("link") or "").strip()
                if not url or _should_skip_url(url):
                    continue
                out.append(
                    {
                        "url": url,
                        "title": (hit.get("title") or "").strip(),
                        "snippet": (hit.get("body") or hit.get("snippet") or "").strip(),
                    }
                )
    except Exception as exc:
        log.warning("Search failed for %r: %s", query, exc)
    return out


def _search_serper(query: str, *, max_results: int, api_key: str) -> list[dict[str, str]]:
    if not api_key:
        return []
    out: list[dict[str, str]] = []
    try:
        with httpx.Client(timeout=20.0) as client:
            r = client.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
                json={"q": query, "gl": "fi", "hl": "fi", "num": max_results},
            )
            r.raise_for_status()
            for item in (r.json() or {}).get("organic", [])[:max_results]:
                url = (item.get("link") or "").strip()
                if not url or _should_skip_url(url):
                    continue
                out.append(
                    {
                        "url": url,
                        "title": (item.get("title") or "").strip(),
                        "snippet": (item.get("snippet") or "").strip(),
                    }
                )
    except Exception as exc:
        log.warning("Serper search failed for %r: %s", query, exc)
    return out


def run_web_search(
    query: str,
    *,
    provider: str = "google",
    max_results: int = 8,
    serper_api_key: str = "",
) -> list[dict[str, str]]:
    if provider == "serper":
        hits = _search_serper(query, max_results=max_results, api_key=serper_api_key)
        if hits:
            return hits
    return _search_ddgs(query, max_results=max_results)


def _fetch_html(url: str) -> str:
    with httpx.Client(
        timeout=15.0,
        follow_redirects=True,
        headers={"User-Agent": "HermesProspectBot/1.0 (+contact enrichment)"},
    ) as client:
        r = client.get(url)
        r.raise_for_status()
        return r.text


def _guess_company_name(domain: str, page_title: str = "") -> str:
    title = re.sub(r"\s*[\|\-–—].*$", "", page_title).strip()
    if title and len(title) > 2 and "yhteystiedot" not in title.lower():
        return title
    base = domain.split(".")[0].replace("-", " ").title()
    return f"{base} Oy" if base else domain


def _phone_type(phone: str) -> str:
    compact = phone.replace(" ", "")
    if re.search(r"\+358\s*4|\+3584|^04", compact):
        return "mobile"
    if compact.startswith("+3589") or compact.startswith("010") or compact.startswith("020"):
        return "switchboard"
    return "direct"


def _extract_contacts_from_html(
    html: str,
    *,
    source_url: str,
    default_title: str = "",
) -> list[dict[str, Any]]:
    contacts: list[dict[str, Any]] = []
    phones = list(dict.fromkeys(PHONE_RE.findall(html)))
    names = list(dict.fromkeys(m.group(1).strip() for m in NAME_RE.finditer(html)))
    titles_found = TITLE_RE.findall(html)
    default_title = default_title or (titles_found[0] if titles_found else "")

    for i, phone in enumerate(phones[:3]):
        phone = re.sub(r"\s+", " ", phone).strip()
        name = names[i] if i < len(names) else ""
        title = titles_found[i] if i < len(titles_found) else default_title
        if not name and not phone:
            continue
        contacts.append(
            {
                "contact_name": name,
                "contact_title": title,
                "contact_phone": phone,
                "phone_type": _phone_type(phone),
                "phone_source_url": source_url,
                "confidence": 0.5 if name else 0.35,
            }
        )
    return contacts


def _build_contact_brief(row: dict[str, Any]) -> str:
    name = (row.get("contact_name") or "").strip()
    title = (row.get("contact_title") or "").strip()
    company = (row.get("company_name") or "").strip()
    industry = (row.get("industry") or "").strip()
    if not name or not company:
        return ""
    role = title or "yhteyshenkilö"
    parts = [f"{name} on {company}:n {role}."]
    if industry:
        parts.append(f"Yritys toimii {industry}-alalla.")
    parts.append(
        "Yhteystiedot on poimittu julkisesta verkkolähteestä; puhelinnumero kannattaa varmistaa ennen soittoa."
    )
    return " ".join(parts)


def _normalize_row(
    *,
    company_name: str,
    domain: str,
    company_url: str,
    industry: str,
    revenue: str,
    country: str,
    contact: dict[str, Any],
    evidence_urls: list[str],
) -> dict[str, Any]:
    row = {
        "company_name": company_name,
        "company_domain": domain,
        "industry": industry,
        "estimated_revenue": revenue,
        "employee_count": "",
        "contact_name": contact.get("contact_name") or "",
        "contact_title": contact.get("contact_title") or "",
        "contact_phone": contact.get("contact_phone") or "",
        "phone_type": contact.get("phone_type") or "unknown",
        "phone_source_url": contact.get("phone_source_url") or company_url,
        "company_url": company_url,
        "confidence": str(contact.get("confidence") or 0.4),
        "evidence_urls": "; ".join(evidence_urls[:5]),
        "country": country,
        "source": "ICP Search",
    }
    row["contact_brief"] = _build_contact_brief(row)
    return row


def _domain_key(row: dict[str, Any]) -> str:
    d = (row.get("company_domain") or "").strip().lower()
    if d.startswith("www."):
        d = d[4:]
    return d or (row.get("company_name") or "").strip().lower()


def _phone_digits(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


def dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Dedupe by domain + contact name, then drop duplicate phone numbers."""
    seen_keys: set[str] = set()
    seen_phones: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        dk = _domain_key(row)
        name = (row.get("contact_name") or "").strip().lower()
        phone = _phone_digits(row.get("contact_phone") or "")
        key = f"{dk}|{name}|{phone}"
        if not dk or key in seen_keys:
            continue
        if phone and phone in seen_phones:
            continue
        seen_keys.add(key)
        if phone:
            seen_phones.add(phone)
        out.append(row)
    return out


def _extract_from_hit(
    hit: dict[str, str],
    *,
    industry: str,
    revenue: str,
    country: str,
) -> list[dict[str, Any]]:
    url = hit["url"]
    domain = _domain_from_url(url)
    if not domain:
        return []
    company_name = _guess_company_name(domain, hit.get("title", ""))
    company_url = f"https://{domain}"
    evidence = [url]
    rows: list[dict[str, Any]] = []

    try:
        html = _fetch_html(url)
        contacts = _extract_contacts_from_html(html, source_url=url)
    except Exception:
        contacts = []

    if not contacts:
        scraped = scrape_phones_from_site(
            company_name=company_name,
            domain=domain,
            company_url=company_url,
        )
        for s in scraped:
            contacts.append(
                {
                    "contact_name": "",
                    "contact_title": s.get("contact_title") or "",
                    "contact_phone": s.get("contact_phone") or "",
                    "phone_type": s.get("phone_type") or "unknown",
                    "phone_source_url": s.get("phone_source_url") or url,
                    "confidence": s.get("confidence") or 0.45,
                }
            )

    snippet_phone = PHONE_RE.search(hit.get("snippet", ""))
    if snippet_phone and not contacts:
        contacts.append(
            {
                "contact_name": "",
                "contact_title": "",
                "contact_phone": snippet_phone.group(0).strip(),
                "phone_type": _phone_type(snippet_phone.group(0)),
                "phone_source_url": url,
                "confidence": 0.3,
            }
        )

    for contact in contacts:
        if not (contact.get("contact_phone") or "").strip():
            continue
        rows.append(
            _normalize_row(
                company_name=company_name,
                domain=domain,
                company_url=company_url,
                industry=industry,
                revenue=revenue,
                country=country,
                contact=contact,
                evidence_urls=evidence,
            )
        )
    return rows


def _trim_companies(
    rows: list[dict[str, Any]], company_limit: int, contacts_per_company: int
) -> list[dict[str, Any]]:
    by_co: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for row in rows:
        dk = _domain_key(row)
        if not dk:
            continue
        if dk not in by_co:
            if len(order) >= company_limit:
                continue
            order.append(dk)
            by_co[dk] = []
        if len(by_co[dk]) < contacts_per_company:
            by_co[dk].append(row)
    out: list[dict[str, Any]] = []
    for dk in order:
        out.extend(by_co[dk])
    return out


def run_hermes_search(
    spec: HermesSearchSpec,
    *,
    serper_api_key: str = "",
    continue_from: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Execute Hermes Google Search workflow. Returns (rows, meta)."""
    if spec.provider == "gemini":
        raise ValueError("Use cli.py search --provider gemini for Gemini mode")

    seed = list(continue_from or [])
    queries = build_search_queries(spec)
    collected: list[dict[str, Any]] = list(seed)
    companies_seen = {_domain_key(r) for r in seed if _domain_key(r)}
    meta: dict[str, Any] = {
        "provider": spec.provider,
        "queries_run": 0,
        "hits_seen": 0,
        "criteria": {
            "country": spec.country,
            "industries": spec.industries,
            "revenue": spec.revenue,
            "titles": spec.titles,
            "company_limit": spec.company_limit,
            "contacts_per_company": spec.contacts_per_company,
        },
    }

    industry_terms = _industry_terms(spec.industries)
    primary_industry = ", ".join(industry_terms[:3])

    for query in queries:
        if len(companies_seen) >= spec.company_limit:
            break
        meta["queries_run"] += 1
        log.info("Hermes search: %s", query)
        hits = run_web_search(
            query,
            provider=spec.provider,
            max_results=spec.max_results_per_query,
            serper_api_key=serper_api_key,
        )
        meta["hits_seen"] += len(hits)
        for hit in hits:
            domain = _domain_from_url(hit["url"])
            if not domain or domain in companies_seen:
                continue
            new_rows = _extract_from_hit(
                hit,
                industry=primary_industry,
                revenue=spec.revenue,
                country=spec.country,
            )
            if not new_rows:
                continue
            companies_seen.add(domain)
            collected.extend(new_rows)
            if len(companies_seen) >= spec.company_limit:
                break
        time.sleep(spec.pause_seconds)

    collected = dedupe_rows(collected)
    trimmed = _trim_companies(collected, spec.company_limit, spec.contacts_per_company)
    meta["company_count"] = len({_domain_key(r) for r in trimmed if _domain_key(r)})
    meta["contact_rows"] = len(trimmed)
    meta["queries"] = queries[: meta["queries_run"]]
    return trimmed, meta
