"""SubagentTool: spawn ephemeral low-context workers in parallel for focused
single-item tasks, return a structured-summary digest. Workers run AS the parent
persona (env._active_persona) with a fresh conversation, restricted toolset, and a
cheaper model. Depth-1: workers never get the subagent tool (registry is_worker).

Guardrails: per-worker step_limit (turn cap) + wall_time; NOT cost (upstream
GLOBAL_MODEL_STATS is process-global). Pool is per-call. Hard MAX_TASKS_PER_CALL."""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

from harness import vibeproxy
from harness.agent_build import build_persona_agent
from harness.subagent_config import resolve_subagent_model, subagent_max_concurrent
from harness.tools.subagent_prompt import build_worker_task

MAX_TASKS_PER_CALL = 16
DEFAULT_WORKER_TOOLSET = {"read", "bash"}
DEFAULT_STEP_LIMIT = 15

SUBAGENT_TOOL = {
    "type": "function",
    "function": {
        "name": "subagent",
        "description": (
            "Delegate one or more FOCUSED tasks to fresh low-context worker agents "
            "that run in parallel and return a structured summary. A worker does NOT "
            "see this conversation — put everything it needs in `context`. Default "
            "tools are read-only (read, bash); grant write/edit per task via `tools`. "
            "Use for parallel single-item investigation/work on a cheaper model."),
        "parameters": {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "goal": {"type": "string"},
                            "context": {"type": "string"},
                            "tools": {"type": "array", "items": {"type": "string"}},
                            "model": {"type": "string"},
                            "max_iterations": {"type": "integer"},
                        },
                        "required": ["goal", "context"],
                    },
                },
            },
            "required": ["tasks"],
        },
    },
}


def _run_one_worker(task: dict, env, *, agent_id: str):
    """Build + run ONE worker. Returns (ok: bool, text: str). Raising is caught by
    the caller and rendered as a failed entry (sibling isolation)."""
    # parent model: env override, else the engine default — NEVER silently mock.
    # (Matches persona_sessions.resolve_session_model's ladder semantics: a real
    #  worker inherits a real model even when VIBEPROXY_MODEL isn't in the env.)
    parent_model = vibeproxy.default_model()
    model_name = resolve_subagent_model(
        agent_id, per_task=task.get("model"), parent_model=parent_model)

    toolset = set(task.get("tools") or DEFAULT_WORKER_TOOLSET)
    step_limit = int(task.get("max_iterations") or DEFAULT_STEP_LIMIT)
    remaining = getattr(env, "_remaining_secs", None)

    import yaml
    from harness.paths import mini_yaml_path
    # Resolve mini.yaml via find_spec (install-layout agnostic) rather than a
    # hardcoded upstream/ disk path, which does not exist in a wheel install (#104).
    agent_cfg = yaml.safe_load(mini_yaml_path().read_text())["agent"]

    if remaining is not None:
        _default_wt = agent_cfg.get("wall_time_limit_seconds", 0) or remaining
        wall_time = min(_default_wt, remaining)
    else:
        wall_time = None

    workspace_cwd = getattr(env, "config", None)
    cwd = getattr(workspace_cwd, "cwd", None) or os.getcwd()

    runner, _ = build_persona_agent(
        agent_id=agent_id,
        model_name=model_name,
        cwd=cwd,
        skill_roots=None,            # worker: no skills menu (load_skill only if granted)
        memory_root=None,            # worker: no memory block (load_memory gated elsewhere)
        agent_cfg=agent_cfg,
        toolset=toolset,
        is_worker=True,
        step_limit=step_limit,
        wall_time_limit=wall_time,
    )

    task_str = build_worker_task(task["goal"], task["context"])
    for _ in runner.run(task_str):
        pass
    res = runner.result
    summary = (res.submission or "").strip() if res else ""
    ok = bool(res and res.ok)
    if not ok:
        return (False, (res.error if res and res.error else res.exit_status if res else "unknown"))
    return (True, summary or "(no summary returned)")


def _format_digest(results: list[tuple[bool, str]], goals: list[str]) -> str:
    n = len(results)
    blocks = []
    for i, ((ok, text), goal) in enumerate(zip(results, goals), start=1):
        mark = "✓" if ok else "✗"
        head = f"[subagent {i}/{n} {mark}] goal: {goal!r}"
        body = text if ok else f"failed: {text}"
        blocks.append(f"{head}\n{body}")
    return "\n\n".join(blocks)


class SubagentTool:
    name = "subagent"
    schema = SUBAGENT_TOOL

    def display_label(self, args: dict) -> str:
        tasks = args.get("tasks") or []
        return f"subagent ({len(tasks)} task{'s' if len(tasks) != 1 else ''})"

    def execute(self, args: dict, env) -> dict:
        agent_id = getattr(env, "_active_persona", None) or "default"
        tasks = args.get("tasks") or []
        if len(tasks) > MAX_TASKS_PER_CALL:
            return {"output": f"Too many tasks ({len(tasks)}); max is "
                              f"{MAX_TASKS_PER_CALL} per call.",
                    "returncode": 1, "exception_info": None}
        if not tasks:
            return {"output": "No tasks provided.", "returncode": 1,
                    "exception_info": None}

        goals = [t.get("goal", "") for t in tasks]

        def _safe(task):
            try:
                return _run_one_worker(task, env, agent_id=agent_id)
            except BaseException as e:  # sibling isolation
                return (False, f"{type(e).__name__}: {e}")

        max_workers = min(subagent_max_concurrent(), len(tasks))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            results = list(pool.map(_safe, tasks))

        return {"output": _format_digest(results, goals), "returncode": 0,
                "exception_info": None}
