#!/usr/bin/env python3
"""ACP agent entrypoint: serve the harness over JSON-RPC on stdio.

STDOUT IS THE WIRE — MSWEA_SILENT_STARTUP is set before any minisweagent import
and nothing is ever printed to stdout. Usage (a client launches this):
  .venv/bin/python harness/acp_main.py [--model mock|vibeproxy]
"""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

os.environ.setdefault("MSWEA_SILENT_STARTUP", "1")   # MUST be before minisweagent import

import acp  # noqa: E402
from harness import paths  # noqa: E402


def _load_agent_cfg() -> dict:
    import yaml
    cfg = yaml.safe_load(paths.mini_yaml_path().read_text())
    return cfg["agent"]


def _stub_complete(system: str, user: str) -> str:
    """Deterministic, offline replacement for the Router's `complete` (used only
    when HARNESS_ROUTER_STUB=1) — no VibeProxy call, so tests are fast and
    non-flaky.

    Prompt-aware so it serves BOTH chat and agent test paths: an imperative
    prompt (starts with a task verb like "fix"/"add"/"implement") classifies as
    `code_fix` (→ the agent/dispatch path); anything else stays `chat_question`
    (→ the chat path). This is a coarse heuristic for tests, not the real
    classifier — questions ("what is 1+1") stay chat; commands ("Fix the failing
    test…") route to the agent.

    Router.classify wraps the prompt with a history preamble when prior turns
    exist ("Recent context …\n\nClassify THIS request: <prompt>"). Read the actual
    request after that marker so a follow-up COMMAND classifies the same with or
    without history — otherwise the first word would be "Recent" and every
    follow-up would mis-route to chat."""
    import json
    _MARKER = "Classify THIS request:"
    request = user.rsplit(_MARKER, 1)[-1] if _MARKER in user else user
    _TASK_VERBS = ("fix", "add", "implement", "create", "write", "refactor",
                   "rename", "remove", "delete", "update", "change", "build",
                   "make", "patch", "generate")
    first = request.strip().split(None, 1)[0].lower() if request.strip() else ""
    task_type = "code_fix" if first in _TASK_VERBS else "chat_question"
    return json.dumps({
        "task_type": task_type,
        "skills": [],
        "confidence": 1.0,
        "suggested_model": None,
        "reasoning": "stubbed classification (HARNESS_ROUTER_STUB)",
    })


def _model_factory(model_choice: str):
    """Return a factory `make(current_model=None, project_cwd=None,
    memory_root=None) -> Model`. The agent calls it per turn with its current
    worker model so /models can hot-swap (the arg wins over the env default),
    plus the project cwd (skill roots) and the session workspace (memory recall).
    Mock ignores them all."""
    if model_choice == "mock":
        from harness.models_mock import build_mock_model

        def make(current_model=None, project_cwd=None, memory_root=None):
            return build_mock_model()
        return make
    # vibeproxy path — api_base/api_key live in model_kwargs (LitellmModelConfig has
    # no top-level api_base/api_key fields); mirror run_traced.py's proven wiring.
    def make(current_model=None, project_cwd=None, memory_root=None):
        from harness.streaming_model import StreamingLitellmModel
        from harness.tools.registry import build_registry
        from harness import vibeproxy, paths
        model_id = current_model or vibeproxy.default_model()
        return StreamingLitellmModel(
            model_name=vibeproxy.model_id(model_id),
            model_kwargs=vibeproxy.model_kwargs(),
            cost_tracking="ignore_errors",
            # skill_roots (project-aware) => the load_skill tool can pull project
            # .agents/.claude skill bodies, not just global ones. memory_root (the
            # session persona workspace) => the load_memory tool for fact recall.
            registry=build_registry(skill_roots=paths.skills_dirs(project_cwd=project_cwd),
                                    memory_root=memory_root),
        )
    return make


