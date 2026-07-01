from harness.goal_gate import GoalContext, Verdict


def _agent_with_goal(monkeypatch, verdict_seq):
    """Build a bare TracingAgent around _apply_goal_gate, injecting a fake reviewer.
    Tests the gate helper in isolation from the LLM loop."""
    from harness import tracing_agent as ta
    calls = {"n": 0}

    def fake_review(goal, transcript, model, **kw):
        v = verdict_seq[min(calls["n"], len(verdict_seq) - 1)]
        calls["n"] += 1
        return v
    monkeypatch.setattr(ta, "review_goal", fake_review)
    import logging
    agent = ta.TracingAgent.__new__(ta.TracingAgent)   # bypass __init__
    agent.logger = logging.getLogger("test-goal-agent")   # add_messages logs
    agent.goal_ctx = GoalContext(text="G", reviewer_model="m", max_attempts=2)
    agent.messages = [{"role": "exit",
                       "extra": {"exit_status": "Submitted", "submission": "done?"}}]
    # a minimal format_message + transcript
    agent.model = type("M", (), {"format_message": staticmethod(
        lambda **kw: {"role": kw["role"], "content": kw["content"]})})()
    return agent


def test_unmet_replaces_exit_with_continue(monkeypatch):
    agent = _agent_with_goal(monkeypatch, [Verdict(met=False, reason="not yet")])
    still_exit = agent._apply_goal_gate()
    assert still_exit is False
    assert agent.messages[-1]["role"] == "user"
    assert "not yet" in agent.messages[-1]["content"]


def test_met_keeps_exit_and_clears_goal(monkeypatch):
    agent = _agent_with_goal(monkeypatch, [Verdict(met=True)])
    still_exit = agent._apply_goal_gate()
    assert still_exit is True
    assert agent.messages[-1]["role"] == "exit"
    assert agent.goal_ctx is None


def test_budget_exhaustion_escapes(monkeypatch):
    agent = _agent_with_goal(monkeypatch, [Verdict(met=False)])
    agent.goal_ctx = GoalContext(text="G", reviewer_model="m", max_attempts=1)
    agent.goal_ctx.attempts = 1
    still_exit = agent._apply_goal_gate()
    assert still_exit is True
    assert agent.messages[-1]["role"] == "exit"
    assert agent.goal_ctx is None


def test_non_submitted_exit_is_ignored(monkeypatch):
    agent = _agent_with_goal(monkeypatch, [Verdict(met=False)])
    agent.messages = [{"role": "exit",
                       "extra": {"exit_status": "cancelled", "submission": ""}}]
    still_exit = agent._apply_goal_gate()
    assert still_exit is True
    assert agent.messages[-1]["role"] == "exit"


def test_no_goal_is_noop(monkeypatch):
    agent = _agent_with_goal(monkeypatch, [Verdict(met=False)])
    agent.goal_ctx = None
    still_exit = agent._apply_goal_gate()
    assert still_exit is True


def test_continue_closes_dangling_tool_call(monkeypatch):
    # A bash submit leaves an assistant tool_calls message unpaired (Submitted
    # re-raises before the observation is appended). On continue, the gate must
    # close that tool call so the next request isn't malformed.
    agent = _agent_with_goal(monkeypatch, [Verdict(met=False, reason="keep going")])
    agent.messages = [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": "bash", "arguments": "{}"}}]},
        {"role": "exit", "extra": {"exit_status": "Submitted", "submission": "done?"}},
    ]
    still_exit = agent._apply_goal_gate()
    assert still_exit is False
    roles = [m.get("role") for m in agent.messages]
    # every assistant tool_call id must have a matching tool message
    tool_ids = {tc["id"] for m in agent.messages if m.get("role") == "assistant"
                for tc in m.get("tool_calls", [])}
    answered = {m.get("tool_call_id") for m in agent.messages if m.get("role") == "tool"}
    assert tool_ids <= answered, f"unpaired tool call: {tool_ids - answered}"
    assert roles[-1] == "user"        # continue message is last


def test_reviewer_exception_escapes(monkeypatch):
    from harness import tracing_agent as ta
    def boom(*a, **k): raise RuntimeError("reviewer down")
    monkeypatch.setattr(ta, "review_goal", boom)
    agent = ta.TracingAgent.__new__(ta.TracingAgent)
    agent.goal_ctx = GoalContext(text="G", reviewer_model="m", max_attempts=3)
    agent.messages = [{"role": "exit", "extra": {"exit_status": "Submitted"}}]
    agent.model = type("M", (), {"format_message": staticmethod(lambda **kw: kw)})()
    still_exit = agent._apply_goal_gate()
    assert still_exit is True                 # reviewer failure → escape (never loop)
    assert agent.goal_ctx is None
