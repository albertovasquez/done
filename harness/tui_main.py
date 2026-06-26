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

from harness import paths
from harness.tui.app import HarnessTui


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Harness Textual ACP client")
    parser.add_argument("--model", choices=["mock", "vibeproxy"], default="vibeproxy")
    parser.add_argument("--cwd", default=None,
                        help="project directory the agent operates on (default: current dir)")
    args = parser.parse_args(argv)

    cwd = str(Path(args.cwd).resolve()) if args.cwd else os.getcwd()
    paths.load_env(cwd)               # resolve VIBEPROXY_* before spawning the agent
    # Pass --cwd through so the agent subprocess anchors .env to the same project.
    agent_cmd = [sys.executable, "-m", "harness.acp_main", "--model", args.model, "--cwd", cwd]
    HarnessTui(agent_cmd=agent_cmd, cwd=cwd, model=args.model).run()


if __name__ == "__main__":
    main()
