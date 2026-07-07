#!/usr/bin/env bash
# Bootstrap google-ai-prospect-agent on a new machine (macOS/Linux).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "==> Prospect agent root: $ROOT"

# Record path for Hermes skill + shell
echo "$ROOT" > .prospect-agent-root

# Python venv
if [[ ! -d .venv ]]; then
  echo "==> Creating venv..."
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt

# Env
if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "==> Created .env from .env.example — add your API keys before searching."
else
  echo "==> .env already exists (not overwritten)."
fi

# Output dir + optional seed
mkdir -p output
if [[ ! -f output/latest.csv ]] && ls output/web_prospects_*.csv 1>/dev/null 2>&1; then
  latest_seed="$(ls -t output/web_prospects_*.csv | head -1)"
  cp "$latest_seed" output/latest.csv
  echo "==> Seeded output/latest.csv from $latest_seed"
fi

# Hermes skill (optional)
if command -v hermes >/dev/null 2>&1; then
  mkdir -p "$HOME/.hermes/skills/outreach"
  ln -sfn "$ROOT/hermes-skill" "$HOME/.hermes/skills/outreach/aether-prospect"
  echo "==> Hermes skill linked: ~/.hermes/skills/outreach/aether-prospect"
else
  echo "==> Hermes not installed. Optional:"
  echo "    curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash"
fi

# Shell helper (idempotent)
MARKER="# prospect-agent"
ZSHRC="${ZSHRC:-$HOME/.zshrc}"
if [[ -f "$ZSHRC" ]] && ! grep -q "$MARKER" "$ZSHRC" 2>/dev/null; then
  cat >> "$ZSHRC" <<EOF

$MARKER
export PROSPECT_AGENT_DIR="$ROOT"
alias prospect='cd "\$PROSPECT_AGENT_DIR" && source .venv/bin/activate'
EOF
  echo "==> Added PROSPECT_AGENT_DIR + 'prospect' alias to $ZSHRC"
fi

# Smoke test
python cli.py --help >/dev/null
echo ""
echo "Setup OK."
echo ""
echo "Next steps:"
echo "  1. Edit $ROOT/.env  (GAP_OPENROUTER_API_KEY or GAP_GEMINI_API_KEY)"
echo "  2. source $ZSHRC   # or open a new terminal"
echo "  3. prospect        # cd + activate venv"
echo "  4. hermes chat -s aether-prospect -t terminal,skills"
echo "     or: python cli.py search --help"
echo ""
echo "Sync leads from other laptop: copy output/latest.csv into $ROOT/output/"
