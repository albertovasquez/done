"""Tests for handle_create_job — the SINGLE-DOOR privileged path that writes a job.

Tests the handler function directly (handle_create_job(spec, *, now=...)) plus
the "harness/create_job" ext-method registration in acp_agent.  The ACP wire
round-trip is NOT tested here (deferred).
"""
import pytest
from harness.jobs import ops
from harness.acp_agent import handle_create_job

# ---------------------------------------------------------------------------
# Shared spec factories
# ---------------------------------------------------------------------------

def _base_spec(**kw):
    spec = {
        "id": "test-job-1",
        "name": "Test Job",
        "agent_id": "fred",
        "schedule": {"kind": "every", "seconds": 60},
        "payload": {"kind": "reminder", "text": "ping"},
        "grant": {
            "tools": "inherit",
            "paths": "workspace",
            "write": False,
            "exec": False,
            "network": False,
        },
        "cost": {
            "timeout_s": 30,
            "min_cadence_s": 60,
            "max_consecutive_failures": 3,
        },
    }
    spec.update(kw)
    return spec


# ---------------------------------------------------------------------------
# Store isolation (same pattern as tests/jobs/test_ops.py)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr("harness.paths.config_dir", lambda: tmp_path)


# ---------------------------------------------------------------------------
# Gate: fail-closed when required fields are missing
# ---------------------------------------------------------------------------

def test_missing_agent_id_raises():
    spec = _base_spec()
    del spec["agent_id"]
    with pytest.raises(ValueError, match="agent_id"):
        handle_create_job(spec, now=100.0)


def test_empty_agent_id_raises():
    spec = _base_spec(agent_id="")
    with pytest.raises(ValueError, match="agent_id"):
        handle_create_job(spec, now=100.0)


def test_missing_cost_raises():
    spec = _base_spec()
    del spec["cost"]
    with pytest.raises(ValueError, match="cost"):
        handle_create_job(spec, now=100.0)


def test_missing_grant_raises():
    spec = _base_spec()
    del spec["grant"]
    with pytest.raises(ValueError, match="grant"):
        handle_create_job(spec, now=100.0)


# ---------------------------------------------------------------------------
# Gate: nothing written on failure (ops.get returns None)
# ---------------------------------------------------------------------------

def test_missing_cost_does_not_write():
    spec = _base_spec()
    del spec["cost"]
    try:
        handle_create_job(spec, now=100.0)
    except ValueError:
        pass
    assert ops.get(spec["id"]) is None


def test_missing_grant_does_not_write():
    spec = _base_spec()
    del spec["grant"]
    try:
        handle_create_job(spec, now=100.0)
    except ValueError:
        pass
    assert ops.get(spec["id"]) is None


def test_missing_agent_id_does_not_write():
    spec = _base_spec()
    spec_id = spec["id"]
    del spec["agent_id"]
    try:
        handle_create_job(spec, now=100.0)
    except ValueError:
        pass
    assert ops.get(spec_id) is None


# ---------------------------------------------------------------------------
# Happy path: fully gated spec creates the job
# ---------------------------------------------------------------------------

def test_happy_path_returns_dict_with_id():
    result = handle_create_job(_base_spec(), now=100.0)
    assert isinstance(result, dict)
    assert result["id"] == "test-job-1"


def test_happy_path_job_persisted():
    handle_create_job(_base_spec(), now=100.0)
    j = ops.get("test-job-1")
    assert j is not None
    assert j.agent_id == "fred"


def test_happy_path_next_run_set():
    """ops.add must schedule next_run_at correctly (Every 60s from now=100)."""
    handle_create_job(_base_spec(), now=100.0)
    j = ops.get("test-job-1")
    assert j.state.next_run_at == 160.0


def test_happy_path_session_target_isolated():
    """Spec without session_target should default to 'isolated'."""
    spec = _base_spec()
    spec.pop("session_target", None)
    handle_create_job(spec, now=100.0)
    j = ops.get("test-job-1")
    assert j.session_target == "isolated"


def test_happy_path_cron_schedule():
    spec = _base_spec(
        id="cron-job",
        schedule={"kind": "cron", "expr": "0 * * * *"},
    )
    result = handle_create_job(spec, now=100.0)
    assert result["id"] == "cron-job"
    j = ops.get("cron-job")
    assert j is not None


# ---------------------------------------------------------------------------
# Cadence-floor gate: an Every schedule below min_cadence_s is rejected (nothing written)
# ---------------------------------------------------------------------------

def test_create_job_rejects_subfloor_cadence():
    spec = _base_spec(
        id="too-fast",
        schedule={"kind": "every", "seconds": 5},
        cost={"timeout_s": 30, "min_cadence_s": 3600, "max_consecutive_failures": 3},
    )
    with pytest.raises(ValueError, match="cadence below min_cadence_s floor"):
        handle_create_job(spec, now=100.0)
    assert ops.get("too-fast") is None   # nothing written on a failed gate


# ---------------------------------------------------------------------------
# Dynamic (self-paced loop) gate: floor > 0 required, agent_turn payload only,
# cost fields must be set (a None min_cadence_s is a next_run_at poison pill).
# ---------------------------------------------------------------------------

def _dyn_spec(**kw):
    # Dynamic defaults, overridable by kw (kw wins on key collision).
    base = dict(
        id="dyn",
        schedule={"kind": "dynamic"},
        payload={"kind": "agent_turn", "message": "loop"},
        cost={"timeout_s": 30, "min_cadence_s": 60, "max_consecutive_failures": 3},
    )
    base.update(kw)
    return _base_spec(**base)


def test_dynamic_valid_is_created_and_armed():
    handle_create_job(_dyn_spec(), now=1000.0)
    job = ops.get("dyn")
    assert job is not None
    assert job.state.next_run_at == 1000.0        # fresh Dynamic arms at now


def test_dynamic_zero_min_cadence_rejected():
    spec = _dyn_spec(cost={"timeout_s": 30, "min_cadence_s": 0,
                           "max_consecutive_failures": 3})
    with pytest.raises(ValueError, match="positive min_cadence_s"):
        handle_create_job(spec, now=1000.0)
    assert ops.get("dyn") is None


def test_dynamic_reminder_payload_rejected():
    spec = _dyn_spec(payload={"kind": "reminder", "text": "ping"})
    with pytest.raises(ValueError, match="agent_turn payload"):
        handle_create_job(spec, now=1000.0)
    assert ops.get("dyn") is None


def test_none_min_cadence_rejected_fail_closed():
    # The poison-pill source: a normalized cost missing min_cadence_s → None.
    spec = _dyn_spec(cost={"timeout_s": 30, "min_cadence_s": None,
                           "max_consecutive_failures": 3})
    with pytest.raises(ValueError, match="cost gate fields must be set"):
        handle_create_job(spec, now=1000.0)
    assert ops.get("dyn") is None


# ---------------------------------------------------------------------------
# ext_method registration — "harness/create_job" routes to handle_create_job
# ---------------------------------------------------------------------------

def test_ext_method_registered():
    """Verify "harness/create_job" is wired in HarnessAgent.ext_method.

    We test by grepping the source rather than constructing a full HarnessAgent
    (which requires ACP infrastructure) — the ACP wire round-trip is deferred.
    The test asserts the method name appears in the dispatch.
    """
    import inspect
    from harness import acp_agent
    src = inspect.getsource(acp_agent.HarnessAgent.ext_method)
    assert '"harness/create_job"' in src, (
        '"harness/create_job" not found in HarnessAgent.ext_method — '
        "registration is missing"
    )
