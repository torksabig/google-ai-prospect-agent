---
name: aether-prospect
description: Hermes-orchestrated B2B prospecting. Gemini grounded search (automates manual web Gemini). Also free Google Search + scrape. Normalizes Finnish briefs and appends outreach-ready leads to output/call_list.csv (Notion optional).
triggers:
  - /aether-prospect
  - /prospect-search
  - find me companies
  - find leads
  - enrich leads
  - proptech leads
  - Aether prospecting
  - ICP search
  - gemini search
  - tekoälyhaku
---

# Aether Prospect — Hermes Skill

**Hermes is the orchestrator.** Parse the user request, run the CLI workflow, report results. Do not invent contacts — only use CLI output.

## Project path (any laptop)

Resolve the agent directory in this order:

1. `$PROSPECT_AGENT_DIR` environment variable
2. Contents of `.prospect-agent-root` inside the project (created by `scripts/setup-laptop.sh`)
3. Parent of this skill folder (`hermes-skill/..` = project root when skill is symlinked from repo)

```bash
if [ -n "$PROSPECT_AGENT_DIR" ]; then
  ROOT="$PROSPECT_AGENT_DIR"
elif [ -f .prospect-agent-root ]; then
  ROOT="$(cat .prospect-agent-root)"
else
  ROOT="$(cd "$(dirname "$0")/.." && pwd)"
fi
cd "$ROOT"
source .venv/bin/activate
```

First-time setup: `./scripts/setup-laptop.sh` (see `SETUP_OTHER_LAPTOP.md`).

---

## Mode G: Gemini Web Prospecting (DEFAULT for Gemini users)

**Automates what you do manually on gemini.google.com** — grounded Google Search + structured company/contact extraction + phone verify + **daily call list CSV**.

### Manual Gemini steps → Hermes automation

| Manual (gemini.google.com) | Hermes automation |
|----------------------------|-------------------|
| Open Gemini, enable Google Search | `--provider gemini` (Google Search grounding built-in) |
| Paste Finnish ICP brief | Parsed from NL or baked into `open-hermes.sh` defaults |
| Copy contacts into spreadsheet | `output/gemini_batch_*.csv` + **`output/call_list.csv`** |
| Check phone on company site | `--verify` (person_page_match) |
| Add to call sheet / CRM | **`--update-call-list`** (default on) |
| Add to Notion pipeline | optional `--export-pipeline --sync-notion` |

### Daily one-liner (fully automated, no chat)

```bash
./scripts/open-hermes.sh --run-gemini
```

Optional batch size: `./scripts/open-hermes.sh --run-gemini --limit 50`

### On-demand via Hermes chat (Option B)

Start interactive Hermes, then ask in natural language:

```text
/prospect-search find 20 Finnish companies in energy, manufacturing, mining and proptech,
revenue 10M-5B, R&D/engineering/CEO contacts — use Gemini
```

**Hermes runs:**

```bash
python cli.py hermes-search \
  --provider gemini \
  --country Finland \
  --industries "energy,manufacturing,mining,proptech" \
  --revenue "10M - 5B EUR" \
  --titles "Engineering Manager,CTO,Head of R&D,VP Engineering,CEO" \
  --limit 20 \
  --contacts-per-company 2 \
  --verify \
  --update-call-list \
  --output-dir output
```

### Required `.env` for Gemini mode

```env
GAP_PROVIDER=google
GAP_GEMINI_API_KEY=your_key_here
GAP_GEMINI_MODEL=gemini-2.5-flash
```

Default model is **Flash** (`gemini-2.5-flash`, $0.30/1M input). Use `gemini-2.5-flash-lite` for cheaper runs at lower JSON/contact quality.

Get key: https://aistudio.google.com/apikey

Optional fallback: `GAP_OPENROUTER_API_KEY` with `GAP_PROVIDER=openrouter`.

### Parse user message → CLI flags

| User says | CLI flag |
|-----------|----------|
| N companies | `--limit N` |
| Finland | `--country Finland` |
| energy / manufacturing / mining / proptech | `--industries "energy,manufacturing,mining,proptech"` |
| revenue 10M–5B | `--revenue "10M - 5B EUR"` |
| R&D / engineering / CEO contacts | `--titles "Engineering Manager,CTO,Head of R&D,VP Engineering,CEO"` |
| 2 contacts per company | `--contacts-per-company 2` |
| push to Notion | add `--export-pipeline --sync-notion` (optional) |
| call list / daily dial sheet | default `--update-call-list` → `output/call_list.csv` |
| Gemini / tekoälyhaku / web Gemini | `--provider gemini` |

**When user prospects via Gemini on the web, always add `--provider gemini`.** Do not default to free DuckDuckGo search unless user asks for free mode.

### Output brief format (required)

