import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

from harness.transcript import flatten_agent_messages


def _agent_messages():
    # mirrors the verified real shape: system, user, (assistant, tool)*, exit
    return [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "TASK"},
        {"role": "assistant", "content": "Let me reproduce the failure first."},
        {"role": "tool", "content": "<returncode>1</returncode>"},
        {"role": "assistant", "content": None},                       # tool-only turn
        {"role": "tool", "content": "<returncode>0</returncode>"},
        {"role": "assistant", "content": "Fixed it."},
        {"role": "exit", "content": "", "extra": {"exit_status": "Submitted",
                                                  "submission": "Bug fixed in calculator.py"}},
    ]


def test_flatten_joins_assistant_prose_skips_none_and_appends_submission():
    out = flatten_agent_messages(_agent_messages())
    assert "Let me reproduce the failure first." in out
    assert "Fixed it." in out
    assert "Bug fixed in calculator.py" in out          # submission appended
    assert "None" not in out                             # None content skipped, not stringified
    assert "<returncode>" not in out                     # tool/exit structure never leaks
    assert out.index("reproduce") < out.index("Fixed")   # chronological order


def test_flatten_empty_submission_uses_only_prose():
    msgs = _agent_messages()
    msgs[-1]["extra"]["submission"] = ""
    out = flatten_agent_messages(msgs)
    assert out.strip().endswith("Fixed it.")             # no trailing empty submission


def test_flatten_no_messages_returns_empty():
    assert flatten_agent_messages([]) == ""


def test_flatten_only_tool_turns_returns_empty():
    msgs = [{"role": "assistant", "content": None},
            {"role": "exit", "content": "", "extra": {"exit_status": "Submitted", "submission": ""}}]
    assert flatten_agent_messages(msgs) == ""
