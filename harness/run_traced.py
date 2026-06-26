#!/usr/bin/env python3
"""Phase-0 entrypoint: run the vendored agent under the live tracer.

  python3 harness/run_traced.py                 # mock (default), zero cost
  python3 harness/run_traced.py --model vibeproxy --task "fix the add bug"

Run via ./run.sh so PYTHONPATH includes upstream/src.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import yaml
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "upstream" / "src"))
sys.path.insert(0, str(REPO_ROOT))

from minisweagent.environments.local import LocalEnvironment  # noqa: E402

from harness.events import Emitter  # noqa: E402
from harness.models_mock import build_mock_model  # noqa: E402
from harness.runner import MiniSweAgentRunner  # noqa: E402
from harness.router import Router, complete  # noqa: E402
from harness import skills  # noqa: E402
from harness import persona as _persona  # noqa: E402
from harness import paths as _paths_persona  # noqa: E402
from harness.chat_handler import ChatHandler  # noqa: E402

DEFAULT_TASK = "Fix the failing test in examples/sample-repo so that add(2, 3) == 5."
DEFAULT_VIBEPROXY_MODEL = "gpt-5.4"  # single source of truth; gpt-5.1-codex does not exist on this proxy


def _load_agent_config() -> dict:
    cfg = yaml.safe_load((REPO_ROOT / "upstream/src/minisweagent/config/mini.yaml").read_text())
    return cfg["agent"]


def _build_vibeproxy_model():
    from minisweagent.models.litellm_model import LitellmModel
    return LitellmModel(
        model_name="openai/" + os.getenv("VIBEPROXY_MODEL", DEFAULT_VIBEPROXY_MODEL),
        model_kwargs={
            "api_base": os.getenv("VIBEPROXY_BASE_URL", "http://localhost:8317/v1"),
            "api_key": os.getenv("VIBEPROXY_API_KEY", "dummy-not-used"),
        },
        cost_tracking="ignore_errors",
    )


def _run_id() -> str:
    # No Date.now in scripts? This is a real process; time is fine here.
    return time.strftime("%Y%m%d-%H%M%S")


def route_and_dispatch(prompt, *, router, emitter, make_chat_handler, run_agent,
                       ask_user, echo, worker_model_id,
                       load_skills=lambda names: skills.SkillLoad()) -> int:
    """Classify the prompt, clarify once if unclear, then dispatch.

    The caller owns the emitter's lifecycle (open/close); this function only
    emits through it. Returns an exit code (0 on a handled/declined request).
    """
    cls = router.classify(prompt)
    emitter.emit("task.classified", task_type=cls.task_type, skills=cls.skills,
                 confidence=cls.confidence, suggested_model=cls.suggested_model)
    if cls.needs_clarification:
        try:
            answer = ask_user(cls.clarifying_question or "Please clarify:")
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if not answer.strip():
            echo("no clarification provided — not running the agent.")
            return 0
        cls = router.classify(prompt + "\n\n[clarification]: " + answer)
        # Re-emit so the trace reflects the decision the run ACTUALLY dispatched
        # on (not the pre-clarification guess) — observability is the point here.
        emitter.emit("task.classified", task_type=cls.task_type, skills=cls.skills,
                     confidence=cls.confidence, suggested_model=cls.suggested_model)
    if cls.suggested_model and cls.suggested_model != worker_model_id:
        echo(f"(router suggests model '{cls.suggested_model}'; using your '{worker_model_id}')")
    if cls.task_type == "chat_question":
        # this CLI prints to a plain console (not the streaming TUI), so join the
        # streamed pieces into one string.
        echo("".join(make_chat_handler().answer_stream(prompt)))
        return 0
    if cls.task_type == "ambiguous":
        echo("still unclear after clarification — not running the agent; please rephrase.")
        return 0
    load = load_skills(cls.skills)
    emitter.emit("skill.load", injected=load.injected, skipped=load.skipped)
    if cls.skills:
        echo(f"skills: injected {load.injected}, skipped {load.skipped}")
    run_agent(prompt, skill_block=load.block)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase-0 traced mini-swe-agent")
    parser.add_argument("--model", choices=["mock", "vibeproxy"], default="mock")
    parser.add_argument("--task", default=DEFAULT_TASK)
    parser.add_argument("--cwd", default=str(REPO_ROOT / "examples" / "sample-repo"))
    args = parser.parse_args(argv)

    load_dotenv(REPO_ROOT / ".env")  # explicit: mini's own load targets the global dir
    _persona.seed_default_workspace()  # first-run: drop editable templates in the config dir

    run_dir = REPO_ROOT / "harness" / "runs" / _run_id()
    run_dir.mkdir(parents=True, exist_ok=True)

    worker_model_id = None if args.model == "mock" else os.getenv("VIBEPROXY_MODEL", DEFAULT_VIBEPROXY_MODEL)

    if args.model == "mock":
        model = build_mock_model()
    else:
        model = _build_vibeproxy_model()
    env = LocalEnvironment(cwd=args.cwd)
    agent_cfg = _load_agent_config()
    agent_cfg["output_path"] = str(run_dir / "traj.json")
    emitter = Emitter(run_dir / "events.jsonl", clock=lambda: 0.0, console=True)
    persona_block = _persona.resolve_persona(_paths_persona.default_workspace_dir()).block

    def run_agent(prompt, skill_block=""):
        runner = MiniSweAgentRunner(model, env, agent_cfg=agent_cfg)
        try:
            for event in runner.run(prompt, skill_block=skill_block,
                                    persona_block=persona_block):
                emitter.write_renumbered(event)
        except KeyboardInterrupt:
            print("\ninterrupted", file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            if args.model == "vibeproxy":
                print(f"\nVibeProxy run failed: {e}\n"
                      f"Is VibeProxy running on {os.getenv('VIBEPROXY_BASE_URL', 'http://localhost:8317/v1')}?",
                      file=sys.stderr)
            else:
                raise

    from harness import paths as _paths
    skills_roots = _paths.skills_dirs()
    router = Router(complete, catalog=skills.load_catalog(skills_roots))
    try:
        rc = route_and_dispatch(
            args.task, router=router, emitter=emitter,
            make_chat_handler=lambda: ChatHandler(worker_model_id, catalog=router.catalog,
                                                  persona_block=persona_block),
            run_agent=run_agent, ask_user=input, echo=print,
            worker_model_id=worker_model_id,
            load_skills=lambda names: skills.compose(skills_roots, names))
    except Exception as e:  # noqa: BLE001 — router model unreachable etc.
        print(f"\nRouter failed: {e}\n"
              f"Is VibeProxy running on {os.getenv('VIBEPROXY_BASE_URL', 'http://localhost:8317/v1')}? "
              f"(the router uses VibeProxy even when --model is mock)", file=sys.stderr)
        rc = 1
    finally:
        emitter.close()
        print(f"\nevents:     {run_dir / 'events.jsonl'}")
        print(f"trajectory: {run_dir / 'traj.json'}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
