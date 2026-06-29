import threading
import time

import pytest
from minisweagent.exceptions import Submitted

from harness.acp_env import AcpEnvironment


def _env(tmp_path, **kw):
    return AcpEnvironment(cwd=str(tmp_path), **kw)


def test_submitted_propagates_and_still_fires_done(tmp_path):
    # the submit command makes super().execute() raise Submitted; AcpEnvironment
    # must NOT swallow it (the agent loop ends on it) — but it MUST still fire
    # on_command("done") so the start/done pair balances. Skipping done here was
    # the bug: it left the TUI's "Running shell…" tool-call open forever (stuck
    # spinner + locked composer) after the turn had already finished.
    calls = []
    env = _env(tmp_path, on_command=lambda phase, cmd, out: calls.append(phase))
    with pytest.raises(Submitted):
        env.execute({"command": "printf 'COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\\nresult'"})
    assert calls == ["start", "done"]   # balanced even though Submitted propagated


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


def test_cancel_flag_kills_running_command(tmp_path):
    # The flag flips AFTER the command has started: a long-running subprocess must
    # be killed and execute() must return promptly with a cancelled result, NOT
    # block for the full sleep. (Before the fix, super().execute() blocks in
    # communicate() and ignores the flag once the process is running.)
    flag = threading.Event()
    env = _env(tmp_path, on_command=lambda *a: None, cancel_flag=flag)

    # flip the flag shortly after execute() starts the subprocess
    threading.Timer(0.3, flag.set).start()
    start = time.monotonic()
    result = env.execute({"command": "sleep 30"})
    elapsed = time.monotonic() - start

    assert elapsed < 5, f"execute() blocked {elapsed:.1f}s — the running command was not killed"
    assert result["returncode"] == -1
    assert "cancel" in result["exception_info"].lower()


def test_cancel_flag_unset_runs_to_completion(tmp_path):
    # Regression guard: with the flag never set, a normal command still runs
    # fully and returns its real output (the poll loop must not truncate it).
    flag = threading.Event()
    env = _env(tmp_path, on_command=lambda *a: None, cancel_flag=flag)
    result = env.execute({"command": "printf 'done'; exit 7"})
    assert result["output"] == "done"
    assert result["returncode"] == 7


def test_permission_reject_skips_execution(tmp_path):
    calls = []
    env = _env(tmp_path,
               on_command=lambda phase, cmd, out: calls.append(phase),
               check_permission=lambda req: False)    # deny
    result = env.execute({"command": "printf 'denied'"})
    assert result["returncode"] == -1
    assert "denied" not in result.get("output", "")     # the command did NOT run
    assert "start" in calls and "rejected" in calls and "done" not in calls


def test_permission_allow_runs(tmp_path):
    env = _env(tmp_path, on_command=lambda *a: None, check_permission=lambda req: True)
    assert "ok" in env.execute({"command": "printf 'ok'"})["output"]


def test_permission_request_carries_bash_kind(tmp_path):
    seen = []
    env = _env(tmp_path, on_command=lambda *a: None,
               check_permission=lambda req: (seen.append(req) or True))
    env.execute({"command": "printf 'ok'"})
    assert seen and seen[0].kind == "bash"
    assert seen[0].command == "printf 'ok'" and seen[0].is_exec is True


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


def test_client_terminal_submit_raises_and_fires_done(tmp_path):
    """client_terminal path (the one used when the client has a terminal) must
    still raise Submitted for the submit sentinel AND balance start/done — this
    is the exact path that left the spinner stuck in the reported session."""
    calls = []
    stub_out = {
        "output": "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\nmy result",
        "returncode": 0,
        "exception_info": "",
    }
    env = _env(tmp_path,
               on_command=lambda phase, cmd, out: calls.append(phase),
               client_terminal=lambda cmd: stub_out)
    with pytest.raises(Submitted):
        env.execute({"command": "anything"})
    assert calls == ["start", "done"]


# ---------------------------------------------------------------------------
# Plan sentinel: `plan ...` is intercepted, emitted, and NOT executed
# ---------------------------------------------------------------------------

def test_plan_command_intercepted_not_executed(tmp_path):
    plans, cmd_calls = [], []
    env = _env(tmp_path,
               on_command=lambda phase, cmd, out: cmd_calls.append(phase),
               check_permission=lambda req: (_ for _ in ()).throw(AssertionError("asked perm")),
               on_plan=lambda entries: plans.append(entries))
    result = env.execute({"command": 'plan "Push + PR:in_progress" "CI + merge:pending"'})
    # the plan callback got the parsed entries
    assert plans == [[("Push + PR", "in_progress"), ("CI + merge", "pending")]]
    # benign success returned to the agent loop
    assert result["returncode"] == 0
    # it was NOT run as a tool: no start/done/rejected, no permission ask
    assert cmd_calls == []


def test_non_plan_command_still_runs(tmp_path):
    plans = []
    env = _env(tmp_path, on_command=lambda *a: None, on_plan=lambda e: plans.append(e))
    result = env.execute({"command": "printf 'real'"})
    assert "real" in result["output"]
    assert plans == []                                  # on_plan never fired


def test_plan_without_on_plan_callback_is_safe(tmp_path):
    # if no on_plan is wired, a plan command must not crash and must not execute
    env = _env(tmp_path, on_command=lambda *a: None)
    result = env.execute({"command": 'plan "A:completed"'})
    assert result["returncode"] == 0
