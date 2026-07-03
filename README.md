# Google AI Prospect Agent

**Web-only** B2B prospecting using Gemini **Grounding with Google Search** (Google tekoälyhaku). No Apify lists, no CSV lead databases.

## What it does

Given filters like country, industry, revenue band, and target titles, the agent:

1. Runs **Google AI Search** via the Gemini API (`google_search` tool)
2. Finds matching companies from public web sources
3. Identifies decision makers in your target roles
4. Extracts **direct phone numbers** when publicly listed (with source URLs)
5. Writes narrative **contact briefs** (Finnish for Finland)

## Setup

```bash
cd ai-agents/google-ai-prospect-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

### API key (pick one)

| Provider | Key format | `.env` variable |
|----------|------------|-----------------|
| **OpenRouter** (recommended if you have `sk-or-v1-…`) | `sk-or-v1-…` | `GAP_OPENROUTER_API_KEY` + `GAP_PROVIDER=openrouter` |
| **Google AI Studio** (native tekoälyhaku) | `AIza…` | `GAP_GEMINI_API_KEY` |

OpenRouter uses `openrouter:web_search` with Gemini models — same idea as Google tekoälyhaku, but billed via [openrouter.ai/credits](https://openrouter.ai/credits).

Billing: Google Search grounding is **1,500 requests/day free** on paid tier; free tier has tight Gemini limits. Enable billing in Google AI Studio for production use.

## Usage

```bash
# Finnish industrial automation, mid-market, R&D leaders + phones
python cli.py search \
  --country Finland \
  --industry "teollisuusautomaatio, energiatekniikka" \
  --revenue "10-100M EUR" \
  --titles "Head of R&D, CTO, teknologiajohtaja, tutkimusjohtaja" \
  --limit 20

# Re-search contacts when first pass only found switchboard
python cli.py search \
  --country Finland \
  --industry "kaukolämpö" \
  --revenue "yli 50M€" \
  --titles "toimitusjohtaja, teknologiajohtaja" \
  --limit 10 \
  --deep-pass
```

## Output

`output/web_prospects_YYYYMMDD_HHMMSS.csv` and `.md` with:

`company_name`, `contact_name`, `contact_title`, `contact_phone`, `phone_type`, `phone_source_url`, `contact_brief`, `evidence_urls`, `confidence`

## Stack

- Python 3.11+
- `google-genai` with `GoogleSearch()` grounding tool
- No DuckDuckGo, no PRH, no lead CSVs (web only)

## Limits

- Phones only when **publicly on the web** — no Apollo/Lusha
- Quality depends on Google index + grounding quota
- Never invents contacts; may return fewer than `--limit` if phones not found
