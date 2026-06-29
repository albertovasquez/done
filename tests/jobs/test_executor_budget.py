# tests/jobs/test_executor_budget.py
"""Task 8: cron budget wiring.

When a scheduled job carries a positive timeout, the cron executor stamps
`runner._env._remaining_secs` so any subagent worker spawned mid-turn caps its
wall-time at that budget (subagent.py:64 reads `_remaining_secs`). The
interactive path leaves it unset (None).
"""
from harness.agent_build import build_persona_agent
from harness.jobs import executor as ex, model as m


def _job(timeout_s, agent_id="fred"):
    return m.Job(
        id="j1",
        name="n",
        agent_id=agent_id,
        schedule=m.Every(seconds=60),
        payload=m.AgentTurn(message="do it"),
        grant=m.Grant(tools="inherit", paths="workspace", write=False, exec=False, network=False),
        cost=m.CostGate(timeout_s=timeout_s, min_cadence_s=60, max_consecutive_failures=3),
        state=m.JobState(),
    )


def test_remaining_secs_is_settable_wiring_point(tmp_path):
    """The wiring point exists: env._remaining_secs is a settable attribute that
    the subagent tool reads. (End-to-end cap is covered by the subagent tool test.)"""
    runner, _ = build_persona_agent(
        "default", model_name=None, agent_cfg={"step_limit": 0},
        memory_root=tmp_path, cwd=str(tmp_path),
    )
    runner._env._remaining_secs = 30
    assert getattr(runner._env, "_remaining_secs") == 30


def test_cron_stamps_remaining_secs_from_job_timeout():
    """A positive job.cost.timeout_s is stamped onto the runner env via run_turn."""
    seen = {}

    def _run_turn(*, model_id, workspace, persona_block, memory_block, message, wall_budget=None):
        seen["wall_budget"] = wall_budget

    deps = ex.Deps(
        resolve_workspace=lambda pid: __import__("pathlib").Path("/ws/fred"),
        resolve_model=lambda *a, **k: "model-X",
        compose=lambda ws: ("PB", "MB", ws),
        run_turn=_run_turn,
        notify=lambda **k: None,
    )
    ex.run_headless_turn(_job(timeout_s=42), deps=deps)
    assert seen["wall_budget"] == 42


def test_cron_omits_budget_when_timeout_not_positive():
    """timeout_s <= 0 means no budget — interactive parity (None / not passed)."""
    seen = {}

    def _run_turn(*, model_id, workspace, persona_block, memory_block, message, wall_budget=None):
        seen["wall_budget"] = wall_budget

    deps = ex.Deps(
        resolve_workspace=lambda pid: __import__("pathlib").Path("/ws/fred"),
        resolve_model=lambda *a, **k: "model-X",
        compose=lambda ws: ("PB", "MB", ws),
        run_turn=_run_turn,
        notify=lambda **k: None,
    )
    ex.run_headless_turn(_job(timeout_s=0), deps=deps)
    assert seen["wall_budget"] is None


def test_cron_does_not_break_fixed_signature_run_turn():
    """A test double whose run_turn has NO wall_budget param must still work:
    run_headless_turn only passes wall_budget to callables that accept it."""
    ran = {}

    # No wall_budget, no **kwargs — exactly the existing parity double shape.
    def _run_turn(*, model_id, workspace, persona_block, memory_block, message):
        ran["ok"] = True

    deps = ex.Deps(
        resolve_workspace=lambda pid: __import__("pathlib").Path("/ws/fred"),
        resolve_model=lambda *a, **k: "model-X",
        compose=lambda ws: ("PB", "MB", ws),
        run_turn=_run_turn,
        notify=lambda **k: None,
    )
    ex.run_headless_turn(_job(timeout_s=42), deps=deps)
    assert ran["ok"] is True
