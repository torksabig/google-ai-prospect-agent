#!/usr/bin/env python3
"""CLI: free/public-web prospecting for B2B leads."""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

from aether_icp import AETHER_DEFAULTS
from search_agent import (
    enrich_contact,
    enrich_contacts,
    filter_companies,
    search_prospects,
    _sort_by_phone_priority,
)

from notion_pipeline import (
    build_prospect_notes,
    load_notion_config,
    normalize_sync_rows,
    sync_pipeline_to_notion,
    write_contacts_only_csv,
    write_pipeline_csv,
)
from phone_verify import verify_rows
from pipeline_filter import (
    FilterResult,
    filter_outreach_ready,
    format_filter_report,
    outreach_reject_reason,
)
from prh_discover import discover_aether_industrial, discover_prh
from website_scrape import scrape_phones_from_site

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

OUTPUT_COLUMNS = [
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
]

VERIFY_COLUMNS = [
    "phone_verification_status",
    "phone_verification_reason",
    "phone_owner_match",
    "phone_page_match",
    "verified_for_outreach",
]

DASHBOARD_DEFAULT = (
    Path(__file__).resolve().parents[2]
    / "outreach-automation/aether-applied-leads/data"
)


def _domain_key(row: dict) -> str:
    d = (row.get("company_domain") or "").strip().lower()
    d = d.replace("https://", "").replace("http://", "").split("/")[0]
    if d.startswith("www."):
        d = d[4:]
    if d:
        return d
    return (row.get("company_name") or "").strip().lower()


def _company_count(rows: list[dict]) -> int:
    return len({_domain_key(r) for r in rows if _domain_key(r)})


