import inspect
import dataclasses
from harness import acp_agent
from harness.acp_session import SessionState
from harness.goal_gate import GoalContext


def test_set_and_clear_goal_registered():
    src = inspect.getsource(acp_agent.HarnessAgent.ext_method)
    assert '"harness/set_goal"' in src
    assert '"harness/clear_goal"' in src


def test_goal_context_dataclass_shape():
    g = GoalContext(text="do X", reviewer_model="m")
    assert g.text == "do X" and g.reviewer_model == "m"
    assert g.max_attempts == 3 and g.attempts == 0


def test_session_state_has_goal_field():
    fields = {f.name for f in dataclasses.fields(SessionState)}
    assert "goal" in fields


def test_session_state_goal_defaults_none():
    st = SessionState(cwd="/tmp")
    assert st.goal is None


def test_cancel_clears_goal_source():
    # B5: the cancel handler must disarm the goal (state.goal = None).
    src = inspect.getsource(acp_agent.HarnessAgent.cancel)
    assert "goal = None" in src
