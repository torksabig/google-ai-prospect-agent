#!/usr/bin/env python3
"""Verify Notion database schema against notion_property_map.json / NOTION_PROPS."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from notion_pipeline import NOTION_PROPS, load_property_map  # noqa: E402

load_dotenv(ROOT / ".env")

NOTION_VERSION = "2022-06-28"

# Expected types when using full NOTION_PROPS template (no custom map)
DEFAULT_TYPES = {
    "Lead Key": "rich_text",
    "Name": "title",
    "Company name": "title",
    "Account owner": "people",
    "Deal stage": "select",
    "Email": "email",
    "Phone number": "phone_number",
    "Notes": "rich_text",
    "Date Added": "date",
    "Lead source": "multi_select",
    "Company": "rich_text",
    "Contact Name": "rich_text",
    "Title": "rich_text",
    "Phone": "phone_number",
    "Industry": "rich_text",
    "Revenue": "rich_text",
    "Country": "rich_text",
    "Website": "url",
    "Brief": "rich_text",
    "Stage": "select",
    "Source": "select",
    "Phone Status": "rich_text",
    "Outreach OK": "checkbox",
}


def _integration_name(token: str) -> str | None:
    r = httpx.get(
        "https://api.notion.com/v1/users/me",
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
        },
        timeout=30.0,
    )
    if r.status_code != 200:
        return None
    data = r.json()
    return data.get("name")


def _expected_type(key: str, col: str, pmap: dict) -> str | None:
    type_overrides = pmap.get("type_overrides") or {}
    if key in type_overrides:
        return type_overrides[key]
    return DEFAULT_TYPES.get(col)


def main() -> int:
    token = os.environ.get("NOTION_API_KEY", "").strip()
    db_id = os.environ.get("NOTION_DATABASE_ID", "").strip()
    if not token:
        print(
            "Missing NOTION_API_KEY in .env\n"
            "  1. https://www.notion.so/my-integrations → New integration\n"
            "  2. Copy Internal Integration Secret\n"
            "  3. Paste into NOTION_API_KEY= in .env\n"
            "  4. In Notion Pipeline DB → ••• → Connections → add your integration"
        )
        return 1
    if not db_id:
        print("Missing NOTION_DATABASE_ID in .env")
        return 1

    pmap = load_property_map()
    map_path = ROOT / "notion_property_map.json"
    using_map = map_path.exists()

    integration = _integration_name(token)
    if integration:
        print(f"Integration name (search in Notion Connections): {integration!r}\n")

    r = httpx.get(
        f"https://api.notion.com/v1/databases/{db_id}",
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
        },
        timeout=30.0,
    )
    if r.status_code != 200:
        print(f"Notion API error {r.status_code}: {r.text[:500]}")
        if r.status_code == 404:
            int_label = repr(integration) if integration else "your integration"
            print(
                "\nDatabase not shared with this integration yet.\n"
                "  1. Open Pipeline DB as full page\n"
                "  2. ••• → Connections → add"
                f" {int_label}\n"
                "  3. python scripts/notion_list_databases.py"
            )
        return 1

    data = r.json()
    title = "".join(t.get("plain_text", "") for t in (data.get("title") or []))
    notion_props = data.get("properties") or {}

    print(f"Database: {title or db_id}\n")
    print("Properties in Notion:")
    for name, meta in sorted(notion_props.items()):
        print(f"  - {name!r} ({meta.get('type')})")

    explicit = pmap.get("explicit_keys") or set(NOTION_PROPS.keys())
    props = pmap.get("properties") or NOTION_PROPS
    embed = set(pmap.get("embed_in_notes") or [])

    print(f"\nProperty map: {'notion_property_map.json' if using_map else 'NOTION_PROPS defaults'}")
    print("\nMapped pipeline fields → Notion columns:")
    mapped_ok: list[str] = []
    missing: list[str] = []
    wrong: list[str] = []
    embedded: list[str] = []

    for key in sorted(explicit):
        col = props.get(key)
        if not col:
            continue
        if key in embed and key != pmap.get("lead_key_query"):
            embedded.append(f"{key} → embedded in Notes")
            continue
        if col not in notion_props:
            missing.append(f"{key} → {col!r}")
            continue
        exp_type = _expected_type(key, col, pmap)
        actual = notion_props[col].get("type")
        if exp_type and actual != exp_type:
            wrong.append(f"{key} → {col!r} (have {actual}, need {exp_type})")
        else:
            mapped_ok.append(f"{key} → {col!r} ({actual})")

    for key in sorted(embed):
        if key not in explicit:
            embedded.append(f"{key} → embedded in Notes")

    query_key = pmap.get("lead_key_query") or "lead_key"
    if query_key == "brief" or query_key in embed:
        qcol = props.get(query_key) or props.get("brief")
        if qcol and qcol in notion_props:
            print(f"\nDedup query: lead_key marker in {qcol!r} (rich_text contains)")

    if mapped_ok:
        print("\nOK:")
        for line in mapped_ok:
            print(f"  ✓ {line}")
    if embedded:
        print("\nEmbedded (no dedicated column):")
        for line in embedded:
            print(f"  · {line}")
    if missing:
        print("\nMISSING in Notion:")
        for line in missing:
            print(f"  ✗ {line}")
    if wrong:
        print("\nTYPE MISMATCH:")
        for line in wrong:
            print(f"  ! {line}")

    unmapped_defaults = set(NOTION_PROPS.keys()) - explicit - embed
    if unmapped_defaults and using_map:
        print("\nSkipped (no CRM column):")
        for key in sorted(unmapped_defaults):
            print(f"  - {key} (default {NOTION_PROPS[key]!r})")

    if missing or wrong:
        print("\nFix notion_property_map.json or add columns in Notion.")
        return 1

    print("\nOK — ready to sync. Run: python cli.py sync-notion -i output/notion_pipeline.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
