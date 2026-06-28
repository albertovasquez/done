# harness/jobs/cron_main.py
"""Console entrypoint for the harness-cron daemon.

Usage:
    harness-cron [--interval SECONDS]   # run forever (default interval 30 s)
    harness-cron --once                 # fire one tick and exit

The daemon loads .env before any tick so executor's dotenv=None assumption
(harness/jobs/executor.py:123) is satisfied — the values reach os.environ
at process startup, which is what resolve_session_model reads.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import time

from dotenv import load_dotenv

from harness import paths
from harness.jobs.daemon import run_forever, tick


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="harness-cron",
        description="Run the harness job scheduler daemon.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Fire one tick (process all due jobs) and exit immediately.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=30.0,
        metavar="SECONDS",
        help="Seconds between ticks in continuous mode (default: 30).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # CARRY-FORWARD (executor.py:123): executor passes dotenv=None and relies on
    # .env being loaded into the process environment before any tick runs.
    # Mirror harness/tui_main.py:103 (paths.load_env) — we call load_dotenv directly
    # here so tests can monkeypatch harness.jobs.cron_main.load_dotenv.
    # Daemon has no project_dir, so only the global config .env is relevant.
    # load_dotenv is a no-op (returns False) when the path does not exist.
    load_dotenv(paths.config_dir() / ".env", override=False)

    if args.once:
        tick(now=time.time())
        return 0

    asyncio.run(
        run_forever(
            interval=args.interval,
            clock=time.time,
            sleep=asyncio.sleep,
        )
    )
    return 0
