"""Post pipeline events to the sales-ops dashboard API (optional)."""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_API_URL = "http://127.0.0.1:8787"
FALLBACK_LOG = Path(__file__).resolve().parent / "output" / "dashboard_events.jsonl"


def _api_base() -> str | None:
    url = (os.environ.get("PROSPECT_DASHBOARD_URL") or os.environ.get("DASHBOARD_API_URL") or "").strip()
    if url:
        return url.rstrip("/")
    if os.environ.get("PROSPECT_DASHBOARD_AUTO", "").lower() in {"1", "true", "yes"}:
        return DEFAULT_API_URL
    return None


def _append_local(event: dict[str, Any]) -> None:
    FALLBACK_LOG.parent.mkdir(parents=True, exist_ok=True)
    with FALLBACK_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def log_event(
    action: str,
    *,
    run_id: str | None = None,
    status: str = "info",
    message: str = "",
    details: dict[str, Any] | None = None,
) -> None:
    """Record one pipeline action. No-op if dashboard URL unset."""
    payload = {
        "action": action,
        "run_id": run_id,
        "status": status,
        "message": message,
        "details": details or {},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    _append_local(payload)

    base = _api_base()
    if not base:
        return

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/api/events",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            resp.read()
    except urllib.error.URLError as exc:
        log.debug("Dashboard log skipped (%s): %s", action, exc)


def start_run(filters: dict[str, Any], source: str = "cli") -> str | None:
    """Register a new run (no subprocess); returns run_id when dashboard API reachable."""
    base = _api_base()
    if not base:
        return None

    body = json.dumps({"filters": filters, "source": source}).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/api/runs/register",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("run_id")
    except urllib.error.URLError as exc:
        log.debug("Dashboard run start skipped: %s", exc)
        return None


def finish_run(
    run_id: str | None,
    *,
    status: str,
    message: str = "",
    summary: dict[str, Any] | None = None,
) -> None:
    if not run_id:
        log_event("run_finished", status=status, message=message, details=summary or {})
        return

    base = _api_base()
    if not base:
        log_event("run_finished", run_id=run_id, status=status, message=message, details=summary or {})
        return

    body = json.dumps(
        {"status": status, "message": message, "summary": summary or {}}
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/api/runs/{run_id}/finish",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
    except urllib.error.URLError as exc:
        log.debug("Dashboard run finish skipped: %s", exc)
