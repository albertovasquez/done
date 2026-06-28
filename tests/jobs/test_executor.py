# tests/jobs/test_executor.py
import pytest
from harness.jobs import executor as ex, model as m


def _job(agent_id="fred", payload=None):
    return m.Job(
        id="j1",
        name="n",
        agent_id=agent_id,
        schedule=m.Every(seconds=60),
        payload=payload or m.AgentTurn(message="do it"),
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
        notify=lambda **k: None,
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
        notify=lambda **k: None,
    )
    with pytest.raises(ex.OrphanPersona):
        ex.run_headless_turn(_job("ghost"), deps=deps)


def test_reminder_does_not_run_turn():
    """A Reminder payload must call deps.notify, NOT deps.run_turn."""
    notify_calls = []

    def _assert_run_turn_not_called(**k):
        raise AssertionError("run_turn must NOT be called for a Reminder payload")

    deps = ex.Deps(
        resolve_workspace=lambda pid: __import__("pathlib").Path("/ws/fred"),
        resolve_model=lambda *a, **k: "model-X",
        compose=lambda ws: ("PB", "MB", ws),
        run_turn=_assert_run_turn_not_called,
        notify=lambda *, text, agent_id: notify_calls.append({"text": text, "agent_id": agent_id}),
    )

    job = _job(agent_id="fred", payload=m.Reminder(text="ping"))
    ex.run_headless_turn(job, deps=deps)

    assert len(notify_calls) == 1, "notify should be called exactly once"
    assert notify_calls[0]["text"] == "ping"
    assert notify_calls[0]["agent_id"] == "fred"


def test_default_deps_constructs():
    """_default_deps() wires the real harness functions without NameErrors.

    The other executor tests inject Deps, so they never exercise _default_deps —
    this catches a missing import / typo in the now-more-complex compose+run_turn
    wiring without needing a live engine (no turn is run)."""
    deps = ex._default_deps()
    assert isinstance(deps, ex.Deps)
    assert callable(deps.resolve_workspace)
    assert callable(deps.resolve_model)
    assert callable(deps.compose)
    assert callable(deps.run_turn)
    assert callable(deps.notify)


def test_reminder_orphan_persona_raises():
    """Even for a Reminder, an orphaned persona should raise OrphanPersona."""
    from harness import persona_select

    notify_calls = []
    deps = ex.Deps(
        resolve_workspace=lambda pid: (
            (_ for _ in ()).throw(persona_select.UnknownPersona(pid))
        ),
        resolve_model=lambda *a, **k: "m",
        compose=lambda ws: ("", "", ws),
        run_turn=lambda **k: None,
        notify=lambda **k: notify_calls.append(k),
    )

    job = _job(agent_id="ghost", payload=m.Reminder(text="ping"))
    with pytest.raises(ex.OrphanPersona):
        ex.run_headless_turn(job, deps=deps)

    # notify should NOT have been called — the orphan check comes first
    assert len(notify_calls) == 0
