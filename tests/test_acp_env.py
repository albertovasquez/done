import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

import threading
from trace.acp_env import AcpEnvironment


def _env(tmp_path, **kw):
    return AcpEnvironment(cwd=str(tmp_path), **kw)


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
