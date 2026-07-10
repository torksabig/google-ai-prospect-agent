# The Gemini Agent

GitHub folder/repo: **`the-gemini-agent`**. Web-only B2B prospecting (Gemini + Google Search, PRH/YTJ, scrape-first enrichment).

Also referenced as *Google AI Prospect Agent* in older docs.

**Web-only** B2B prospecting using public web sources, PRH/YTJ discovery, and scrape-first enrichment. No Apify lists, no CSV lead databases.

## What it does

Given filters like country, industry, revenue band, and target titles, the agent:

1. Finds matching companies from public web sources
2. Identifies decision makers in your target roles
3. Extracts **direct phone numbers** when publicly listed (with source URLs)
4. Writes narrative **contact briefs** (Finnish for Finland)

## Setup on another machine

See **`SETUP_OTHER_LAPTOP.md`** — run `./scripts/setup-laptop.sh` after copying or cloning the repo.

## Setup

```bash
cd the-gemini-agent  # or: ai-agents/google-ai-prospect-agent in MAIN AI monorepo
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

No API key is required for the default workflow. Free discovery uses PRH/YTJ plus website scraping.

## Aether Applied (standard ICP run)

```bash
# 200 Finnish industrial companies, 2 callable contacts each, free/public-web workflow
python cli.py discover-prh --aether --limit 200
python cli.py enrich -i output/latest.csv --scrape-first --max-llm-calls 0 --preserve-companies
python cli.py verify -i output/latest.csv --export-dashboard
```

See **`AETHER_RUNBOOK.md`** for full criteria (revenue, industry, titles, phones).

## Usage

```bash
# Public-web discovery, scrape, and verify
python cli.py discover-prh --aether --limit 200
python cli.py enrich -i output/latest.csv --scrape-first --max-llm-calls 0 --preserve-companies
python cli.py verify -i output/web_prospects_*.csv --export-dashboard
```

## Phone verify (before outreach)

```bash
python cli.py verify -i output/web_prospects_*.csv --export-dashboard
```

Adds: `phone_verification_status`, `phone_verification_reason`, `phone_owner_match`, `phone_page_match`, `verified_for_outreach`.

`--export-dashboard` writes to `outreach-automation/aether-applied-leads/data/google_ai_prospects_verified.csv`.

Outreach gate: `verified_for_outreach=yes` only for `person_page_match` or `dial_confirmed`.

## Daily call list (primary outreach)

Each `hermes-search` run appends outreach-ready leads to **`output/call_list.csv`** (deduped by phone + company + contact). Notion sync is optional.

```bash
./scripts/open-hermes.sh --run-gemini --limit 20
# or free mode:
./scripts/open-hermes.sh --run --limit 20
```

Rebuild or seed call list from a batch CSV:

```bash
python cli.py export-call-list -i output/gemini_batch_*.csv --append
```

## Output

`output/web_prospects_YYYYMMDD_HHMMSS.csv` and `.md` with:

`company_name`, `contact_name`, `contact_title`, `contact_phone`, `phone_type`, `phone_source_url`, `contact_brief`, `evidence_urls`, `confidence`

## Environment variables

Copy `.env.example` to `.env`. Never commit `.env`.

| Variable | Purpose |
|----------|---------|
| `GAP_GEMINI_API_KEY` | Google AI Studio key for `--run-gemini` / `--provider gemini` |
| `GAP_OPENROUTER_API_KEY` | Optional OpenRouter instead of direct Gemini |
| `GAP_PROVIDER`, `GAP_GEMINI_MODEL` | Provider and model (default Flash) |
| `GAP_SERPER_API_KEY` | Optional Serper for Hermes Google Search |
| `NOTION_API_KEY`, `NOTION_DATABASE_ID` | Optional Notion CRM sync — see `NOTION_PIPELINE.md` |
| `PROSPECT_DASHBOARD_URL` | Optional local dashboard event logging |


## Stack

- Python 3.11+
- No paid search API dependency
- PRH/YTJ discovery, website scraping, and verification only

## Limits

- Phones only when **publicly on the web** — no Apollo/Lusha
- Quality depends on public web data and official company pages
- Never invents contacts; may return fewer than `--limit` if phones not found
