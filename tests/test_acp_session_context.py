import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

from harness.transcript import flatten_agent_messages


def test_flatten_used_for_agent_capture_smoke():
    # Guards the contract Task 5 relies on: a realistic agent message list
    # flattens to non-empty prose that excludes tool/exit structure.
    messages = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "TASK"},
        {"role": "assistant", "content": "Working on it."},
        {"role": "tool", "content": "<returncode>0</returncode>"},
        {"role": "exit", "content": "", "extra": {"exit_status": "Submitted", "submission": "done"}},
    ]
    out = flatten_agent_messages(messages)
    assert out == "Working on it.\n\ndone"