```md
Jani Toropainen on Mecmetal Oy:n Engineering Manager. Yritys toimii valmistus-alalla...

**Jani Toropainen** — Engineering Manager

Website: https://www.mecmetal.fi/

Puhelin: +358 40 672 6783 (mobile)

Verify: person_page_match — Mobile on official contact page (https://www.mecmetal.fi/fi/yhteystiedot/)
```

### After search — report to user

- Companies found vs requested
- Contact rows + rows with phones
- `verified_for_outreach=yes` count (if `--verify`)
- Paths: `output/gemini_batch_*.csv`, **`output/call_list.csv`**
- Call list stats: rows added / skipped (dedupe by phone + company + contact)
- Notion sync stats only if `--sync-notion`

---

## Mode 0: Free Google Search Prospecting (alternative)

Use when user explicitly wants **free** search (no Gemini API credits).

Hermes parses natural language → runs `cli.py hermes-search` (DuckDuckGo/Serper + contact-page scrape). **No `--provider gemini`.**

### Example

```bash
python cli.py hermes-search \
  --country Finland \
  --industries "energy,manufacturing,mining,proptech" \
  --revenue "10M - 5B EUR" \
  --titles "Engineering Manager,CTO,Head of R&D,VP Engineering,CEO" \
  --limit 100 \
  --contacts-per-company 2 \
  --verify \
  --update-call-list \
  --output-dir output
```

One-liner: `./scripts/open-hermes.sh --run`

### What the command does

1. Builds Finnish `site:.fi` query templates (`puhelin`, `yhteystiedot` + industry + title)
2. Searches the public web (DuckDuckGo by default; Serper if `GAP_SERPER_API_KEY` set)
3. Visits official `.fi` pages, extracts contacts + phones
4. Verifies phone source, writes Finnish brief block
5. Dedupes by domain, contact name, phone
6. Appends outreach-ready rows to **`output/call_list.csv`** (deduped)

### Call list columns

`date_added`, `company_name`, `contact_name`, `contact_title`, `contact_phone`, `phone_type`, `industry`, `company_url`, `contact_brief`, `verified_for_outreach`, `phone_verification_status`, `source_run_id`

Rebuild from latest batch:

```bash
python cli.py export-call-list -i output/latest.csv --append
```

### Search size limits

| Requested | Action |
|-----------|--------|
| ≤25 | Single run |
| 26–100 | One run or split if rate-limited |
| 100+ | Batch across days; suggest `--continue-from output/latest.csv` |

---

## Mode 1: Import Hermes browser research (JSON)

When Hermes already researched in browser and has structured JSON (not CLI search):

```bash
python cli.py import-hermes -i output/hermes_research.json \
  --verify --update-call-list
```

JSON schema: list of companies or `{ "companies": [...] }` with `contact_name`, `contact_title`, `contact_phone`, `company_url`, etc.

---

## Mode 2: Free PRH discovery + scrape enrich (Finland, no search)

When user wants registry-backed company list without Google search:

```bash
python cli.py discover-prh --aether --limit 20 --basename hermes_prh
python cli.py enrich -i output/latest.csv --limit 20 --scrape-first --max-llm-calls 0 \
  --contacts-per-company 2 --preserve-companies --verify
```

---

## Mode 3: Enrich existing CSV

When user says **enrich** / **add phones** on existing list:

```bash
python cli.py enrich -i output/latest.csv --limit 25 --scrape-first \
  --contacts-per-company 2 --preserve-companies --verify
```

---

## Mode 4: 24/7 ICP pipeline (background)

```bash
bash scripts/icp-cycle.sh
# or: ./scripts/install-icp-daemon.sh
```

Each cycle: enrich 25 → verify → append **`output/call_list.csv`**. Notion only if `NOTION_SYNC=1` in `.env`.

---

## Provider matrix

| Mode | Command | Cost | When to use |
|------|---------|------|-------------|
| **Gemini grounding** | `hermes-search --provider gemini` | API credits | **Default if user uses Gemini web** |
| Free Google Search | `hermes-search` | Free | User asks for free / no API |
| Serper Google API | `hermes-search --provider serper` | API key | Optional |
| PRH + scrape | `discover-prh` + `enrich` | Free | Fallback FI |

---

## Install

```bash
cd /path/to/google-ai-prospect-agent
./scripts/setup-laptop.sh
mkdir -p ~/.hermes/skills/outreach
ln -sf "$(pwd)/hermes-skill" ~/.hermes/skills/outreach/aether-prospect
```

## Do NOT

- Default to free DuckDuckGo when user prospects via Gemini web
- Invent phone numbers or contacts
- Skip `verify` before outreach
- Use bare `cli.py search` without `--provider gemini` path (use `hermes-search --provider gemini`)

## More examples

See [`search_examples.md`](search_examples.md).
