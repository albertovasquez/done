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
    when HARNESS_ROUTER_STUB=1). Returns a fixed chat_question classification as
    JSON — no VibeProxy call — so tests are fast and non-flaky."""
    import json
    return json.dumps({
        "task_type": "chat_question",
        "skills": [],
        "confidence": 1.0,
        "suggested_model": None,
        "reasoning": "stubbed classification (HARNESS_ROUTER_STUB)",
    })


def _model_factory(model_choice: str):
    """Return a factory `make(current_model=None) -> Model`. The agent calls it
    per turn with its current worker model so /models can hot-swap (the arg wins
    over the env default). Mock ignores the arg."""
    if model_choice == "mock":
        from harness.models_mock import build_mock_model

        def make(current_model=None):
            return build_mock_model()
        return make
    # vibeproxy path — api_base/api_key live in model_kwargs (LitellmModelConfig has
    # no top-level api_base/api_key fields); mirror run_traced.py's proven wiring.
    def make(current_model=None):
        from harness.streaming_model import StreamingLitellmModel
        model_id = current_model or os.getenv("VIBEPROXY_MODEL", "gpt-5.4")
        return StreamingLitellmModel(
            model_name="openai/" + model_id,
            model_kwargs={
                "api_base": os.getenv("VIBEPROXY_BASE_URL", "http://localhost:8317/v1"),
                "api_key": os.getenv("VIBEPROXY_API_KEY", "dummy-not-used"),
            },
            cost_tracking="ignore_errors",
        )
    return make


async def _main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="ACP harness agent")
    parser.add_argument("--model", choices=["mock", "vibeproxy"], default="mock")
    parser.add_argument("--cwd", default=None,
                        help="project dir the agent operates on (anchors .env)")
    parser.add_argument("--yolo", action="store_true",
                        help="auto-allow every command without prompting (no permission modal)")
    args = parser.parse_args(argv)

    cwd = str(Path(args.cwd).resolve()) if args.cwd else os.getcwd()
    paths.load_env(cwd)               # BEFORE importing engine-touching modules

    from harness.acp_agent import HarnessAgent
    from harness.router import Router, complete
    from harness import skills

    worker_model_id = None if args.model == "mock" else os.getenv("VIBEPROXY_MODEL", "gpt-5.4")

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
        workspace_dir=paths.default_workspace_dir(),
    )
    await acp.run_agent(agent)


def main(argv=None) -> None:
    """Sync entrypoint so the agent is runnable as `python -m harness.acp_main`
    (the TUI launches it this way via sys.executable)."""
    asyncio.run(_main(argv))


if __name__ == "__main__":
    main()
