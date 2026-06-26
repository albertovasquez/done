import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

import threading

import pytest
from minisweagent.exceptions import Submitted

from harness.acp_env import AcpEnvironment


def _env(tmp_path, **kw):
    return AcpEnvironment(cwd=str(tmp_path), **kw)


def test_submitted_propagates_and_skips_done(tmp_path):
    # the submit command makes super().execute() raise Submitted; AcpEnvironment
    # must NOT swallow it (the agent loop ends on it), and on_command("done")
    # must be skipped for that command.
    calls = []
    env = _env(tmp_path, on_command=lambda phase, cmd, out: calls.append(phase))
    with pytest.raises(Submitted):
        env.execute({"command": "printf 'COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\\nresult'"})
    assert "start" in calls and "done" not in calls


def test_executes_and_returns_full_output(tmp_path):
    calls = []
    env = _env(tmp_path, on_command=lambda phase, cmd, out: calls.append((phase, cmd, out)))
    result = env.execute({"command": "printf 'abc'"})
    assert result["returncode"] == 0
    assert "abc" in result["output"]                    # FULL output available at the seam
    phases = [c[0] for c in calls]
    assert phases == ["start", "done"]
    assert calls[1][2]["output"] == result["output"]    # done callback carries the full dict


def test_cancel_flag_skips_execution(tmp_path):
    flag = threading.Event(); flag.set()
    ran = []
    env = _env(tmp_path, on_command=lambda *a: ran.append(a), cancel_flag=flag)
    result = env.execute({"command": "printf 'should-not-run'"})
    assert result["returncode"] == -1
    assert "cancel" in result["exception_info"].lower()
    assert ran == []                                    # nothing fired; command never ran


def test_permission_reject_skips_execution(tmp_path):
    calls = []
    env = _env(tmp_path,
               on_command=lambda phase, cmd, out: calls.append(phase),
               request_permission=lambda cmd: False)    # deny
    result = env.execute({"command": "printf 'denied'"})
    assert result["returncode"] == -1
    assert "denied" not in result.get("output", "")     # the command did NOT run
    assert "start" in calls and "rejected" in calls and "done" not in calls


def test_permission_allow_runs(tmp_path):
    env = _env(tmp_path, on_command=lambda *a: None, request_permission=lambda cmd: True)
    assert "ok" in env.execute({"command": "printf 'ok'"})["output"]


# ---------------------------------------------------------------------------
# Layer 3: client_terminal delegation unit tests
# ---------------------------------------------------------------------------

def test_client_terminal_stub_is_used(tmp_path):
    """When client_terminal is set, execute() routes through it, not super().execute()."""
    done_calls = []
    sentinel = {"output": "SENTINEL_FROM_CLIENT", "returncode": 0, "exception_info": ""}

    env = _env(tmp_path,
               on_command=lambda phase, cmd, out: done_calls.append((phase, out)),
               client_terminal=lambda cmd: sentinel)
    result = env.execute({"command": "printf 'should-not-run'"})

    # Stub's sentinel output must be returned, not real shell output
    assert result["output"] == "SENTINEL_FROM_CLIENT"
    assert result["returncode"] == 0
    # on_command("done") must have fired with the stub's dict
    done = [out for phase, out in done_calls if phase == "done"]
    assert done and done[0]["output"] == "SENTINEL_FROM_CLIENT"
    # on_command("start") must have fired
    assert any(phase == "start" for phase, _ in done_calls)


def test_client_terminal_submit_raises(tmp_path):
    """client_terminal path must still raise Submitted for the submit sentinel."""
    stub_out = {
        "output": "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\nmy result",
        "returncode": 0,
        "exception_info": "",
    }
    env = _env(tmp_path, on_command=lambda *a: None, client_terminal=lambda cmd: stub_out)
    with pytest.raises(Submitted):
        env.execute({"command": "anything"})
