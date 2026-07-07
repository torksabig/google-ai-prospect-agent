#!/usr/bin/env python3
"""List Notion databases this integration token can access."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

NOTION_VERSION = "2022-06-28"


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _integration_name(client: httpx.Client, token: str) -> str:
    r = client.get("https://api.notion.com/v1/users/me", headers=_headers(token))
    r.raise_for_status()
    data = r.json()
    if data.get("type") == "bot":
        return data.get("name") or "Unknown integration"
    return data.get("name") or "Unknown user"


def _database_title(db: dict) -> str:
    parts = db.get("title") or []
    title = "".join(p.get("plain_text", "") for p in parts).strip()
    return title or "(untitled)"


def main() -> int:
    token = os.environ.get("NOTION_API_KEY", "").strip()
    if not token:
        print(
            "Missing NOTION_API_KEY in .env\n"
            "Create an integration at https://www.notion.so/my-integrations"
        )
        return 1

    with httpx.Client(timeout=30.0) as client:
        try:
            name = _integration_name(client, token)
        except httpx.HTTPStatusError as exc:
            print(f"Token invalid ({exc.response.status_code}): {exc.response.text[:300]}")
            return 1

        print(f"Integration name (search this in Notion Connections): {name!r}\n")

        r = client.post(
            "https://api.notion.com/v1/search",
            headers=_headers(token),
            json={"filter": {"property": "object", "value": "database"}},
        )
        if r.status_code != 200:
            print(f"Search failed {r.status_code}: {r.text[:500]}")
            return 1

        results = r.json().get("results") or []
        if not results:
            print("No databases shared with this integration yet.\n")
            print("Fix:")
            print("  1. Open your Pipeline database in Notion (full page, not only a linked view)")
            print("  2. Click ••• (top right) → Connections")
            print(f"  3. Add integration {name!r} (exact name from notion.so/my-integrations)")
            print("  4. Re-run: python scripts/notion_list_databases.py")
            return 1

        print(f"Accessible databases ({len(results)}):")
        for db in results:
            db_id = db.get("id", "")
            title = _database_title(db)
            print(f"  - {title}")
            print(f"    ID: {db_id}")
            print(f"    URL: https://www.notion.so/{db_id.replace('-', '')}")

        configured = os.environ.get("NOTION_DATABASE_ID", "").strip()
        if configured:
            match = any(db.get("id", "").replace("-", "") == configured.replace("-", "") for db in results)
            print()
            if match:
                print(f"NOTION_DATABASE_ID={configured} is accessible.")
            else:
                print(
                    f"NOTION_DATABASE_ID={configured} is NOT in the list above.\n"
                    "Copy the correct ID from this output into .env, or share that database "
                    f"with integration {name!r}."
                )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
