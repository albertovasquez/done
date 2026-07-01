from pathlib import Path
from harness.jobs.executor import run_headless_turn, Deps
from harness.jobs.model import Job, JobState, Dynamic, AgentTurn, Grant, CostGate


def _job():
    return Job(
        id="j1", name="loop", agent_id="a",
        schedule=Dynamic(), payload=AgentTurn(message="hi"),
        grant=Grant(tools=[], paths=[], write=False, exec=False, network=False),
        cost=CostGate(timeout_s=0, min_cadence_s=0, max_consecutive_failures=3),
        state=JobState(),
    )


def _deps(run_turn):
    return Deps(
        resolve_workspace=lambda aid: Path("/tmp/ws"),
        resolve_model=lambda aid, **kw: "mock",
        compose=lambda ws: ("P", "M", ws),
        run_turn=run_turn,
    )


def test_run_headless_turn_returns_override():
    # A run_turn that "chose" 120s — return it, as the production run_turn does.
    deps = _deps(lambda **kw: 120)
    assert run_headless_turn(_job(), deps=deps) == 120


def test_run_headless_turn_none_when_no_reschedule():
    deps = _deps(lambda **kw: None)
    assert run_headless_turn(_job(), deps=deps) is None


def test_set_next_run_in_headless_registry():
    from harness.tools.registry import build_registry
    names = {t.name for t in build_registry()}
    assert "set_next_run" in names
