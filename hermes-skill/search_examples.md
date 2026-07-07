# Search examples — natural language → free workflow

Hermes: use these public-web templates. Do not call `cli.py search`, OpenRouter, or Gemini.

## Hermes Google/Public-Web Research

**User:** find Finnish industrial, energy, manufacturing, mining, or proptech leads with contact details

Hermes searches Google/public pages, visits official sources, writes `output/hermes_research.json`, then runs:

```bash
python cli.py import-hermes \
  -i output/hermes_research.json \
  --verify \
  --export-pipeline \
  --basename hermes_google_prospects \
  --source "ICP Search"
```

## PRH Fallback

```bash
python cli.py discover-prh --aether --limit 25 --basename hermes_prh
python cli.py enrich \
  -i output/latest.csv \
  --limit 25 \
  --contacts-per-company 2 \
  --scrape-first \
  --max-llm-calls 0 \
  --preserve-companies \
  --verify \
  --basename hermes_enriched \
  --output-dir output
```

## Export To Notion Pipeline

```bash
python cli.py export-pipeline -i output/latest.csv --basename notion_pipeline
```

## After Every Batch

Report:

- Companies processed
- Contact rows and rows with phones
- `verified_for_outreach=yes` count
- Output path