def _dedupe_contact_rows(rows: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for row in rows:
        dk = _domain_key(row)
        name = (row.get("contact_name") or "").strip().lower()
        key = f"{dk}|{name}" if name else dk
        if not dk or key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _unique_company_names(rows: list[dict]) -> list[str]:
    return sorted(
        {(r.get("company_name") or "").strip() for r in rows if (r.get("company_name") or "").strip()}
    )


def _trim_to_company_limit(
    rows: list[dict], limit: int, contacts_per_company: int
) -> list[dict]:
    by_co: dict[str, list[dict]] = {}
    order: list[str] = []
    for row in _sort_by_phone_priority(rows):
        dk = _domain_key(row)
        if not dk:
            continue
        if dk not in by_co:
            if len(order) >= limit:
                continue
            order.append(dk)
            by_co[dk] = []
        if len(by_co[dk]) < contacts_per_company:
            by_co[dk].append(row)
    out: list[dict] = []
    for dk in order:
        out.extend(by_co[dk])
    return out


def _apply_aether_preset(args: argparse.Namespace) -> None:
    icp = AETHER_DEFAULTS
    args.country = icp.country
    args.industry = icp.industry
    args.revenue = icp.revenue
    args.titles = icp.titles
    args.secondary_titles = icp.secondary_titles
    args.limit = icp.company_limit
    args.contacts_per_company = icp.contacts_per_company
    args.batch_size = icp.batch_size
    args.max_batches = icp.max_batches
    args.batch_pause = icp.batch_pause
    args.rotate_industries = icp.rotate_industries
    args.direct_phone_first = icp.direct_phone_first
    args.product_context = icp.product_context
    if not args.websites_only and icp.require_phone:
        args.websites_only = False


def _load_seed_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _write_outputs(
    rows: list[dict],
    out_dir: Path,
    stamp: str,
    *,
    include_verify: bool = False,
    basename: str | None = None,
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    cols = OUTPUT_COLUMNS + (VERIFY_COLUMNS if include_verify else [])
    stem = basename or f"web_prospects_{stamp}"
    csv_path = out_dir / f"{stem}.csv"
    md_path = out_dir / f"{stem}.md"

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            r = dict(row)
            if isinstance(r.get("evidence_urls"), list):
                r["evidence_urls"] = "; ".join(r["evidence_urls"])
            w.writerow(r)

    lines = [f"# Web AI prospects — {stamp}\n", f"**{len(rows)}** companies\n"]
    for i, row in enumerate(rows, 1):
        lines.append(f"## {i}. {row.get('company_name', '')}\n")
        lines.append(build_prospect_notes(row) + "\n---\n")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return csv_path, md_path


def _write_latest(out_dir: Path, csv_path: Path) -> Path:
    latest = out_dir / "latest.csv"
    latest.write_text(csv_path.read_text(encoding="utf-8"), encoding="utf-8")
    return latest


def _group_by_company(rows: list[dict]) -> dict[str, list[dict]]:
    by: dict[str, list[dict]] = {}
    for row in rows:
        by.setdefault(_domain_key(row), []).append(row)
    return by


def _company_order(rows: list[dict]) -> list[str]:
    order: list[str] = []
    for row in rows:
        dk = _domain_key(row)
        if dk and dk not in order:
            order.append(dk)
    return order


def _contacts_with_phone(rows: list[dict]) -> list[dict]:
    return [r for r in rows if (r.get("contact_phone") or "").strip()]


def _needs_enrich(co_rows: list[dict], contacts_per_company: int) -> bool:
    return len(_contacts_with_phone(co_rows)) < contacts_per_company


def _company_base_fields(co_rows: list[dict]) -> dict:
    return {k: co_rows[0].get(k) for k in (
        "company_name", "company_domain", "industry", "estimated_revenue",
        "employee_count", "company_url", "country",
    )}


def _load_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _domain_from_url(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    parsed = urlparse(value if "://" in value else f"https://{value}")
    host = (parsed.netloc or parsed.path).split("/")[0].lower()
    return host[4:] if host.startswith("www.") else host


def _json_text(value: object) -> str:
    if isinstance(value, list):
        return "; ".join(str(v).strip() for v in value if str(v).strip())
    if value is None:
        return ""
    return str(value).strip()


def _load_hermes_json(path: Path) -> list[dict]:
    """Accept Hermes JSON as a row list or company objects with contacts[]."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        items = data.get("companies") or data.get("rows") or data.get("prospects") or []
    else:
        items = data
    if not isinstance(items, list):
        raise ValueError("Hermes input must be a JSON list or an object with companies/rows/prospects")

    rows: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        contacts = item.get("contacts")
        base = {
            "company_name": _json_text(item.get("company_name") or item.get("company")),
            "company_domain": _json_text(item.get("company_domain")),
            "industry": _json_text(item.get("industry")),
            "estimated_revenue": _json_text(item.get("estimated_revenue") or item.get("revenue")),
            "employee_count": _json_text(item.get("employee_count")),
            "company_url": _json_text(item.get("company_url") or item.get("website")),
            "country": _json_text(item.get("country") or "Finland"),
        }
        if not base["company_domain"]:
            base["company_domain"] = _domain_from_url(base["company_url"])

        contact_items = contacts if isinstance(contacts, list) and contacts else [item]
        for contact in contact_items:
            if not isinstance(contact, dict):
                continue
            row = {
                **base,
                "contact_name": _json_text(contact.get("contact_name") or contact.get("name")),
                "contact_title": _json_text(contact.get("contact_title") or contact.get("title")),
                "contact_phone": _json_text(contact.get("contact_phone") or contact.get("phone")),
                "phone_type": _json_text(contact.get("phone_type") or "unknown"),
                "phone_source_url": _json_text(contact.get("phone_source_url") or contact.get("source_url")),
                "contact_brief": _json_text(contact.get("contact_brief") or contact.get("brief")),
                "confidence": _json_text(contact.get("confidence") or item.get("confidence")),
                "evidence_urls": _json_text(contact.get("evidence_urls") or item.get("evidence_urls")),
            }
            rows.append(row)
    return _dedupe_contact_rows(rows)


def _write_rows_csv(rows: list[dict], path: Path) -> int:
    if not rows:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(dict.fromkeys(k for r in rows for k in r))
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            r = dict(row)
            if isinstance(r.get("evidence_urls"), list):
                r["evidence_urls"] = "; ".join(r["evidence_urls"])
            w.writerow(r)
    return len(rows)


def _print_filter_samples(rows: list[dict], *, label: str, n: int = 3) -> None:
    if not rows:
        return
    print(f"\nSample {label}:")
    for row in rows[:n]:
        extra = ""
        if label == "removed":
            reason = outreach_reject_reason(row)
            extra = f" [{reason}]" if reason else ""
        print(
            f"  {extra} {row.get('company_name', '')} | "
            f"{row.get('contact_name', '')} | {row.get('contact_phone', '')}"
        )


def _apply_outreach_filter(
    rows: list[dict],
    *,
    require_verification: bool = False,
) -> tuple[list[dict], FilterResult]:
    result = filter_outreach_ready(rows, require_verification=require_verification)
    print(format_filter_report(result, total=len(rows)))
    _print_filter_samples(result.kept, label="kept")
    _print_filter_samples(result.removed, label="removed")
    return result.kept, result


def cmd_verify(args: argparse.Namespace) -> int:
    src = Path(args.input)
    if not src.exists():
        log.error("Input not found: %s", src)
        return 1
    rows = _load_csv(src)
    verified = verify_rows(rows)
    ok = sum(1 for r in verified if r.get("verified_for_outreach") == "yes")
    log.info("Verified %d rows — %d OK for direct-call outreach", len(verified), ok)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.output_dir)
    stem = args.basename or f"web_prospects_verified_{stamp}"
    csv_path, md_path = _write_outputs(
        verified, out_dir, stamp, include_verify=True, basename=stem
    )

    if args.export_dashboard:
        dash_dir = Path(args.dashboard_dir)
        dash_dir.mkdir(parents=True, exist_ok=True)
        dash_name = args.dashboard_name or "google_ai_prospects_verified.csv"
        dash_path = dash_dir / dash_name
        cols = OUTPUT_COLUMNS + VERIFY_COLUMNS + ["country"]
        with dash_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for row in verified:
                r = dict(row)
                r.setdefault("country", args.country)
                if isinstance(r.get("evidence_urls"), list):
                    r["evidence_urls"] = "; ".join(r["evidence_urls"])
                w.writerow(r)
        print(f"Dashboard CSV → {dash_path}")

    print(f"Verified CSV → {csv_path}")
    print(f"Briefs → {md_path}")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    if getattr(args, "preset", None) == "aether":
        _apply_aether_preset(args)

    if not getattr(args, "product_context", ""):
        args.product_context = AETHER_DEFAULTS.product_context

    if not args.industry:
        log.error("--industry required (or use --preset aether)")
        return 1

    log.error(
        "LLM-backed search is disabled in this repo. Use discover-prh + "
        "enrich --scrape-first + verify instead."
    )
    return 2

    batch_size = min(15, max(4, args.batch_size))
    target_companies = args.limit
    contacts_per_company = max(1, args.contacts_per_company)
    require_phone = not args.websites_only
    industries = [s.strip() for s in args.industry.split(",") if s.strip()]
    if args.rotate_industries:
        industries = industries + list(AETHER_DEFAULTS.industry_rotation)

    log.info(
        "Google AI Search: %s | revenue %s | titles %s | companies %d | "
        "%d contacts/co | batch %d | phones=%s",
        args.country,
        args.revenue,
        args.titles,
        target_companies,
        contacts_per_company,
        batch_size,
        require_phone,
    )

    rows: list[dict] = []
    if args.continue_from:
        rows = _dedupe_contact_rows(_load_seed_csv(Path(args.continue_from)))
        log.info(
            "Continuing from %s — %d rows, %d companies",
            args.continue_from,
            len(rows),
            _company_count(rows),
        )

    all_notes: list[str] = []
    seen_names = _unique_company_names(rows)
    batch_num = 0
    empty_streak = 0
    prev_industry: str | None = None

    while _company_count(rows) < target_companies:
        batch_num += 1
        if batch_num > args.max_batches:
            log.warning("Stopped at max batches (%d)", args.max_batches)
            break

        have = _company_count(rows)
        need = min(batch_size, target_companies - have)
        industry = industries[(batch_num - 1) % len(industries)]
        if prev_industry is not None and industry != prev_industry:
            empty_streak = 0
        prev_industry = industry
        log.info(
            "Batch %d — %s — fetch %d companies (have %d/%d)",
            batch_num,
            industry,
            need,
            have,
            target_companies,
        )
        try:
            result = search_prospects(
                country=args.country,
                industry=industry,
                revenue=args.revenue,
                titles=args.titles,
                secondary_titles=getattr(args, "secondary_titles", "") or args.titles,
                limit=need,
                contacts_per_company=contacts_per_company,
                product_context=getattr(args, "product_context", AETHER_DEFAULTS.product_context),
                direct_phone_first=args.direct_phone_first,
                require_phone=require_phone,
                exclude_companies=seen_names,
            )
        except Exception as exc:
            log.error("Search failed: %s", exc)
            if batch_num < args.max_batches:
                wait = 30 * min(empty_streak + 1, 4)
                log.warning("Retry batch %d in %ss", batch_num, wait)
                time.sleep(wait)
                batch_num -= 1
                empty_streak += 1
                if empty_streak >= 6:
                    break
                continue
            if not rows:
                return 1
            break

        batch = result.get("companies") or []
        if not batch:
            note = result.get("search_notes", "empty batch")
            all_notes.append(f"batch {batch_num} ({industry}): {note}")
            log.warning("Batch %d returned 0", batch_num)
            empty_streak += 1
            if empty_streak >= 6:
                break
            time.sleep(20)
            continue
        empty_streak = 0

        rows = _dedupe_contact_rows(rows + batch)
        seen_names = _unique_company_names(rows)
        all_notes.append(result.get("search_notes", ""))
        if _company_count(rows) >= target_companies:
            break
        time.sleep(args.batch_pause)

    rows = _trim_to_company_limit(rows, target_companies, contacts_per_company)

    need_deep = args.deep_pass or args.direct_phone_first or contacts_per_company > 1
    max_deep = getattr(args, "max_deep_passes", 0) or 0
    scrape_first = getattr(args, "scrape_first", False)
    preserve = getattr(args, "preserve_companies", False) or bool(args.continue_from)
    if need_deep and rows:
        by_company: dict[str, list[dict]] = {}
        for row in rows:
            dk = _domain_key(row)
            by_company.setdefault(dk, []).append(row)
        enriched_rows: list[dict] = []
        deep_done = 0
        for i, (dk, co_rows) in enumerate(by_company.items(), 1):
            name = co_rows[0].get("company_name", "")
            with_phone = [
                r
                for r in co_rows
                if (r.get("contact_phone") or "").strip()
                and (
                    not args.direct_phone_first
                    or (r.get("phone_type") or "").lower() in {"mobile", "direct"}
                )
            ]
            if len(co_rows) >= contacts_per_company and len(with_phone) >= 1 and not args.deep_pass:
                enriched_rows.extend(co_rows)
                continue
            if max_deep and deep_done >= max_deep:
                enriched_rows.extend(co_rows)
                continue
            log.info(
                "Deep pass [%d/%d] %s (%d/%d contacts)",
                i,
                len(by_company),
                name,
                len(co_rows),
                contacts_per_company,
            )
            extra: list[dict] = []
            if scrape_first and len(co_rows) < contacts_per_company:
                scraped = scrape_phones_from_site(
                    company_name=name,
                    domain=co_rows[0].get("company_domain") or "",
                    company_url=co_rows[0].get("company_url"),
                )
                for s in scraped:
                    extra.append({**s, "contact_name": s.get("contact_name") or ""})
            no_llm = getattr(args, "no_llm", False)
            if (
                not no_llm
                and len(co_rows) + len(extra) < contacts_per_company
            ):
                try:
                    deep_done += 1
                    extra.extend(
                        enrich_contacts(
                            company_name=name,
                            domain=co_rows[0].get("company_domain") or "",
                            country=args.country,
                            industry=args.industry,
                            titles=args.titles,
                            secondary_titles=getattr(args, "secondary_titles", "") or args.titles,
                            existing_contacts=co_rows + extra,
                            need_contacts=contacts_per_company - len(co_rows) - len(extra),
                        )
                    )
                except Exception as exc:
                    log.warning("Deep pass failed for %s: %s", name, exc)
            base = {k: co_rows[0].get(k) for k in (
                "company_name", "company_domain", "industry", "estimated_revenue",
                "employee_count", "company_url",
            )}
            extra_rows = [{**base, **c} for c in extra if isinstance(c, dict)]
            merged = _dedupe_contact_rows(co_rows + extra_rows)
            enriched_rows.extend(merged[:contacts_per_company])
        rows = enriched_rows
        if not preserve:
            rows = filter_companies(
                enriched_rows,
                direct_phone_first=args.direct_phone_first,
                require_phone=require_phone,
                contacts_per_company=contacts_per_company,
            )
        rows = _trim_to_company_limit(rows, target_companies, contacts_per_company)

    if args.verify:
        rows = verify_rows(rows)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.output_dir)
    stem = getattr(args, "basename", None) or f"web_prospects_{stamp}"
    csv_path, md_path = _write_outputs(
        rows, out_dir, stamp, include_verify=args.verify, basename=stem
    )
    latest = _write_latest(out_dir, csv_path)

    notes = " | ".join(n for n in all_notes if n)
    meta_path = out_dir / f"web_prospects_{stamp}_meta.json"
    meta_path.write_text(
        json.dumps(
            {
                "search_notes": notes,
                "contact_rows": len(rows),
                "company_count": _company_count(rows),
                "batches": batch_num,
                "criteria": {
                    "preset": getattr(args, "preset", None),
                    "country": args.country,
                    "industry": args.industry,
                    "revenue": args.revenue,
                    "titles": args.titles,
                    "secondary_titles": getattr(args, "secondary_titles", ""),
                    "company_limit": args.limit,
                    "contacts_per_company": contacts_per_company,
                    "websites_only": args.websites_only,
                    "direct_phone_first": args.direct_phone_first,
                    "batch_size": batch_size,
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    label = "with websites" if args.websites_only else "with phones"
    print(
        f"Found {_company_count(rows)} companies, {len(rows)} contact rows ({label})"
    )
    if notes:
        print(f"Notes: {notes[:300]}")
    print(f"CSV → {csv_path}")
    print(f"Latest → {latest}")
    print(f"Briefs → {md_path}")
    return 0


def cmd_discover_prh(args: argparse.Namespace) -> int:
    log.info("PRH discover — target %d companies (free, no LLM)", args.limit)
    if args.aether:
        rows = discover_aether_industrial(max_companies=args.limit)
    else:
        rows = discover_prh(
            business_line=args.business_line,
            location=args.location,
            name_contains=args.name,
            max_companies=args.limit,
        )
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.output_dir)
    stem = args.basename or f"prh_companies_{stamp}"
    csv_path, md_path = _write_outputs(rows, out_dir, stamp, basename=stem)
    latest = _write_latest(out_dir, csv_path)
    print(f"PRH: {len(rows)} companies → {csv_path}")
    print(f"Latest → {latest}")
    return 0


def cmd_enrich(args: argparse.Namespace) -> int:
    src = Path(args.input)
    if not src.exists():
        log.error("Input not found: %s", src)
        return 1

    rows = _dedupe_contact_rows(_load_csv(src))
    by_company = _group_by_company(rows)
    order = _company_order(rows)
    contacts_per_company = max(1, args.contacts_per_company)
    llm_used = 0
    enriched = 0
    scrape_used = 0

    candidates = [
        (dk, by_company[dk])
        for dk in order
        if _needs_enrich(by_company[dk], contacts_per_company)
    ]
    if args.limit:
        candidates = candidates[: args.limit]

    log.info(
        "Enrich %d companies — scrape-first=%s max-llm=%d contacts/co=%d preserve=%s",
        len(candidates),
        args.scrape_first,
        args.max_llm_calls,
        contacts_per_company,
        args.preserve_companies,
    )

    for i, (dk, co_rows) in enumerate(candidates, 1):
        name = co_rows[0].get("company_name", dk)
        working = list(co_rows)
        log.info("[%d/%d] %s", i, len(candidates), name)

        if args.scrape_first and _needs_enrich(working, contacts_per_company):
            scraped = scrape_phones_from_site(
                company_name=name,
                domain=co_rows[0].get("company_domain") or "",
                company_url=co_rows[0].get("company_url"),
            )
            if scraped:
                scrape_used += 1
                base = _company_base_fields(co_rows)
                working = _dedupe_contact_rows(
                    working + [{**base, **s} for s in scraped]
                )

        if (
            args.use_llm
            and _needs_enrich(working, contacts_per_company)
            and llm_used < args.max_llm_calls
        ):
            try:
                extra = enrich_contacts(
                    company_name=name,
                    domain=co_rows[0].get("company_domain") or "",
                    country=args.country,
                    industry=args.industry or AETHER_DEFAULTS.industry,
                    titles=args.titles,
                    secondary_titles=args.secondary_titles or args.titles,
                    existing_contacts=working,
                    need_contacts=contacts_per_company - len(_contacts_with_phone(working)),
                )
                llm_used += 1
                base = _company_base_fields(co_rows)
                working = _dedupe_contact_rows(
                    working + [{**base, **c} for c in extra if isinstance(c, dict)]
                )
            except Exception as exc:
                log.warning("LLM enrich failed for %s: %s", name, exc)

        if working != co_rows:
            enriched += 1
        by_company[dk] = working

    out_rows: list[dict] = []
    for dk in order:
        co_rows = by_company.get(dk, [])
        if args.preserve_companies:
            out_rows.extend(_dedupe_contact_rows(co_rows))
        else:
            trimmed = _sort_by_phone_priority(co_rows)[:contacts_per_company]
            out_rows.extend(trimmed)

    if args.verify:
        out_rows = verify_rows(out_rows)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.output_dir)
    stem = args.basename or f"web_prospects_enriched_{stamp}"
    csv_path, md_path = _write_outputs(
        out_rows, out_dir, stamp, include_verify=args.verify, basename=stem
    )
    latest = _write_latest(out_dir, csv_path)

    if args.export_dashboard:
        dash_dir = Path(args.dashboard_dir)
        dash_dir.mkdir(parents=True, exist_ok=True)
        dash_path = dash_dir / (args.dashboard_name or "google_ai_prospects_verified.csv")
        cols = OUTPUT_COLUMNS + VERIFY_COLUMNS + ["country"]
        with dash_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for row in out_rows:
                r = dict(row)
                r.setdefault("country", args.country)
                if isinstance(r.get("evidence_urls"), list):
                    r["evidence_urls"] = "; ".join(r["evidence_urls"])
                w.writerow(r)
        print(f"Dashboard → {dash_path}")

    phones = sum(1 for r in out_rows if (r.get("contact_phone") or "").strip())
    print(
        f"Enriched {enriched} companies | {len(out_rows)} rows | "
        f"{_company_count(out_rows)} companies | {phones} with phones | "
        f"scrape={scrape_used} llm={llm_used}"
    )
    print(f"CSV → {csv_path}")
    print(f"Latest → {latest}")
    return 0


def cmd_export_pipeline(args: argparse.Namespace) -> int:
    src = Path(args.input)
    if not src.exists():
        log.error("Input not found: %s", src)
        return 1
    input_rows = _load_csv(src)
    if not input_rows:
        log.error("No rows in %s", src)
        return 1
    rows = input_rows
    if args.verify and not rows[0].get("phone_verification_status"):
        rows = verify_rows(rows)
    out_dir = Path(args.output_dir)
    if args.qualified_only:
        rows, _ = _apply_outreach_filter(
            rows,
            require_verification=args.require_verification,
        )
        qualified_path = out_dir / "latest_qualified.csv"
        qualified_count = _write_rows_csv(rows, qualified_path)
        print(f"Qualified CSV → {qualified_path} ({qualified_count} rows)")
    stem = args.basename or "notion_pipeline"
    out_path = out_dir / f"{stem}.csv"
    company_path, exported, skipped = write_pipeline_csv(rows, out_path, source=args.source)
    contacts_path = out_dir / "latest_with_contacts.csv"
    contacts_count = write_contacts_only_csv(rows, contacts_path)
    print(f"Input: {len(input_rows)} rows total")
    if args.qualified_only:
        print(f"After outreach filter: {len(rows)} rows")
    print(f"Exported {exported} rows, skipped {skipped} (no contact)")
    print(f"Contacts-only CSV → {contacts_path} ({contacts_count} rows)")
    print(f"CRM pipeline CSV → {company_path}")
    print(f"Tab-separated TSV → {out_dir / 'notion_pipeline.tsv'}")
    print(f"Detailed CSV → {out_dir / 'notion_pipeline_detailed.csv'}")
    return 0


def cmd_hermes_search(args: argparse.Namespace) -> int:
    """Hermes Google Search prospecting — free web search + scrape (default)."""
    import os

    from hermes_search import HermesSearchSpec, run_hermes_search

    if args.provider == "gemini":
        log.info("hermes-search --provider gemini → LLM grounded search (cli.py search)")
        gemini_args = argparse.Namespace(
            preset=None,
            country=args.country,
            industry=",".join(args.industries.split(",")),
            revenue=args.revenue,
            titles=args.titles,
            secondary_titles=args.secondary_titles,
            limit=args.limit,
            contacts_per_company=args.contacts_per_company,
            batch_size=args.batch_size,
            max_batches=args.max_batches,
            batch_pause=args.batch_pause,
            websites_only=False,
            rotate_industries=True,
            continue_from=args.continue_from,
            verify=args.verify,
            direct_phone_first=False,
            deep_pass=False,
            max_deep_passes=0,
            preserve_companies=True,
            scrape_first=True,
            no_llm=False,
            product_context="",
            basename=args.basename,
            output_dir=args.output_dir,
        )
        return cmd_search(gemini_args)

    industries = [p.strip() for p in args.industries.split(",") if p.strip()]
    titles = [p.strip() for p in args.titles.split(",") if p.strip()]
    spec = HermesSearchSpec(
        country=args.country,
        industries=industries,
        revenue=args.revenue,
        titles=titles,
        company_limit=args.limit,
        contacts_per_company=args.contacts_per_company,
        provider=args.provider,
    )
    seed = _load_seed_csv(Path(args.continue_from)) if args.continue_from else []
    rows, meta = run_hermes_search(
        spec,
        serper_api_key=os.environ.get("GAP_SERPER_API_KEY", ""),
        continue_from=seed,
    )
    if not rows:
        log.warning(
            "Hermes search returned 0 rows — try --provider serper with GAP_SERPER_API_KEY "
            "or widen --industries"
        )

    if args.verify:
        rows = verify_rows(rows)

    if args.qualified_only:
        rows, _ = _apply_outreach_filter(
            rows,
            require_verification=args.require_verification,
        )

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.output_dir)
    stem = args.basename or f"hermes_google_search_{stamp}"
    csv_path, md_path = _write_outputs(
        rows,
        out_dir,
        stamp,
        include_verify=args.verify,
        basename=stem,
    )
    meta_path = out_dir / f"{stem}_meta.json"
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    latest = _write_latest(out_dir, csv_path)

    companies = _company_count(rows)
    phones = sum(1 for r in rows if (r.get("contact_phone") or "").strip())
    print(
        f"Hermes search: {companies} companies | {len(rows)} contact rows | "
        f"{phones} with phones | provider={args.provider} | queries={meta.get('queries_run', 0)}"
    )
    print(f"CSV → {csv_path}")
    print(f"Briefs → {md_path}")
    print(f"Meta → {meta_path}")
    print(f"Latest → {latest}")

    if args.export_pipeline or args.sync_notion:
        pipeline_path = out_dir / (args.pipeline_basename or "notion_pipeline")
        if pipeline_path.suffix != ".csv":
            pipeline_path = pipeline_path.with_suffix(".csv")
        company_path, exported, skipped = write_pipeline_csv(
            rows,
            pipeline_path,
            source=args.source,
        )
        print(f"Pipeline CSV → {company_path} ({exported} exported, {skipped} skipped)")
        if args.sync_notion:
            token, db_id = load_notion_config()
            if not token or not db_id:
                log.error("Set NOTION_API_KEY and NOTION_DATABASE_ID before --sync-notion")
                return 1
            stats = sync_pipeline_to_notion(
                normalize_sync_rows(_load_csv(company_path)),
                database_id=db_id,
                token=token,
                state_path=Path(args.state_file),
            )
            print(
                f"Notion sync: {stats['created']} created, {stats['updated']} updated, "
                f"{stats['skipped']} skipped, {stats.get('skipped_no_contact', 0)} skipped (no contact), "
                f"{stats['errors']} errors"
            )
            return 0 if stats["errors"] == 0 else 1
    return 0


def cmd_import_hermes(args: argparse.Namespace) -> int:
    src = Path(args.input)
    if not src.exists():
        log.error("Input not found: %s", src)
        return 1
    try:
        rows = _load_hermes_json(src)
    except Exception as exc:
        log.error("Could not read Hermes JSON: %s", exc)
        return 1
    if not rows:
        log.error("No prospect rows in %s", src)
        return 1
    rows = verify_rows(rows) if args.verify else rows

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.output_dir)
    stem = args.basename or f"hermes_google_prospects_{stamp}"
    csv_path, md_path = _write_outputs(
        rows,
        out_dir,
        stamp,
        include_verify=args.verify,
        basename=stem,
    )
    latest = _write_latest(out_dir, csv_path)
    print(f"Imported {len(rows)} Hermes prospect rows")
    print(f"CSV → {csv_path}")
    print(f"Latest → {latest}")
    print(f"Briefs → {md_path}")

    if args.export_pipeline or args.sync_notion:
        pipeline_path = out_dir / (args.pipeline_basename or "notion_pipeline")
        if pipeline_path.suffix != ".csv":
            pipeline_path = pipeline_path.with_suffix(".csv")
        company_path, exported, skipped = write_pipeline_csv(
            rows,
            pipeline_path,
            source=args.source,
        )
        print(f"Pipeline CSV → {company_path} ({exported} exported, {skipped} skipped)")
        if args.sync_notion:
            token, db_id = load_notion_config()
            if not token or not db_id:
                log.error("Set NOTION_API_KEY and NOTION_DATABASE_ID before --sync-notion")
                return 1
            stats = sync_pipeline_to_notion(
                normalize_sync_rows(_load_csv(company_path)),
                database_id=db_id,
                token=token,
                state_path=Path(args.state_file),
            )
            print(
                f"Notion sync: {stats['created']} created, {stats['updated']} updated, "
                f"{stats['skipped']} skipped, {stats.get('skipped_no_contact', 0)} skipped (no contact), "
                f"{stats['errors']} errors"
            )
            return 0 if stats["errors"] == 0 else 1
    return 0


def cmd_filter(args: argparse.Namespace) -> int:
    src = Path(args.input)
    if not src.exists():
        log.error("Input not found: %s", src)
        return 1
    rows = _load_csv(src)
    if not rows:
        log.error("No rows in %s", src)
        return 1
    if args.verify and not rows[0].get("phone_verification_status"):
        rows = verify_rows(rows)
    kept, _ = _apply_outreach_filter(
        rows,
        require_verification=args.require_verification,
    )
    out_dir = Path(args.output_dir)
    out_path = out_dir / (args.output or "latest_qualified.csv")
    count = _write_rows_csv(kept, out_path)
    print(f"Qualified CSV → {out_path} ({count} rows)")
    return 0


def cmd_sync_notion(args: argparse.Namespace) -> int:
    src = Path(args.input)
    if not src.exists():
        log.error("Input not found: %s", src)
        return 1
    token = args.notion_token or load_notion_config()[0]
    db_id = args.database_id or load_notion_config()[1]
    if not token:
        log.error(
            "Set NOTION_API_KEY in .env — create integration at "
            "https://www.notion.so/my-integrations and paste the secret"
        )
        return 1
    if not db_id:
        log.error("Set NOTION_DATABASE_ID in .env (Pipeline database UUID)")
        return 1
    import csv as csv_mod

    with src.open(encoding="utf-8-sig", newline="") as f:
        rows = normalize_sync_rows(list(csv_mod.DictReader(f)))
    if not rows:
        log.error("No company rows in %s", src)
        return 1
    stats = sync_pipeline_to_notion(
        rows,
        database_id=db_id,
        token=token,
        state_path=Path(args.state_file),
    )
    skipped_no_contact = stats.get("skipped_no_contact", 0)
    print(
        f"Notion sync: {stats['created']} created, {stats['updated']} updated, "
        f"{stats['skipped']} skipped, {skipped_no_contact} skipped (no contact), "
        f"{stats['errors']} errors"
    )
    return 0 if stats["errors"] == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Find companies + decision makers via free/public-web workflows. No lead lists."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("search", help="Discover prospects from web only")
    p.add_argument(
        "--preset",
        choices=["aether"],
        help="Aether Applied ICP defaults: 200 FI industrial cos, 2 callable contacts, phones",
    )
    p.add_argument("--country", default="Finland", help="e.g. Finland, Sweden")
    p.add_argument("--industry", default=None, help="e.g. teollisuusautomaatio (required unless --preset aether)")
    p.add_argument("--revenue", default="1M - 5B EUR", help="e.g. '1M - 5B EUR', '10-50M EUR'")
    p.add_argument(
        "--titles",
        default="Head of R&D, CTO, teknologiajohtaja, tutkimusjohtaja, innovaatiojohtaja",
        help="Primary ICP titles (comma-separated)",
    )
    p.add_argument(
        "--secondary-titles",
        default="",
        help="Secondary titles for 2nd contact (CEO, COO, etc.)",
    )
    p.add_argument("--limit", type=int, default=20, help="Target number of companies")
    p.add_argument(
        "--contacts-per-company",
        type=int,
        default=1,
        help="Callable ICP people per company (default 2 with --preset aether)",
    )
    p.add_argument("--batch-size", type=int, default=8, help="Companies per API call")
    p.add_argument("--max-batches", type=int, default=30, help="Max API calls per run")
    p.add_argument("--batch-pause", type=int, default=5, help="Seconds between batches")
    p.add_argument(
        "--websites-only",
        action="store_true",
        help="Require website; phone optional (best for large lists)",
    )
    p.add_argument(
        "--rotate-industries",
        action="store_true",
        help="Rotate sub-industry per batch for broader coverage",
    )
    p.add_argument("--continue-from", metavar="CSV", help="Seed from existing CSV")
    p.add_argument("--verify", action="store_true", help="Run phone verify after search")
    p.add_argument(
        "--direct-phone-first",
        action="store_true",
        help="Prioritize direct/mobile phones for target title-holders; auto deep-pass",
    )
    p.add_argument("--deep-pass", action="store_true", help="Re-search contacts missing direct phones")
    p.add_argument(
        "--max-deep-passes",
        type=int,
        default=0,
        help="Cap LLM deep-pass calls per search run (0 = unlimited)",
    )
    p.add_argument(
        "--preserve-companies",
        action="store_true",
        help="Keep all companies when deep-pass/enrich fails (auto with --continue-from)",
    )
    p.add_argument(
        "--scrape-first",
        action="store_true",
        help="Scrape company websites before LLM deep-pass",
    )
    p.add_argument(
        "--no-llm",
        action="store_true",
        help="Skip LLM deep-pass (scrape-only when combined with --scrape-first)",
    )
    p.add_argument(
        "--product-context",
        default="",
        help="Product/vertical context for search prompt (default: Aether industrial)",
    )
    p.add_argument("--basename", help="Output CSV stem (default web_prospects_TIMESTAMP)")
    p.add_argument("--output-dir", default="output")
    p.set_defaults(func=cmd_search)

    d = sub.add_parser("discover-prh", help="Free Finnish companies via PRH API (no LLM)")
    d.add_argument("--limit", type=int, default=200)
    d.add_argument("--aether", action="store_true", help="Rotate industrial TOL codes")
    d.add_argument("--business-line", help="PRH TOL code filter")
    d.add_argument("--location", help="City filter")
    d.add_argument("--name", help="Company name contains")
    d.add_argument("--output-dir", default="output")
    d.add_argument("--basename", help="Output CSV stem")
    d.set_defaults(func=cmd_discover_prh)

    e = sub.add_parser("enrich", help="Capped daily enrich: scrape first, optional LLM")
    e.add_argument("--input", "-i", required=True, help="Input CSV")
    e.add_argument("--limit", type=int, default=25, help="Max companies to enrich this run")
    e.add_argument("--contacts-per-company", type=int, default=2)
    e.add_argument("--scrape-first", action="store_true", default=True)
    e.add_argument("--no-scrape-first", action="store_false", dest="scrape_first")
    e.add_argument("--use-llm", action="store_true", help="Allow LLM contact lookup")
    e.add_argument("--max-llm-calls", type=int, default=0)
    e.add_argument("--preserve-companies", action="store_true", default=True)
    e.add_argument("--no-preserve-companies", action="store_false", dest="preserve_companies")
    e.add_argument("--country", default="Finland")
    e.add_argument("--industry", default="")
    e.add_argument("--titles", default=AETHER_DEFAULTS.titles)
    e.add_argument("--secondary-titles", default=AETHER_DEFAULTS.secondary_titles)
    e.add_argument("--verify", action="store_true")
    e.add_argument("--export-dashboard", action="store_true")
    e.add_argument("--dashboard-dir", default=str(DASHBOARD_DEFAULT))
    e.add_argument("--dashboard-name", default="google_ai_prospects_verified.csv")
    e.add_argument("--output-dir", default="output")
    e.add_argument("--basename", help="Output stem")
    e.set_defaults(func=cmd_enrich)

    x = sub.add_parser("export-pipeline", help="Organized CSV for Notion / CRM review")
    x.add_argument("--input", "-i", default="output/latest.csv")
    x.add_argument("--output-dir", default="output")
    x.add_argument("--basename", default="notion_pipeline")
    x.add_argument("--source", default="Outbound")
    x.add_argument("--verify", action="store_true")
    x.add_argument(
        "--qualified-only",
        action="store_true",
        help="Keep outreach-ready contacts only; write output/latest_qualified.csv",
    )
    x.add_argument(
        "--require-verification",
        action="store_true",
        help="Require verified_for_outreach or allowed phone_verification_status",
    )
    x.set_defaults(func=cmd_export_pipeline)

    hs = sub.add_parser(
        "hermes-search",
        help="Hermes Google Search prospecting (free default; gemini optional)",
    )
    hs.add_argument("--country", default="Finland")
    hs.add_argument(
        "--industries",
        default="energy,manufacturing,mining,proptech",
        help="Comma-separated industries (English or Finnish)",
    )
    hs.add_argument("--revenue", default="10M - 5B EUR")
    hs.add_argument(
        "--titles",
        default="Engineering Manager,CTO,Head of R&D,VP Engineering,CEO",
        help="Comma-separated target titles",
    )
    hs.add_argument("--secondary-titles", default="CEO,toimitusjohtaja")
    hs.add_argument("--limit", type=int, default=20, help="Target company count")
    hs.add_argument("--contacts-per-company", type=int, default=2)
    hs.add_argument(
        "--provider",
        choices=["google", "serper", "gemini"],
        default="google",
        help="google=DDG site:.fi (free); serper=Google via API; gemini=LLM grounded search",
    )
    hs.add_argument("--batch-size", type=int, default=8, help="Gemini mode only")
    hs.add_argument("--max-batches", type=int, default=30, help="Gemini mode only")
    hs.add_argument("--batch-pause", type=int, default=5, help="Gemini mode only")
    hs.add_argument("--continue-from", metavar="CSV", help="Seed from existing CSV")
    hs.add_argument("--verify", action="store_true", default=True)
    hs.add_argument("--no-verify", action="store_false", dest="verify")
    hs.add_argument(
        "--qualified-only",
        action="store_true",
        help="Filter to outreach-ready contacts before export",
    )
    hs.add_argument("--require-verification", action="store_true")
    hs.add_argument("--export-pipeline", action="store_true", help="Write notion_pipeline.csv")
    hs.add_argument("--sync-notion", action="store_true", help="Push pipeline rows to Notion")
    hs.add_argument("--pipeline-basename", default="notion_pipeline")
    hs.add_argument("--source", default="ICP Search")
    hs.add_argument("--state-file", default="output/notion_sync_state.json")
    hs.add_argument("--basename", help="Output stem (default hermes_google_search_TIMESTAMP)")
    hs.add_argument("--output-dir", default="output")
    hs.set_defaults(func=cmd_hermes_search)

    h = sub.add_parser("import-hermes", help="Import Hermes-researched prospect JSON")
    h.add_argument("--input", "-i", required=True, help="Hermes JSON file")
    h.add_argument("--output-dir", default="output")
    h.add_argument("--basename", help="Output stem")
    h.add_argument("--verify", action="store_true", help="Run phone verification")
    h.add_argument("--export-pipeline", action="store_true", help="Write Notion pipeline CSVs")
    h.add_argument("--sync-notion", action="store_true", help="Sync exported pipeline to Notion")
    h.add_argument("--pipeline-basename", default="notion_pipeline")
    h.add_argument("--source", default="ICP Search")
    h.add_argument("--state-file", default="output/notion_sync_state.json")
    h.set_defaults(func=cmd_import_hermes)

    f = sub.add_parser("filter", help="Filter CSV to outreach-ready contacts only")
    f.add_argument("--input", "-i", default="output/latest.csv")
    f.add_argument("--output-dir", default="output")
    f.add_argument("--output", "-o", help="Output filename (default latest_qualified.csv)")
    f.add_argument("--verify", action="store_true", help="Run phone verify before filtering")
    f.add_argument(
        "--require-verification",
        action="store_true",
        help="Require verified_for_outreach or allowed phone_verification_status",
    )
    f.set_defaults(func=cmd_filter)

    n = sub.add_parser("sync-notion", help="Push pipeline CSV rows to Notion database")
    n.add_argument("--input", "-i", default="output/notion_pipeline.csv")
    n.add_argument("--database-id", help="Notion database ID (or NOTION_DATABASE_ID in .env)")
    n.add_argument("--notion-token", help="Notion integration token (or NOTION_API_KEY)")
    n.add_argument("--state-file", default="output/notion_sync_state.json")
    n.set_defaults(func=cmd_sync_notion)

    v = sub.add_parser("verify", help="Second-pass phone verification before outreach")
    v.add_argument("--input", "-i", required=True, help="Input web_prospects CSV")
    v.add_argument("--output-dir", default="output")
    v.add_argument("--basename", help="Output stem (default web_prospects_verified_TIMESTAMP)")
    v.add_argument("--country", default="Finland", help="Country column for dashboard export")
    v.add_argument(
        "--export-dashboard",
        action="store_true",
        help="Also write CSV to aether-applied-leads/data/",
    )
    v.add_argument("--dashboard-dir", default=str(DASHBOARD_DEFAULT))
    v.add_argument("--dashboard-name", default="google_ai_prospects_verified.csv")
    v.set_defaults(func=cmd_verify)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
