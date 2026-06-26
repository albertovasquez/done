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

from harness import config
from harness import paths
from harness.tui.app import HarnessTui


def _resolve_model(explicit_backend: str | None) -> tuple[str, str | None]:
    """Resolve (backend, model_override) by precedence: an explicit --model flag
    wins (and applies no model override — env/defaults stand); else the persisted
    done.conf default; else the hardcoded ("vibeproxy", None). model_override is
    the persisted model string to export as VIBEPROXY_MODEL, or None to leave the
    env/default untouched."""
    if explicit_backend is not None:
        return explicit_backend, None
    persisted = config.load_default()
    if persisted is not None:
        return persisted.backend, persisted.model
    return "vibeproxy", None


def _relaunch_args(args, cwd) -> list[str]:
    """Flags to re-launch THIS TUI with, reconstructed from parsed args (not raw
    sys.argv) so they are correct however it was invoked. --cwd is always explicit."""
    flags = ["--model", args.model, "--cwd", cwd]
    if args.yolo:
        flags.append("--yolo")
    return flags


def _relaunch_command(args, cwd) -> list[str]:
    """argv for os.execv: the original launcher (the `dn` console script at
    sys.argv[0]) when it is an executable file, else `python -m harness.tui_main`."""
    launcher = sys.argv[0]
    flags = _relaunch_args(args, cwd)
    if launcher and os.path.isfile(launcher) and os.access(launcher, os.X_OK):
        return [launcher, *flags]
    return [sys.executable, "-m", "harness.tui_main", *flags]


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Harness Textual ACP client")
    parser.add_argument("--model", choices=["mock", "vibeproxy"], default=None)
    parser.add_argument("--cwd", default=None,
                        help="project directory the agent operates on (default: current dir)")
    parser.add_argument("--yolo", action="store_true",
                        help="auto-allow every command — never prompt for permission")
    args = parser.parse_args(argv)

    cwd = str(Path(args.cwd).resolve()) if args.cwd else os.getcwd()
    paths.load_env(cwd)               # resolve VIBEPROXY_* before spawning the agent
    backend, model_override = _resolve_model(args.model)
    args.model = backend              # normalize so _relaunch_args carries the resolved backend
    if model_override is not None:
        os.environ.setdefault("VIBEPROXY_MODEL", model_override)  # process env still wins
    # Pass --cwd through so the agent subprocess anchors .env to the same project.
    agent_cmd = [sys.executable, "-m", "harness.acp_main", "--model", backend, "--cwd", cwd]
    if args.yolo:
        agent_cmd.append("--yolo")    # auto-allow flows to the agent, which owns the gate
    app = HarnessTui(agent_cmd=agent_cmd, cwd=cwd, model=backend)
    app.run()
    if getattr(app, "_reexec", False):
        cmd = _relaunch_command(args, cwd)
        try:
            os.execv(cmd[0], cmd)          # replaces the process; never returns on success
        except OSError as e:
            print(f"reload failed to re-exec: {e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
