# Notion pipeline setup

> **Primary outreach output is `output/call_list.csv`** (updated each `hermes-search` run). Notion sync is optional — use when you want CRM-style pipeline in Notion.

Sync ICP search results to a **Notion database** (your pipeline list).

## 1. Create Notion integration

1. https://www.notion.so/my-integrations → **New integration**
2. Name it anything (e.g. `Hermes` or `Aether ICP Pipeline`) — **Connections shows this name**, not the Hermes CLI product
3. Copy **Internal Integration Secret** → `NOTION_API_KEY` in `.env`

## 2. Create pipeline database

In Notion, create a database (table) named **Pipeline** with these properties **exact names**:

| Property | Type |
|----------|------|
| **Name** | Title |
| **Lead Key** | Text |
| **Company** | Text |
| **Title** | Text |
| **Phone** | Phone |
| **Industry** | Text |
| **Revenue** | Text |
| **Country** | Text |
| **Website** | URL |
| **Brief** | Text |
| **Stage** | Select (`New`, `Ready`, `Contacted`, `Qualified`) |
| **Source** | Select (`ICP Search`) |
| **Phone Status** | Text |
| **Outreach OK** | Checkbox |

Share the database with your integration: **⋯ → Connections →** search the exact name from `python scripts/notion_list_databases.py` (e.g. `Hermes`)

Your pipeline database:
https://app.notion.com/p/38eb22873e6680e99c4dd8bbedeb139f

Database ID (already in `.env`):

```
NOTION_DATABASE_ID=38eb2287-3e66-80e9-9c4d-d8bbedeb139f
```

```env
NOTION_API_KEY=secret_...
# NOTION_DATABASE_ID already set
```

Verify schema:

```bash
python scripts/notion_check.py
```

## 4. Manual sync test

```bash
cd ai-agents/google-ai-prospect-agent
source .venv/bin/activate

python cli.py export-pipeline -i output/latest.csv
python cli.py sync-notion -i output/notion_pipeline.csv
```

Output CSV: `output/notion_pipeline.csv` (organized columns for review).

## 5. 24/7 background (macOS)

**Recommended — launchd** (no Hermes LLM cost):

```bash
chmod +x scripts/install-icp-daemon.sh scripts/icp-cycle.sh
./scripts/install-icp-daemon.sh
```

Runs `scripts/icp-cycle.sh` every **6 hours**:
1. Enrich 25 companies (scrape-first) OR search every 4th cycle
2. Verify phones
3. Write `output/notion_pipeline.csv`
4. Push new/updated rows to Notion

Logs: `output/icp_cycle.log`, `output/icp-daemon.log`

**Optional — Hermes cron** (same script, Hermes scheduler):

```bash
./scripts/install-hermes-icp-cron.sh
```

## 6. Edit ICP targets

Edit `icp_config.json` — industry, revenue, country, titles. Daemon picks it up next cycle.

## Pipeline CSV columns

`output/notion_pipeline.csv`:

- `lead_key` — dedup id
- `company_name`, `contact_name`, `contact_title`, `contact_phone`
- `phone_verification_status`, `verified_for_outreach`
- `pipeline_stage` — `New` or `Ready`
- `contact_brief`, `industry`, `estimated_revenue`, `country`

## Stop daemon

```bash
launchctl bootout gui/$(id -u)/com.aether.icp-pipeline
```

## Troubleshooting

### Schema mismatch after connecting

Your Pipeline uses the **Aether CRM template** (Contact Name, Phone number, Deal stage, …), not the minimal template in section 2. Sync maps columns via `notion_property_map.json`. Verify with:

```bash
python scripts/notion_check.py
```

If properties were renamed in Notion, edit `notion_property_map.json` to match.

### Can't find "Hermes" in Connections

**Hermes Agent** (this CLI tool) is not the same thing as your **Notion integration**. The name shown in Notion **Connections** comes from [notion.so/my-integrations](https://www.notion.so/my-integrations) — whatever you named the integration when you created it.

To see the exact name your token uses:

```bash
python scripts/notion_list_databases.py
```

Search for that exact name in **••• → Connections → Add connections**. It might be `Hermes`, `Aether ICP Pipeline`, or something else — use the API name, not the Hermes product name.

### Database 404 / sync fails / zero databases listed

If `python scripts/notion_check.py` returns `object_not_found` or `notion_list_databases.py` shows **no databases**, your integration token works but **no database has been shared with it yet**.

1. Open your Pipeline database: https://app.notion.com/p/38eb22873e6680e99c4dd8bbedeb139f
2. If you only see a **linked view** inside another page, click the database **title** → **Open as page** (you must share the root database, not a view).
3. Top right **•••** → **Connections** → **Add connection**
4. Pick the integration by the name from step above (run `notion_list_databases.py` if unsure)
5. Confirm **Can edit** access
6. Re-check:

```bash
python scripts/notion_list_databases.py   # should list "Pipeline" (or your DB title) + ID
python scripts/notion_check.py            # schema check
python cli.py sync-notion -i output/notion_pipeline.csv
```

### Wrong database ID

If `notion_list_databases.py` lists databases but your `NOTION_DATABASE_ID` is missing from the list, copy the **ID** line from the script output into `.env`:

```env
NOTION_DATABASE_ID=<id-from-script>
```

The ID in the URL (`38eb2287…`) is correct for the Pipeline page — you still must **share that database** with the integration before the API can read it.
