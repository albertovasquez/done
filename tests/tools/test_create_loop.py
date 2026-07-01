import types
from harness.tools.create_loop import CreateLoopTool


class _Recorder:
    def __init__(self): self.spec = None
    def __call__(self, spec, *, now):
        self.spec = spec
        return {"id": spec["id"], "name": spec["name"]}


def _env(persona="alice"):
    return types.SimpleNamespace(_active_persona=persona)


def test_builds_dynamic_agent_turn_bound_to_persona(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr("harness.tools.create_loop.handle_create_job", rec)
    res = CreateLoopTool().execute({
        "message": "check the deploy",
        "description": "deploy watcher",
        "cost": {"timeout_secs": 120, "min_cadence_secs": 60,
                 "max_consecutive_failures": 3},
        "grant": {"paths": [], "shell": False, "network": True},
    }, _env("alice"))
    assert res["returncode"] == 0
    assert rec.spec["agent_id"] == "alice"
    assert rec.spec["schedule"] == {"kind": "dynamic"}
    assert rec.spec["payload"] == {"kind": "agent_turn", "message": "check the deploy"}
    assert rec.spec["cost"]["timeout_s"] == 120        # normalized key
    assert rec.spec["cost"]["min_cadence_s"] == 60


def test_gate_failure_returns_returncode_1(monkeypatch):
    def boom(spec, *, now): raise ValueError("grant required (fail closed)")
    monkeypatch.setattr("harness.tools.create_loop.handle_create_job", boom)
    res = CreateLoopTool().execute({"message": "x", "cost": {}, "grant": {}},
                                   _env())
    assert res["returncode"] == 1
    assert "grant required" in res["output"]


def test_name_and_schema():
    t = CreateLoopTool()
    assert t.name == "create_loop"
    assert t.schema["function"]["name"] == "create_loop"


def test_in_normal_registry_but_denied_for_workers():
    # create_loop is an autonomy-escalation primitive like subagent: present for a
    # normal agent, denied for a depth-1 worker.
    from harness.tools.registry import build_registry
    assert "create_loop" in {t.name for t in build_registry()}
    assert "create_loop" not in {t.name for t in build_registry(is_worker=True)}
    # set_next_run is harmless for a worker, so it stays available.
    assert "set_next_run" in {t.name for t in build_registry(is_worker=True)}
