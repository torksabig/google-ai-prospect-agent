#!/usr/bin/env python3
"""Background ICP cycle: enrich/search → verify → call list CSV (Notion optional)."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(ROOT / "output" / "icp_cycle.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


def _run(cmd: list[str], *, ok_fail: bool = False) -> int:
    log.info("RUN %s", " ".join(cmd))
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0 and not ok_fail:
        log.warning("Command exited %d", r.returncode)
    return r.returncode


def main() -> int:
    config_path = ROOT / "icp_config.json"
    state_path = ROOT / "output" / "icp_cycle_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)

    cfg = {}
    if config_path.exists():
        cfg = json.loads(config_path.read_text(encoding="utf-8"))

    state = {"cycle": 0}
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))

    cycle = int(state.get("cycle", 0)) + 1
    search_every = int(cfg.get("search_every_cycles", 4))
    enrich_limit = int(cfg.get("enrich_limit", 25))
    search_limit = int(cfg.get("search_limit", 20))
    max_llm = int(cfg.get("max_llm_calls", 0))
    use_llm = bool(cfg.get("use_llm", False))
    icp = cfg.get("icp", {})

    input_csv = ROOT / "output" / "latest.csv"
    seed = ROOT / "output" / "web_prospects_20260707_115644.csv"
    if not input_csv.exists() and seed.exists():
        input_csv = seed
    if not input_csv.exists():
        log.error("No input CSV")
        return 1

    cli = [str(ROOT / ".venv" / "bin" / "python"), str(ROOT / "cli.py")]
    log.info("=== ICP cycle %d ===", cycle)

    if cycle % search_every == 0:
        log.info("Search pass limit=%d", search_limit)
        cmd = cli + [
            "search",
            "--country", icp.get("country", "Finland"),
            "--industry", icp.get("industry", "proptech"),
            "--revenue", icp.get("revenue", "10M - 5B EUR"),
            "--titles", icp.get("titles", "CTO, Head of R&D"),
            "--secondary-titles", icp.get("secondary_titles", "CEO"),
            "--product-context", icp.get("product_context", "B2B ICP prospects"),
            "--limit", str(search_limit),
            "--contacts-per-company", "2",
            "--batch-size", "5",
            "--preserve-companies",
            "--scrape-first",
            "--max-deep-passes", str(search_limit),
            "--continue-from", str(input_csv),
            "--basename", f"icp_search_{cycle}",
            "--verify",
        ]
        _run(cmd, ok_fail=True)
    else:
        log.info("Enrich pass limit=%d", enrich_limit)
        cmd = cli + [
            "enrich",
            "-i", str(input_csv),
            "--limit", str(enrich_limit),
            "--scrape-first",
            "--max-llm-calls", str(max_llm),
            "--preserve-companies",
            "--contacts-per-company", "2",
            "--basename", f"icp_enrich_{cycle}",
        ]
        if use_llm:
            cmd.append("--use-llm")
        _run(cmd, ok_fail=True)

    latest = ROOT / "output" / "latest.csv"
    if not latest.exists():
        latest = input_csv

    _run(cli + ["verify", "-i", str(latest), "--basename", f"icp_verified_{cycle}"], ok_fail=True)
    _run(
        cli
        + [
            "export-call-list",
            "-i",
            str(latest),
            "--append",
            "--source-run-id",
            f"icp_cycle_{cycle}",
        ],
        ok_fail=True,
    )

    import os

    if os.environ.get("NOTION_SYNC", "").lower() in {"1", "true", "yes"}:
        _run(cli + ["export-pipeline", "-i", str(latest), "--basename", "notion_pipeline"])
        if os.environ.get("NOTION_API_KEY") and os.environ.get("NOTION_DATABASE_ID"):
            _run(cli + ["sync-notion", "-i", str(ROOT / "output" / "notion_pipeline.csv")], ok_fail=True)
    else:
        log.info("Skip Notion — set NOTION_SYNC=1 to enable optional Notion export")

    state["cycle"] = cycle
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    log.info("=== Cycle %d done ===", cycle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
