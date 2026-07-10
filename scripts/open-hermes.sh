#!/usr/bin/env bash
# Open prospect agent + dashboard + Hermes (aether-prospect skill).
#
# Usage:
#   ./scripts/open-hermes.sh              # interactive Hermes chat
#   ./scripts/open-hermes.sh --run        # free CLI batch (DuckDuckGo + scrape)
#   ./scripts/open-hermes.sh --run-gemini # Gemini grounded search (same as manual web Gemini)
#   ./scripts/open-hermes.sh --run-via-hermes   # NL via Hermes chat (needs big model)
#   ./scripts/open-hermes.sh --profile default --run-gemini --limit 50
set -euo pipefail

ROOT="${PROSPECT_AGENT_DIR:-}"
if [[ -z "$ROOT" && -f "$(dirname "$0")/../.prospect-agent-root" ]]; then
  ROOT="$(cat "$(dirname "$0")/../.prospect-agent-root")"
fi
if [[ -z "$ROOT" ]]; then
  ROOT="$(cd "$(dirname "$0")/.." && pwd)"
fi

DASH="${PROSPECT_DASHBOARD_DIR:-$HOME/Desktop/MAIN AI/web-tools/sales-ops-dashboard}"

port_listen() {
  lsof -iTCP:"$1" -sTCP:LISTEN -t >/dev/null 2>&1
}

if ! port_listen 8787; then
  echo "Starting dashboard API on :8787"
  PROSPECT_AGENT_ROOT="$ROOT" nohup bash "$DASH/scripts/start-api.sh" >>/tmp/prospect-api.log 2>&1 &
  sleep 2
fi

if ! port_listen 3000; then
  echo "Starting dashboard UI on :3000"
  (
    cd "$DASH"
    [[ -d node_modules ]] || npm install --silent
    nohup npm run dev >>/tmp/prospect-ui.log 2>&1 &
  )
  sleep 3
fi

cd "$ROOT"
export PROSPECT_AGENT_DIR="$ROOT"
export PROSPECT_DASHBOARD_URL="${PROSPECT_DASHBOARD_URL:-http://127.0.0.1:8787}"
# shellcheck disable=SC1091
source .venv/bin/activate

HERMES_PROFILE="${HERMES_PROFILE:-local-power}"
MODE="chat"
PROMPT=""
LIMIT="${PROSPECT_BATCH_LIMIT:-20}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --run)
      MODE="cli"
      shift
      ;;
    --run-gemini)
      MODE="gemini"
      shift
      ;;
    --run-via-hermes)
      MODE="hermes"
      PROMPT='/prospect-search find 20 Finnish companies in energy, manufacturing, mining and proptech, revenue 10M-5B EUR, R&D/engineering/CEO contacts'
      shift
      ;;
    --limit)
      LIMIT="${2:?missing value for --limit}"
      shift 2
      ;;
    --profile)
      HERMES_PROFILE="${2:?missing value for --profile}"
      shift 2
      ;;
    --*)
      echo "Unknown flag: $1" >&2
      echo "Flags: --run (free CLI), --run-gemini (Gemini), --run-via-hermes, --limit N, --profile NAME" >&2
      exit 1
      ;;
    *)
      PROMPT="$1"
      MODE="hermes"
      shift
      ;;
  esac
done

if [[ "$MODE" == "gemini" ]]; then
  STAMP="$(date +%Y%m%d_%H%M%S)"
  echo "Running Gemini grounded search — limit ${LIMIT}"
  exec python cli.py hermes-search \
    --provider gemini \
    --country Finland \
    --industries "energy,manufacturing,mining,proptech" \
    --revenue "10M - 5B EUR" \
    --titles "Engineering Manager,CTO,Head of R&D,VP Engineering,CEO" \
    --limit "$LIMIT" \
    --contacts-per-company 2 \
    --verify \
    --update-call-list \
    --output-dir output \
    --basename "gemini_batch_${STAMP}"
fi

if [[ "$MODE" == "cli" ]]; then
  STAMP="$(date +%Y%m%d_%H%M%S)"
  echo "Running hermes-search via CLI (no LLM) — limit ${LIMIT}"
  exec python cli.py hermes-search \
    --country Finland \
    --industries "energy,manufacturing,mining,proptech" \
    --revenue "10M - 5B EUR" \
    --titles "Engineering Manager,CTO,Head of R&D,VP Engineering,CEO" \
    --limit "$LIMIT" \
    --contacts-per-company 2 \
    --verify \
    --update-call-list \
    --output-dir output \
    --basename "hermes_batch_${STAMP}"
fi

HERMES_CMD=(hermes -p "$HERMES_PROFILE" chat --yolo -s aether-prospect -t terminal,skills)
if [[ -n "$PROMPT" ]]; then
  SHORT="Run aether-prospect skill. Execute cli.py only. No explanation. Task: ${PROMPT}"
  exec "${HERMES_CMD[@]}" -q "$SHORT"
fi
exec "${HERMES_CMD[@]}"
