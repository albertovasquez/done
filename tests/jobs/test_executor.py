# tests/jobs/test_executor.py
import pytest
from harness.jobs import executor as ex, model as m


def _job(agent_id="fred"):
    return m.Job(
        id="j1",
        name="n",
        agent_id=agent_id,
        schedule=m.Every(seconds=60),
        payload=m.AgentTurn(message="do it"),
        grant=m.Grant(tools="inherit", paths="workspace", write=False, exec=False, network=False),
        cost=m.CostGate(timeout_s=5, min_cadence_s=60, max_consecutive_failures=3),
        state=m.JobState(),
    )


def test_resolves_model_and_workspace_from_agent_id():
    calls = {}
    deps = ex.Deps(
        resolve_workspace=lambda pid: (
            (_ for _ in ()).throw(AssertionError) if pid != "fred"
            else __import__("pathlib").Path("/ws/fred")
        ),
        resolve_model=lambda pid, **kw: calls.setdefault("model_pid", pid) and "model-X" or "model-X",
        compose=lambda ws: ("PB", "MB", calls.setdefault("composed_ws", ws)),
        run_turn=lambda *, model_id, workspace, persona_block, memory_block, message: (
            calls.setdefault("ran", (model_id, str(workspace), persona_block, message))
        ),
    )
    ex.run_headless_turn(_job(), deps=deps)
    assert calls["model_pid"] == "fred"                  # model from agent_id, not default
    assert str(calls["composed_ws"]) == "/ws/fred"       # workspace from agent_id
    assert calls["ran"][0] == "model-X" and calls["ran"][3] == "do it"


def test_orphan_persona_raises():
    from harness import persona_select
    deps = ex.Deps(
        resolve_workspace=lambda pid: (
            (_ for _ in ()).throw(persona_select.UnknownPersona(pid))
        ),
        resolve_model=lambda *a, **k: "m",
        compose=lambda ws: ("", "", ws),
        run_turn=lambda **k: None,
    )
    with pytest.raises(ex.OrphanPersona):
        ex.run_headless_turn(_job("ghost"), deps=deps)
