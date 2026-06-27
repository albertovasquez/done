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


def _resolve_model(explicit_backend: str | None,
                   persona_id: str | None = None) -> tuple[str, str | None]:
    """Resolve (backend, model_override) by precedence: an explicit --model flag
    wins (and applies no model override — env/defaults stand); else the persisted
    done.conf entry for this PERSONA; else the hardcoded ("vibeproxy", None).
    model_override is the persisted model string to export as VIBEPROXY_MODEL, or
    None to leave the env/default untouched."""
    if explicit_backend is not None:
        return explicit_backend, None
    persisted = config.load_agent(persona_id or "default")
    if persisted is not None:
        return persisted.backend, persisted.model
    return "vibeproxy", None


def _resolve_yolo(flag: bool, persona_id: str | None = None) -> bool:
    """--yolo forces auto-allow on; else the persisted pin for this persona; else
    off."""
    if flag:
        return True
    return config.yolo_pinned(persona_id or "default")


def _effective_worker_model_id(backend: str, persona_id: str | None,
                               shell_set_model: bool) -> str | None:
    """The model id the agent will actually run, so the TUI footer can show it
    on a fresh launch. Resolves the launch persona's model the same way the child
    (acp_main) does — via resolve_session_model — so the footer matches the agent
    without depending on VIBEPROXY_MODEL being pre-seeded."""
    from harness.persona_sessions import resolve_session_model
    return resolve_session_model(
        persona_id or "default",
        shell_set_model=shell_set_model,
        shell_env=os.getenv("VIBEPROXY_MODEL"),
        dotenv=os.getenv("VIBEPROXY_MODEL"),
        backend=backend,
    )


def _relaunch_args(args, cwd) -> list[str]:
    """Flags to re-launch THIS TUI with (the /reload re-exec), reconstructed from
    parsed args (not raw sys.argv) so they are correct however it was invoked.
    --model carries the session backend (mock|vibeproxy); --cwd is always explicit;
    --yolo / --persona are emitted when set — /reload preserves the current state."""
    flags = ["--model", args.model, "--cwd", cwd]
    if args.yolo:
        flags.append("--yolo")
    if getattr(args, "persona", None):
        flags += ["--persona", args.persona]
    if getattr(args, "debug", False):
        flags.append("--debug")        # /reload preserves the trace state
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
    parser.add_argument("--persona", default=None,
                        help="persona workspace id to run as (default: the built-in default)")
    parser.add_argument("--debug", action="store_true",
                        help="write a JSONL trace of this run to harness/runs/<ts>/trace.jsonl")
    args = parser.parse_args(argv)

    cwd = str(Path(args.cwd).resolve()) if args.cwd else os.getcwd()
    # Capture whether VIBEPROXY_MODEL came from the real shell env BEFORE load_env
    # may fill it from a .env file. Precedence we want: shell env > done.conf >
    # .env > default. load_env uses override=False, so a .env value only lands in
    # os.environ here when the shell did NOT already set it.
    shell_set_model = "VIBEPROXY_MODEL" in os.environ
    paths.load_env(cwd)               # resolve VIBEPROXY_* before spawning the agent
    backend, model_override = _resolve_model(args.model, args.persona)
    args.model = backend              # normalize so _relaunch_args carries the resolved backend
    yolo = _resolve_yolo(args.yolo, args.persona)
    args.yolo = yolo                  # normalize so /reload re-execs with the resolved state
    from harness.debug_flag import resolve_debug
    try:
        _conf_debug = config.harness_debug()
    except Exception:
        _conf_debug = None
    debug = resolve_debug(args.debug, os.environ, _conf_debug)
    args.debug = debug                # normalize so /reload re-execs with the resolved state
    worker_model_id = _effective_worker_model_id(backend, args.persona, shell_set_model)
    # Pass --cwd through so the agent subprocess anchors .env to the same project.
    agent_cmd = [sys.executable, "-m", "harness.acp_main", "--model", backend, "--cwd", cwd]
    if args.persona:
        agent_cmd += ["--persona", args.persona]
    if args.yolo:
        agent_cmd.append("--yolo")    # auto-allow flows to the agent, which owns the gate
    if debug:
        agent_cmd.append("--debug")   # the subprocess relays trace payloads when set
    app = HarnessTui(agent_cmd=agent_cmd, cwd=cwd, model=backend,
                     worker_model_id=worker_model_id, yolo=yolo,
                     persona=args.persona, debug=debug)
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
