#!/usr/bin/env python3
"""CLI: web-only prospecting via Gemini Google Search (tekoälyhaku)."""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from search_agent import (
    enrich_contact,
    filter_companies,
    search_prospects,
    _sort_by_phone_priority,
)

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


def _write_outputs(rows: list[dict], out_dir: Path, stamp: str) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"web_prospects_{stamp}.csv"
    md_path = out_dir / f"web_prospects_{stamp}.md"

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            r = dict(row)
            if isinstance(r.get("evidence_urls"), list):
                r["evidence_urls"] = "; ".join(r["evidence_urls"])
            w.writerow(r)

    lines = [f"# Web AI prospects — {stamp}\n", f"**{len(rows)}** companies (Google Search grounding only)\n"]
    for i, row in enumerate(rows, 1):
        brief = (row.get("contact_brief") or "").strip()
        lines.append(f"## {i}. {row.get('company_name', '')}\n")
        if brief:
            lines.append(brief + "\n")
        lines.append(
            f"\n**{row.get('contact_name', '—')}** — {row.get('contact_title', '—')}\n\n"
            f"Puhelin: {row.get('contact_phone') or '—'} ({row.get('phone_type', 'unknown')})\n\n"
            f"Lähde: {row.get('phone_source_url') or '—'}\n\n---\n"
        )
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return csv_path, md_path


def cmd_search(args: argparse.Namespace) -> int:
    log.info(
        "Google AI Search: %s | %s | revenue %s | titles %s | limit %d | direct-phone-first=%s",
        args.country,
        args.industry,
        args.revenue,
        args.titles,
        args.limit,
        args.direct_phone_first,
    )
    try:
        result = search_prospects(
            country=args.country,
            industry=args.industry,
            revenue=args.revenue,
            titles=args.titles,
            limit=args.limit,
            direct_phone_first=args.direct_phone_first,
        )
    except Exception as exc:
        log.error("Search failed: %s", exc)
        return 1

    companies = result.get("companies") or []
    need_deep = args.deep_pass or args.direct_phone_first
    if need_deep and companies:
        for i, row in enumerate(companies, 1):
            pt = (row.get("phone_type") or "").lower()
            if args.direct_phone_first and pt in {"mobile", "direct"} and row.get("contact_phone"):
                continue
            if not args.direct_phone_first and row.get("contact_phone") and float(row.get("confidence") or 0) >= 0.6:
                continue
            name = row.get("company_name", "")
            log.info("Deep pass [%d/%d] %s", i, len(companies), name)
            extra = enrich_contact(
                company_name=name,
                domain=row.get("company_domain") or "",
                country=args.country,
                industry=args.industry,
                titles=args.titles,
            )
            if extra:
                row.update({k: v for k, v in extra.items() if v})
        companies = filter_companies(companies, direct_phone_first=args.direct_phone_first)
        companies = _sort_by_phone_priority(companies)[: args.limit]

    if args.direct_phone_first and not companies:
        log.warning(
            "No direct/mobile phones found for target titles. "
            "Re-run without --direct-phone-first to include switchboard numbers."
        )

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.output_dir)
    csv_path, md_path = _write_outputs(companies, out_dir, stamp)

    notes = result.get("search_notes", "")
    meta_path = out_dir / f"web_prospects_{stamp}_meta.json"
    meta_path.write_text(
        json.dumps(
            {
                "search_notes": notes,
                "count": len(companies),
                "criteria": {
                    "country": args.country,
                    "industry": args.industry,
                    "revenue": args.revenue,
                    "titles": args.titles,
                    "limit": args.limit,
                    "direct_phone_first": args.direct_phone_first,
                    "deep_pass": args.deep_pass,
                },
                "raw_response": result.get("_raw_response", "")[:20000],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"Found {len(companies)} companies with phones")
    if notes:
        print(f"Notes: {notes[:300]}")
    print(f"CSV → {csv_path}")
    print(f"Briefs → {md_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Find companies + decision makers via Gemini Google Search (tekoälyhaku). No lead lists."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("search", help="Discover prospects from web only")
    p.add_argument("--country", default="Finland", help="e.g. Finland, Sweden")
    p.add_argument("--industry", required=True, help="e.g. 'teollisuusautomaatio', 'district heating'")
    p.add_argument("--revenue", default="1M - 5B EUR", help="e.g. '1M - 5B EUR', '10-50M EUR', 'yli 20M€'")
    p.add_argument(
        "--titles",
        default="Head of R&D, CTO, teknologiajohtaja, tutkimusjohtaja, innovaatiojohtaja",
        help="Comma-separated target titles",
    )
    p.add_argument("--limit", type=int, default=20)
    p.add_argument(
        "--direct-phone-first",
        action="store_true",
        help="Prioritize direct/mobile phones for target title-holders; auto deep-pass",
    )
    p.add_argument("--deep-pass", action="store_true", help="Re-search contacts missing direct phones")
    p.add_argument("--output-dir", default="output")
    p.set_defaults(func=cmd_search)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
