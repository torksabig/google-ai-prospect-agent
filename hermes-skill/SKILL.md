---
name: aether-prospect
description: Hermes-orchestrated B2B prospecting. Default = Google Search + page scrape (free). Optional Gemini tekoälyhaku. Normalizes Finnish briefs and syncs Notion pipeline.
triggers:
  - /aether-prospect
  - /prospect-search
  - find me companies
  - find leads
  - enrich leads
  - proptech leads
  - Aether prospecting
  - ICP search
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

## Mode 0: Hermes Google Search Prospecting (DEFAULT)

Use when the user asks to **find**, **discover**, or **search for** companies/leads.

Hermes parses natural language → runs `cli.py hermes-search` (free web search + contact-page scrape). **No OpenRouter/Gemini unless user explicitly asks for Gemini mode.**

### Parse user message → CLI flags

| User says | CLI flag |
|-----------|----------|
| N companies | `--limit N` |
| Finland | `--country Finland` |
| energy / manufacturing / mining / proptech | `--industries "energy,manufacturing,mining,proptech"` |
| revenue 10M–5B | `--revenue "10M - 5B EUR"` |
| R&D / engineering / CEO contacts | `--titles "Engineering Manager,CTO,Head of R&D,VP Engineering,CEO"` |
| 2 contacts per company | `--contacts-per-company 2` |
| push to Notion | add `--export-pipeline --sync-notion` |
| Gemini / tekoälyhaku | `--provider gemini` (paid; only when user asks) |

### Example user request

```text
/prospect-search find 100 Finnish companies in energy, manufacturing, mining and proptech,
revenue 10M-5B, R&D/engineering/CEO contacts
```

**Hermes runs:**

```bash
python cli.py hermes-search \
  --country Finland \
  --industries "energy,manufacturing,mining,proptech" \
  --revenue "10M - 5B EUR" \
  --titles "Engineering Manager,CTO,Head of R&D,VP Engineering,CEO" \
  --limit 100 \
  --contacts-per-company 2 \
  --verify \
  --export-pipeline \
  --sync-notion \
  --output-dir output
```

### What the command does

1. Builds Finnish `site:.fi` query templates (`puhelin`, `yhteystiedot` + industry + title)
2. Searches the public web (DuckDuckGo by default; Serper if `GAP_SERPER_API_KEY` set)
3. Visits official `.fi` pages, extracts contacts + phones
4. Verifies phone source, writes Finnish brief block (`build_prospect_notes` format)
5. Dedupes by domain, contact name, phone
6. Exports `output/hermes_google_search_*.csv` + `output/notion_pipeline.csv`
7. Optionally syncs Notion (`NOTION_API_KEY` + `NOTION_DATABASE_ID` in `.env`)

### Output brief format (required)

```md
Jani Toropainen on Mecmetal Oy:n Engineering Manager. Yritys toimii valmistus-alalla...

**Jani Toropainen** — Engineering Manager

Website: https://www.mecmetal.fi/

Puhelin: +358 40 672 6783 (mobile)

Verify: person_page_match — Mobile on official contact page (https://www.mecmetal.fi/fi/yhteystiedot/)
```

### Search size limits

| Requested | Action |
|-----------|--------|
| ≤25 | Single `hermes-search` run |
| 26–100 | One run or split if rate-limited |
| 100+ | Batch across days; suggest `--continue-from output/latest.csv` |

### After search — report to user

- Companies found vs requested
- Contact rows + rows with phones
- `verified_for_outreach=yes` count (if `--verify`)
- Paths: `output/hermes_google_search_*.csv`, `output/notion_pipeline.csv`
- Notion sync stats if `--sync-notion`

---

## Mode 1: Import Hermes browser research (JSON)

When Hermes already researched in browser and has structured JSON (not CLI search):

```bash
python cli.py import-hermes -i output/hermes_research.json \
  --verify --export-pipeline --sync-notion
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

## Mode 4: Gemini tekoälyhaku (OPTIONAL — paid)

**Only when user explicitly requests Gemini / tekoälyhaku / LLM search.**

```bash
python cli.py hermes-search \
  --provider gemini \
  --country Finland \
  --industries "energy,manufacturing" \
  --limit 20 \
  --verify
```

Or direct: `python cli.py search --preset aether` (same LLM path, higher API cost).

Requires `GAP_OPENROUTER_API_KEY` or `GAP_GEMINI_API_KEY` in `.env`.

---

## Mode G: 24/7 ICP pipeline → Notion (background)

```bash
bash scripts/icp-cycle.sh
# or: ./scripts/install-icp-daemon.sh
```

Each cycle: enrich 25 → verify → export-pipeline → sync-notion.

---

## Provider matrix

| Mode | Command | Cost | Default |
|------|---------|------|---------|
| Google Search | `hermes-search` | Free | **Yes** |
| Serper Google API | `hermes-search --provider serper` | API key | Optional |
| Gemini grounding | `hermes-search --provider gemini` | API credits | Explicit only |
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

- Use `cli.py search` unless user chose Gemini mode
- Invent phone numbers or contacts
- Skip `verify` before outreach
- Default to OpenRouter/Gemini when user asked for Hermes Google search

## More examples

See [`search_examples.md`](search_examples.md).
