#!/usr/bin/env python3
"""TUI entrypoint: a Textual ACP client driving the harness agent subprocess.

Usage:
  .venv/bin/python trace/tui_main.py [--model mock|vibeproxy] [--cwd PATH]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "upstream" / "src"))
sys.path.insert(0, str(REPO_ROOT))

from trace.tui.app import HarnessTui  # noqa: E402


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Harness Textual ACP client")
    parser.add_argument("--model", choices=["mock", "vibeproxy"], default="mock")
    parser.add_argument("--cwd", default=".")
    args = parser.parse_args(argv)

    agent_cmd = [
        str(REPO_ROOT / ".venv/bin/python"),
        str(REPO_ROOT / "trace/acp_main.py"),
        "--model", args.model,
    ]
    cwd = str(Path(args.cwd).resolve())
    HarnessTui(agent_cmd=agent_cmd, cwd=cwd, model=args.model).run()


if __name__ == "__main__":
    main()