async def _main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="ACP harness agent")
    parser.add_argument("--model", choices=["mock", "vibeproxy"], default="mock")
    parser.add_argument("--cwd", default=None,
                        help="project dir the agent operates on (anchors .env)")
    parser.add_argument("--yolo", action="store_true",
                        help="auto-allow every command without prompting (no permission modal)")
    parser.add_argument("--persona", default=None,
                        help="persona workspace id to run as (default: the built-in default)")
    parser.add_argument("--debug", action="store_true",
                        help="relay a JSONL trace of the dn↔agent loop to the client")
    args = parser.parse_args(argv)

    from harness import config
    from harness.debug_flag import resolve_debug
    try:
        conf_debug = config.harness_debug()
    except Exception:
        conf_debug = None
    debug = resolve_debug(args.debug, os.environ, conf_debug)
    if debug:
        # The agent's stderr is hidden behind the TUI's alt-screen, so its
        # logger.warning/exception calls are invisible without a file sink. Route
        # them next to the trace. Own run dir (separate process from the TUI).
        import time as _time
        from harness.logging_setup import setup_file_logging
        # NOTE: `paths` is already module-imported at the top — do NOT re-import it
        # here. A local `from harness import paths` would make `paths` a function
        # local for the WHOLE scope, leaving paths.load_env() below unbound on the
        # non-debug path (UnboundLocalError).
        try:
            log_dir = paths.runs_dir() / f"{_time.strftime('%Y%m%d-%H%M%S')}-agent-{os.getpid()}"
            setup_file_logging(log_dir / "harness.log")
        except Exception:
            pass                          # logging setup must never break startup

    cwd = str(Path(args.cwd).resolve()) if args.cwd else os.getcwd()
    # Capture whether PROXY_MODEL/VIBEPROXY_MODEL came from the real SHELL env BEFORE
    # load_env may fill it from a .env file. Mirrors tui_main: the precedence we want
    # is shell env > done.conf[persona] > .env > engine default. load_env uses
    # override=False, so a .env value only lands in os.environ when the shell did
    # NOT already set it — but we must still distinguish the two, because a
    # .env-derived value must NOT outrank the persona's persisted model.
    from harness import vibeproxy
    shell_set_model = vibeproxy.model_set_in(os.environ)
    paths.load_env(cwd)               # BEFORE importing engine-touching modules

    import sys
    from harness import persona_select
    try:
        workspace_dir = persona_select.resolve_workspace(args.persona)
    except persona_select.InvalidPersonaId as e:
        print(f'invalid persona id "{e}" — use only letters, digits, - or _',
              file=sys.stderr)
        raise SystemExit(2)
    except persona_select.UnknownPersona as e:
        print(f'no persona "{e}" — run /persona to list available personas',
              file=sys.stderr)
        raise SystemExit(2)

    from harness import persona
    persona.seed_default_workspace()   # first-run: drop editable templates in the config dir

    from harness.acp_agent import HarnessAgent
    from harness.router import Router, complete
    from harness import skills

    from harness.persona_sessions import resolve_session_model
    shell_env = vibeproxy.model_value(os.environ)   # shell OR .env at this point
    worker_model_id = resolve_session_model(
        workspace_dir.name,
        shell_set_model=shell_set_model,
        shell_env=shell_env,
        dotenv=shell_env,            # post-load_env, .env value (if any) is in this same var
        backend=args.model,
    )

    # Test seam: HARNESS_ROUTER_STUB=1 swaps the live (VibeProxy) classifier for a
    # fixed one, so tests that only exercise downstream behavior (e.g. session
    # replay) don't pay for — or flake on — real network classification. OFF by
    # default; only honored when the env flag is set.
    complete_fn = _stub_complete if os.getenv("HARNESS_ROUTER_STUB") == "1" else complete

    roots = paths.skills_dirs()
    agent = HarnessAgent(
        model_factory=_model_factory(args.model),
        agent_cfg=_load_agent_cfg(),
        skills_dir=roots,                                   # now an ordered list
        router=Router(complete_fn, catalog=skills.load_catalog(roots)),
        worker_model_id=worker_model_id,
        yolo=args.yolo,
        backend=args.model,
        workspace_dir=workspace_dir,
        cwd=cwd,
        shell_set_model=shell_set_model,
        shell_env=vibeproxy.model_value(os.environ),
        debug=debug,
    )
    await acp.run_agent(agent)


def main(argv=None) -> None:
    """Sync entrypoint so the agent is runnable as `python -m harness.acp_main`
    (the TUI launches it this way via sys.executable)."""
    asyncio.run(_main(argv))


if __name__ == "__main__":
    main()
