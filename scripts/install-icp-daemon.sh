#!/usr/bin/env bash
# Install macOS launchd agent — runs ICP cycle every 6 hours in background.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLIST_SRC="${ROOT}/deploy/com.aether.icp-pipeline.plist"
PLIST_DST="${HOME}/Library/LaunchAgents/com.aether.icp-pipeline.plist"
LOG_DIR="${ROOT}/output"

chmod +x "${ROOT}/scripts/icp-cycle.sh"

mkdir -p "$LOG_DIR" "${HOME}/Library/LaunchAgents"

sed \
  -e "s|__ROOT__|${ROOT}|g" \
  -e "s|__LOG__|${LOG_DIR}/icp-daemon.log|g" \
  -e "s|/Users/teodorhiidenlampi|${HOME}|g" \
  "$PLIST_SRC" > "$PLIST_DST"

launchctl bootout "gui/$(id -u)/com.aether.icp-pipeline" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_DST"
launchctl enable "gui/$(id -u)/com.aether.icp-pipeline"
launchctl kickstart -k "gui/$(id -u)/com.aether.icp-pipeline"

echo "Installed 24/7 ICP daemon (every 6h)."
echo "  Plist: $PLIST_DST"
echo "  Log:   $LOG_DIR/icp-daemon.log"
echo "  Stop:  launchctl bootout gui/$(id -u)/com.aether.icp-pipeline"
