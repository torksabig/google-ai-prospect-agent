# Setup on another laptop

Move the prospect pipeline + Hermes skill to a second machine in ~10 minutes.

## What you need on the new laptop

| Item | Required? | Notes |
|------|-----------|--------|
| Python 3.9+ | Yes | 3.11+ recommended |
| Git or copy of project folder | Yes | See sync options below |
| API key | No | Free scrape/enrich works without LLM |
| Hermes | Optional | For `/prospect-search` natural-language runs |

---

## Option A — Git (recommended)

On **this laptop** (if not already in git):

```bash
cd "/path/to/MAIN AI"
git add ai-agents/google-ai-prospect-agent
git commit -m "prospect agent"
git push
```

On **other laptop**:

```bash
git clone <your-repo-url> "MAIN AI"
cd "MAIN AI/ai-agents/google-ai-prospect-agent"
chmod +x scripts/setup-laptop.sh
./scripts/setup-laptop.sh
```

`.env` and `output/` are gitignored — copy those separately (see below).

---

## Option B — Copy folder (USB / AirDrop / iCloud)

Copy the whole folder:

```
ai-agents/google-ai-prospect-agent/
```

**Do copy:** all `.py` files, `hermes-skill/`, `requirements.txt`, `scripts/`

**Do not copy:** `.venv/` (recreate on new machine)

**Copy manually if you want continuity:**

| File | Why |
|------|-----|
| `.env` | API keys (or recreate from `.env.example`) |
| `output/latest.csv` | Current lead list |
| `output/web_prospects_*.csv` | Historical runs |

Then on the other laptop:

```bash
cd /path/to/google-ai-prospect-agent
chmod +x scripts/setup-laptop.sh
./scripts/setup-laptop.sh
```

---

## After `setup-laptop.sh`

### 1. Verify CLI

```bash
prospect   # alias added to ~/.zshrc by setup script
python cli.py --help
python cli.py search --help
```

### 3. Install Hermes (optional)

```bash
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
hermes setup          # login / model if prompted
./scripts/setup-laptop.sh   # re-run to link skill
hermes skills list | grep aether
```

### 2. Free workflow

```bash
python cli.py discover-prh --aether --limit 20
python cli.py enrich -i output/latest.csv --scrape-first --max-llm-calls 0 --preserve-companies
python cli.py verify -i output/latest.csv --export-dashboard
```

Or via Hermes:

```bash
hermes chat --yolo -s aether-prospect -t terminal,skills -q \
  "/aether-prospect enrich daily batch"
```

---

## Sync leads between laptops

`output/` is not in git. Pick one:

**AirDrop / cloud:** copy `output/latest.csv` to the other machine’s `output/latest.csv`

**rsync over SSH:**

```bash
rsync -avz output/latest.csv user@other-laptop:/path/to/google-ai-prospect-agent/output/
```

**Dashboard export:** if you use `aether-applied-leads`, sync:

```
outreach-automation/aether-applied-leads/data/google_ai_prospects_verified.csv
```

---

## Hermes skill path (any machine)

The skill no longer hardcodes `Desktop/MAIN AI`. It uses:

1. `$PROSPECT_AGENT_DIR` (set by `setup-laptop.sh` in `~/.zshrc`)
2. Or `.prospect-agent-root` file in the project (written by setup script)

If Hermes can’t find the project, run setup again or:

```bash
export PROSPECT_AGENT_DIR="/your/path/google-ai-prospect-agent"
```

---

## Daily workflow on either laptop

```bash
prospect
python cli.py enrich -i output/latest.csv --limit 25 --scrape-first --preserve-companies
python cli.py verify -i output/latest.csv --export-dashboard
```

Or via Hermes:

```bash
hermes chat -s aether-prospect -q "/prospect-search find 20 proptech companies..."
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `search` says LLM-backed search is disabled | Use `discover-prh`, `enrich --scrape-first`, and `verify` |
| Hermes can’t find `cli.py` | `export PROSPECT_AGENT_DIR=...` and restart terminal |
| Empty discovery results | Tighten PRH filters or use `--aether` to rotate industrial codes |
| Dashboard path wrong | Repo must include `outreach-automation/aether-applied-leads` sibling, or use `--dashboard-dir` |
| `python` not found | Use `python3` and recreate venv |

---

## Security

- Never commit `.env` to git
- Rotate keys if pasted in chat
- Use separate OpenRouter key per machine if you prefer
