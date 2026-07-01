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


from harness.transcript import router_preamble


def test_router_preamble_includes_user_and_chat_assistant_excludes_agent():
    history = [
        {"role": "user", "content": "Flutter or React Native?", "origin": "chat"},
        {"role": "assistant", "content": "Which target — Flutter or RN?", "origin": "chat"},
        {"role": "user", "content": "fix the test", "origin": "agent"},
        {"role": "assistant", "content": "I ran pytest, 2 failed: ...", "origin": "agent"},
    ]
    pre = router_preamble(history)
    assert "Flutter or React Native?" in pre        # user turn (chat)
    assert "Which target" in pre                     # chat assistant answer included
    assert "fix the test" in pre                     # user turn (agent) included
    assert "I ran pytest" not in pre                  # agent assistant narration EXCLUDED


def test_router_preamble_empty_history_is_empty():
    assert router_preamble([]) == ""


def test_router_preamble_caps_to_recent_turns():
    # #256: an unbounded preamble re-sends the whole growing transcript to the
    # cheap classifier every turn. Cap to the most recent turns; triage only needs
    # recent context. Keep the LATEST turns (tail), drop the oldest.
    from harness.transcript import ROUTER_PREAMBLE_MAX_TURNS
    history = [{"role": "user", "content": f"turn {i}", "origin": "chat"}
               for i in range(ROUTER_PREAMBLE_MAX_TURNS + 20)]
    pre = router_preamble(history)
    lines = pre.splitlines()
    assert len(lines) == ROUTER_PREAMBLE_MAX_TURNS          # bounded, not unbounded
    assert f"turn {ROUTER_PREAMBLE_MAX_TURNS + 19}" in pre  # newest kept
    assert "turn 0" not in pre                              # oldest dropped
