"""build_persona_agent(): the single agent-construction chokepoint.

Owns model + env + runner + registry construction ONLY. Compose/skills/base_block
stay in callers (cron executor, run_traced) and arrive via skill_roots/memory_root/
agent_cfg. Shared by the cron path and the subagent worker path so a worker can
never drift from how a real persona turn is built.

Stamps env._active_persona = agent_id UNCONDITIONALLY so tools (create_job,
subagent) bind to the right persona on every launch surface (the bare cron
LocalEnvironment did not stamp it before).
"""
from __future__ import annotations

from pathlib import Path

from harness.runner import MiniSweAgentRunner
from harness.tools.registry import build_registry


def build_persona_agent(
    agent_id: str,
    *,
    model_name: str | None = None,
    model_kwargs: dict | None = None,
    cwd: str | None = None,
    skill_roots: list[Path] | None = None,
    memory_root: Path | None = None,
    agent_cfg: dict,
    toolset: set[str] | None = None,
    is_worker: bool = False,
    step_limit: int | None = None,
    wall_time_limit: int | None = None,
) -> tuple[MiniSweAgentRunner, list]:
    # Fresh registry — handed to BOTH model (schemas) and agent (dispatch).
    registry = build_registry(
        skill_roots=skill_roots, memory_root=memory_root,
        toolset=toolset, is_worker=is_worker,
    )

    # Fresh model per call. Mock when no model_name (persona-fidelity rule #1:
    # never vibeproxy.default_model()).
    if model_name is None:
        from harness.models_mock import build_mock_model
        model = build_mock_model()
    else:
        from harness import vibeproxy as _vp
        from harness.streaming_model import StreamingLitellmModel
        model = StreamingLitellmModel(
            model_name=_vp.model_id(model_name),
            model_kwargs=(model_kwargs if model_kwargs is not None else _vp.model_kwargs()),
            cost_tracking="ignore_errors",
            registry=registry,
        )

    # Fresh env per call; stamp the persona so env-bound tools resolve agent_id.
    from minisweagent.environments.local import LocalEnvironment  # noqa: E402
    env = LocalEnvironment(cwd=cwd) if cwd else LocalEnvironment()
    env._active_persona = agent_id

    # Apply per-worker caps into a COPY of agent_cfg (never mutate the caller's dict).
    cfg = dict(agent_cfg)
    if step_limit is not None:
        cfg["step_limit"] = step_limit
    if wall_time_limit is not None:
        cfg["wall_time_limit_seconds"] = wall_time_limit

    runner = MiniSweAgentRunner(model, env, agent_cfg=cfg)
    return runner, registry
