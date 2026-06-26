#!/usr/bin/env python3
"""TUI entrypoint: a Textual ACP client driving the harness agent subprocess.

Usage:
  .venv/bin/python harness/tui_main.py [--model mock|vibeproxy] [--cwd PATH]
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# Path-hacks for running directly from the checkout. When the harness is
# installed (editable or global), `trace` and `minisweagent` are real
# importable deps and these inserts are harmless no-ops; guarded so a missing
# upstream/ (e.g. a non-editable install) doesn't shadow the installed package.
for _p in (REPO_ROOT / "upstream" / "src", REPO_ROOT):
    if _p.exists() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from harness.tui.app import HarnessTui  # noqa: E402


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Harness Textual ACP client")
    parser.add_argument("--model", choices=["mock", "vibeproxy"], default="mock")
    # Default to the directory you launched from — `done` operates on YOUR cwd
    # (the project you're in), never on the harness's install dir. The harness's
    # own assets (skills/, config, .env) resolve separately via __file__.
    parser.add_argument("--cwd", default=None,
                        help="project directory the agent operates on (default: current dir)")
    args = parser.parse_args(argv)

    # Launch the agent with the SAME interpreter running this process, via
    # module invocation — not a hardcoded .venv path. This is what makes a
    # globally-installed `done` work from any directory: the running interpreter
    # already has both `trace` and `minisweagent` importable (installed deps),
    # so the agent subprocess resolves with no source-tree paths.
    agent_cmd = [sys.executable, "-m", "harness.acp_main", "--model", args.model]
    # The project the agent operates on = your launch cwd (or explicit --cwd).
    cwd = str(Path(args.cwd).resolve()) if args.cwd else os.getcwd()
    HarnessTui(agent_cmd=agent_cmd, cwd=cwd, model=args.model).run()


if __name__ == "__main__":
    main()
