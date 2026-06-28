#!/usr/bin/env python3
"""Phase-0 entrypoint: run the vendored agent under the live tracer.

  python3 harness/run_traced.py                 # mock (default), zero cost
  python3 harness/run_traced.py --model vibeproxy --task "fix the add bug"

Run via ./run.sh so PYTHONPATH includes upstream/src.
"""

from __future__ import annotations

import argparse
import os
import platform
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
from harness import memory as _memory  # noqa: E402
from harness import base_prompt  # noqa: E402
from harness.chat_handler import ChatHandler  # noqa: E402
from harness import vibeproxy  # noqa: E402

DEFAULT_TASK = "Fix the failing test in examples/sample-repo so that add(2, 3) == 5."


def _load_agent_config() -> dict:
    cfg = yaml.safe_load((REPO_ROOT / "upstream/src/minisweagent/config/mini.yaml").read_text())
    return cfg["agent"]


def _build_vibeproxy_model(project_cwd=None, memory_root=None):
    # StreamingLitellmModel (not bare LitellmModel) so the standalone CLI advertises
    # the full tool registry too; on_delta defaults None => byte-identical blocking path.
    from harness.streaming_model import StreamingLitellmModel
    from harness.tools.registry import build_registry
    from harness import paths as _paths
    return StreamingLitellmModel(
        model_name=vibeproxy.model_id(vibeproxy.default_model()),
        model_kwargs=vibeproxy.model_kwargs(),
        cost_tracking="ignore_errors",
        # skill_roots => the agent gets a load_skill tool to pull bodies on demand;
        # project_cwd so it can resolve project .agents/.claude skills too.
        # memory_root => the load_memory tool for on-demand fact recall.
        registry=build_registry(skill_roots=_paths.skills_dirs(project_cwd=project_cwd),
                                memory_root=memory_root),
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
    try:
        cls = router.classify(prompt)
    except Exception as e:  # noqa: BLE001 — record the failure in the trace, then re-raise
        # The trace IS the point of this CLI; a run that died classifying must
        # leave a record, not just a stderr line main() prints.
        emitter.emit("task.classify_failed", error=str(e))
        raise
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
    try:
        run_agent(prompt, skill_block=load.block)
    except Exception as e:  # noqa: BLE001 — record the crash in the trace, then re-raise
        emitter.emit("run.failed", error=str(e))
        raise
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase-0 traced mini-swe-agent")
    parser.add_argument("--model", choices=["mock", "vibeproxy"], default="mock")
    parser.add_argument("--task", default=DEFAULT_TASK)
    parser.add_argument("--cwd", default=str(REPO_ROOT / "examples" / "sample-repo"))
    parser.add_argument("--persona", default=None,
                        help="persona workspace id to run as (default: the built-in default)")
    args = parser.parse_args(argv)

    import sys as _sys
    from harness import persona_select as _persona_select
    try:
        workspace_dir = _persona_select.resolve_workspace(args.persona)
    except _persona_select.InvalidPersonaId as e:
        print(f'invalid persona id "{e}" — use only letters, digits, - or _',
              file=_sys.stderr)
        raise SystemExit(2)
    except _persona_select.UnknownPersona as e:
        print(f'no persona "{e}"', file=_sys.stderr)
        raise SystemExit(2)

    load_dotenv(REPO_ROOT / ".env")  # explicit: mini's own load targets the global dir
    _persona.seed_default_workspace()  # first-run: drop editable templates in the config dir

    run_dir = REPO_ROOT / "harness" / "runs" / _run_id()
    run_dir.mkdir(parents=True, exist_ok=True)

    worker_model_id = None if args.model == "mock" else vibeproxy.default_model()

    if args.model == "mock":
        model = build_mock_model()
    else:
        model = _build_vibeproxy_model(project_cwd=args.cwd)
    env = LocalEnvironment(cwd=args.cwd)
    agent_cfg = _load_agent_config()
    agent_cfg["output_path"] = str(run_dir / "traj.json")
    emitter = Emitter(run_dir / "events.jsonl", clock=lambda: 0.0, console=True)
    from datetime import date
    from harness import paths as _paths
    from harness import flows as _flows
    from harness import persona_config as _persona_config
    persona_block = _persona.resolve_persona(workspace_dir).block
    memory_block = _memory.resolve_memory(workspace_dir, today=date.today()).block
    # Lazy skill discovery: the agent gets a flow-scoped MENU (names+descriptions)
    # and pulls bodies on demand via load_skill. No flows on the persona => full
    # catalog, no gating (no-op vs. before).
    skills_roots = _paths.skills_dirs(project_cwd=args.cwd)   # project .agents/.claude skills too
    _catalog_load = skills.load_catalog_with_skips(skills_roots)
    _full_catalog = _catalog_load.skills
    _skipped_skills = _catalog_load.skipped       # surfaced in the capability answer
    _shadowed_skills = _catalog_load.shadowed     # name clashes across roots (later won)
    _enabled_flows = _persona_config.read_flows(workspace_dir)
    _menu_metas = (_flows.scope_catalog(_full_catalog, _enabled_flows)
                   if _enabled_flows else _full_catalog)
    # Three-tier AGENTS.md (persona > project > global), folded into base_block so
    # both the agent runner and the chat handler inherit it. No-op when no files.
    from harness import agents as _agents
    _agents_block = _agents.resolve_agents(
        persona_dir=workspace_dir, project_cwd=args.cwd,
        global_dir=_paths.config_dir()).block
    base_block = base_prompt.render_base_prompt(
        model_id=(worker_model_id or "mock"),
        cwd=args.cwd,
        system_line=platform.platform(),
        skills_menu=skills.compose_menu(_menu_metas),
        agents_block=_agents_block)

    def run_agent(prompt, skill_block=""):
        runner = MiniSweAgentRunner(model, env, agent_cfg=agent_cfg)
        try:
            for event in runner.run(prompt, skill_block=skill_block,
                                    persona_block=persona_block,
                                    memory_block=memory_block,
                                    base_block=base_block):
                emitter.write_renumbered(event)
        except KeyboardInterrupt:
            print("\ninterrupted", file=sys.stderr)
        # NOTE: a run crash is NOT caught here — route_and_dispatch wraps the
        # run_agent call and emits run.failed (the single trace seam), then it
        # propagates to main()'s handler. (KeyboardInterrupt stays local: a
        # Ctrl-C is a user abort, not a traced failure.)

    # Router classifies against the same flow-scoped catalog the agent sees.
    router = Router(complete, catalog=_menu_metas)
    try:
        rc = route_and_dispatch(
            args.task, router=router, emitter=emitter,
            make_chat_handler=lambda: ChatHandler(worker_model_id, catalog=router.catalog,
                                                  persona_block=persona_block + memory_block,
                                                  base_block=base_block,
                                                  skipped=_skipped_skills,
                                                  shadowed=_shadowed_skills),
            run_agent=run_agent, ask_user=input, echo=print,
            worker_model_id=worker_model_id,
            load_skills=lambda names: skills.compose(skills_roots, names))
    except Exception as e:  # noqa: BLE001 — classify OR run failure (both traced in route_and_dispatch)
        print(f"\nRun failed: {e}\n"
              f"Is VibeProxy running on {vibeproxy.base_url()}? "
              f"(the router uses VibeProxy even when --model is mock)", file=sys.stderr)
        rc = 1
    finally:
        emitter.close()
        print(f"\nevents:     {run_dir / 'events.jsonl'}")
        print(f"trajectory: {run_dir / 'traj.json'}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
