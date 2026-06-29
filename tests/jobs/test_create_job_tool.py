"""Unit tests for harness/tools/create_job.py — the agent-facing create tool."""
import pytest
from harness.tools.create_job import CreateJobTool
from harness.jobs import ops


@pytest.fixture(autouse=True)
def _cron_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("harness.paths.config_dir", lambda: tmp_path)
    return tmp_path


class _Env:
    def __init__(self, persona=None):
        if persona is not None:
            self._active_persona = persona


def _good_args(**over):
    args = {
        "schedule": "0 9 * * *",
        "description": "stand-up reminder",
        "cost": {"timeout_secs": 60, "min_cadence_secs": 86400, "max_consecutive_failures": 3},
        "grant": {"paths": [], "shell": False, "network": False, "tools": []},
    }
    args.update(over)
    return args


def test_valid_spec_creates_a_job():
    tool = CreateJobTool()
    out = tool.execute(_good_args(), _Env(persona="default"))
    assert out["returncode"] == 0
    assert "Created job" in out["output"]
    jobs = ops.list_jobs()
    assert len(jobs) == 1
    assert jobs[0].agent_id == "default"
    assert jobs[0].description == "stand-up reminder"


def test_agent_id_comes_from_env_not_args():
    tool = CreateJobTool()
    # model tries to smuggle agent_id in args — it must be IGNORED
    out = tool.execute(_good_args(agent_id="evil"), _Env(persona="alberto"))
    assert out["returncode"] == 0
    assert ops.list_jobs()[0].agent_id == "alberto"   # env wins


def test_env_without_persona_falls_back_to_default():
    tool = CreateJobTool()
    out = tool.execute(_good_args(), _Env(persona=None))   # no _active_persona attr
    assert out["returncode"] == 0
    assert ops.list_jobs()[0].agent_id == "default"


def test_missing_cost_gate_fails_closed():
    tool = CreateJobTool()
    args = _good_args()
    del args["cost"]
    out = tool.execute(args, _Env(persona="default"))
    assert out["returncode"] == 1
    assert "cost gate" in out["output"] and "fail closed" in out["output"]
    assert ops.list_jobs() == []                          # nothing written


def test_missing_grant_gate_fails_closed():
    tool = CreateJobTool()
    args = _good_args()
    del args["grant"]
    out = tool.execute(args, _Env(persona="default"))
    assert out["returncode"] == 1
    assert "grant" in out["output"]
    assert ops.list_jobs() == []


def test_payload_defaults_to_reminder_from_description():
    tool = CreateJobTool()
    tool.execute(_good_args(description="water the plants"), _Env(persona="default"))
    job = ops.list_jobs()[0]
    from harness.jobs import model as m
    assert isinstance(job.payload, m.Reminder)
    assert job.payload.text == "water the plants"


def test_execute_never_raises_returns_error_dict():
    tool = CreateJobTool()
    # a wholly broken schedule → handle_create_job raises → tool must return rc=1
    out = tool.execute(_good_args(schedule={"not": "a string"}), _Env(persona="default"))
    assert out["returncode"] == 1
    assert "Could not create job" in out["output"]
