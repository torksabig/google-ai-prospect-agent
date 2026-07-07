#!/usr/bin/env bash
# Optional: Hermes cron mirror (runs same script via Hermes scheduler).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if ! command -v hermes >/dev/null 2>&1; then
  echo "Hermes not installed. Use scripts/install-icp-daemon.sh for launchd instead."
  exit 1
fi

hermes cron add \
  --name "aether-icp-pipeline" \
  "0 */6 * * *" \
  "Run bash ${ROOT}/scripts/icp-cycle.sh — ICP enrich/search, export notion_pipeline.csv, sync Notion if configured. Report cycle stats from output/icp_cycle.log tail."

echo "Hermes cron job added (every 6h). List: hermes cron list"
