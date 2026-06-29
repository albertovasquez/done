import harness.tools.subagent as sub
from harness.tools.subagent import SubagentTool


class _FakeEnv:
    _active_persona = "default"


def _patch_worker(monkeypatch, fn):
    # Replace the single-worker runner so tests don't spin a real engine.
    monkeypatch.setattr(sub, "_run_one_worker", fn)


def test_schema_shape():
    t = SubagentTool()
    assert t.name == "subagent"
    assert t.schema["function"]["name"] == "subagent"
    assert "tasks" in t.schema["function"]["parameters"]["properties"]


def test_runs_all_tasks_and_digests(monkeypatch):
    def fake(task, env, *, agent_id):  # returns (ok, summary_or_error)
        return (True, f"summary for {task['goal']}")
    _patch_worker(monkeypatch, fake)
    out = SubagentTool().execute(
        {"tasks": [{"goal": "A", "context": "c"}, {"goal": "B", "context": "c"}]},
        _FakeEnv())
    assert out["returncode"] == 0
    assert "summary for A" in out["output"]
    assert "summary for B" in out["output"]
    assert "1/2" in out["output"] and "2/2" in out["output"]


def test_one_failure_does_not_abort_siblings(monkeypatch):
    def fake(task, env, *, agent_id):
        if task["goal"] == "bad":
            raise RuntimeError("boom")
        return (True, "ok")
    _patch_worker(monkeypatch, fake)
    out = SubagentTool().execute(
        {"tasks": [{"goal": "good", "context": "c"}, {"goal": "bad", "context": "c"}]},
        _FakeEnv())
    # Tool still succeeds; failure is in the text with a ✗.
    assert out["returncode"] == 0
    assert "✓" in out["output"] and "✗" in out["output"]
    assert "boom" in out["output"]


def test_rejects_over_hard_cap(monkeypatch):
    tasks = [{"goal": str(i), "context": "c"} for i in range(sub.MAX_TASKS_PER_CALL + 1)]
    out = SubagentTool().execute({"tasks": tasks}, _FakeEnv())
    assert out["returncode"] == 1
    assert "too many" in out["output"].lower()


def test_empty_tasks_returns_error():
    out = SubagentTool().execute({"tasks": []}, _FakeEnv())
    assert out["returncode"] == 1


def test_concurrency_isolation_no_crosstalk(monkeypatch):
    # N concurrent mock workers must each return their OWN goal (no shared state).
    def fake(task, env, *, agent_id):
        return (True, f"[{task['goal']}]")
    _patch_worker(monkeypatch, fake)
    tasks = [{"goal": f"g{i}", "context": "c"} for i in range(8)]
    out = SubagentTool().execute({"tasks": tasks}, _FakeEnv())
    for i in range(8):
        assert f"[g{i}]" in out["output"]


def test_worker_uses_real_model_when_env_var_unset(monkeypatch):
    # Bug regression: with VIBEPROXY_MODEL unset, the worker must still resolve a
    # REAL model (engine default), not silently fall back to mock (model_name=None).
    monkeypatch.delenv("VIBEPROXY_MODEL", raising=False)
    captured = {}

    class _FakeRunner:
        result = type("R", (), {"submission": "done", "ok": True, "error": None, "exit_status": "ok"})()

        def run(self, task_str):
            return iter(())  # empty generator; no real engine

    def _fake_build(**kwargs):
        captured["model_name"] = kwargs.get("model_name")
        return _FakeRunner(), []

    monkeypatch.setattr(sub, "build_persona_agent", _fake_build)

    env = _FakeEnv()
    sub._run_one_worker({"goal": "g", "context": "c"}, env, agent_id="default")
    assert captured["model_name"] is not None, (
        "model_name must be a real model string, not None (which triggers mock path)"
    )
    assert captured["model_name"] == "gpt-5.4", (
        f"expected vibeproxy.DEFAULT_MODEL 'gpt-5.4', got {captured['model_name']!r}"
    )
