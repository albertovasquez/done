"""Session-context tests: the transcript carried across turns, in-process.

These drive HarnessAgent.prompt() directly (no subprocess) with an injected fake
router and a fake connection that records session_update calls. This lets us
assert on state.transcript and on what `prior`/`history` each path received —
the user-visible "follow-ups are understood" behavior.
"""

import asyncio
import sys

sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

import acp

from harness.acp_agent import build_harness_agent
from harness.router import Classification
from harness.transcript import flatten_agent_messages


# --------------------------------------------------------------------------
# Task 2/5 contract smoke: flatten excludes tool/exit structure
# --------------------------------------------------------------------------

def test_flatten_used_for_agent_capture_smoke():
    messages = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "TASK"},
        {"role": "assistant", "content": "Working on it."},
        {"role": "tool", "content": "<returncode>0</returncode>"},
        {"role": "exit", "content": "", "extra": {"exit_status": "Submitted", "submission": "done"}},
    ]
    assert flatten_agent_messages(messages) == "Working on it.\n\ndone"


# --------------------------------------------------------------------------
# In-process harness scaffolding
# --------------------------------------------------------------------------

class _FakeConn:
    """Records session_update calls; services request_permission as allow."""

    def __init__(self):
        self.updates = []

    async def session_update(self, session_id, update, **kw):
        self.updates.append(update)


class _ScriptedRouter:
    """Returns a queued Classification per classify() call, recording the
    `history` it was handed each time."""

    def __init__(self, classifications):
        self._queue = list(classifications)
        self.history_seen = []   # the history arg of each classify call, in order

    def classify(self, prompt, history=None):
        self.history_seen.append(list(history) if history else [])
        return self._queue.pop(0)


def _chat(cls_skills=None):
    return Classification(task_type="chat_question", skills=cls_skills or [], confidence=1.0)


def _agent_fix():
    # code_fix routes to the agent path; skills empty so no skill compose noise
    return Classification(task_type="code_fix", skills=[], confidence=1.0)


def _ambiguous():
    return Classification(task_type="ambiguous", confidence=0.2, needs_clarification=True,
                          clarifying_question="Could you clarify?")


def _build(router, worker_model_id=None):
    """Mock-mode harness (no real worker model). worker_model_id=None => chat
    path yields the honest mock line; agent path uses the deterministic mock model."""
    from harness import acp_main
    from pathlib import Path
    agent = build_harness_agent(
        model_factory=acp_main._model_factory("mock"),
        agent_cfg=acp_main._agent_config() if hasattr(acp_main, "_agent_config") else _agent_cfg(),
        skills_dir=Path("skills"),
        router=router,
        worker_model_id=worker_model_id,
    )
    agent._conn = _FakeConn()
    agent._client_caps = None    # standalone: auto-allow, no terminal delegation
    return agent


def _agent_cfg():
    import yaml
    from pathlib import Path
    return yaml.safe_load(Path("upstream/src/minisweagent/config/mini.yaml").read_text())["agent"]


def _prompt(agent, sid, text):
    return asyncio.run(agent.prompt([acp.text_block(text)], sid))


# --------------------------------------------------------------------------
# Task 7: per-branch write rules
# --------------------------------------------------------------------------

def test_chat_turn_writes_user_and_assistant_with_chat_origin():
    router = _ScriptedRouter([_chat()])
    agent = _build(router)
    sid = agent._store.new(cwd=".")
    _prompt(agent, sid, "what is X")
    t = agent._store.get(sid).transcript
    assert [(m["role"], m["origin"]) for m in t] == [("user", "chat"), ("assistant", "chat")]
    assert t[0]["content"] == "what is X"
    assert t[1]["content"]                    # the mock honest line, non-empty


def test_clarify_turn_writes_only_user_turn():
    router = _ScriptedRouter([_ambiguous()])
    agent = _build(router)
    sid = agent._store.new(cwd=".")
    _prompt(agent, sid, "huh")
    t = agent._store.get(sid).transcript
    assert [(m["role"], m["origin"]) for m in t] == [("user", "clarify")]
    assert t[0]["content"] == "huh"


def test_router_unavailable_writes_nothing():
    class _Boom:
        def classify(self, prompt, history=None):
            raise RuntimeError("vibeproxy down")
    agent = _build(_Boom())
    sid = agent._store.new(cwd=".")
    resp = _prompt(agent, sid, "x")
    assert resp.stop_reason == "refusal"
    assert agent._store.get(sid).transcript == []


def test_second_turn_passes_prior_transcript_to_classify():
    router = _ScriptedRouter([_chat(), _chat()])
    agent = _build(router)
    sid = agent._store.new(cwd=".")
    _prompt(agent, sid, "first")
    _prompt(agent, sid, "second")
    # classify on turn 2 must have received turn-1's user+assistant in history
    assert router.history_seen[0] == []                       # turn 1: empty
    assert any(m["content"] == "first" for m in router.history_seen[1])


# --------------------------------------------------------------------------
# Task 8: end-to-end cross-path context
# --------------------------------------------------------------------------

def test_chat_then_agent_sees_prior_chat_in_classify_history(tmp_path):
    # turn 1 chat, turn 2 agent. The agent-turn classify must see the turn-1 chat.
    import shutil
    repo = tmp_path / "sample-repo"
    shutil.copytree("examples/sample-repo", repo)   # never mutate the checked-in fixture
    router = _ScriptedRouter([_chat(), _agent_fix()])
    agent = _build(router)
    sid = agent._store.new(cwd=str(repo))
    _prompt(agent, sid, "what does add do?")
    _prompt(agent, sid, "now fix it")
    # router.history_seen[1] is the context the agent turn was classified with
    hist = router.history_seen[1]
    assert any("what does add do?" in m["content"] for m in hist)
    # and the agent turn itself was written to the transcript
    t = agent._store.get(sid).transcript
    assert [(m["role"], m["origin"]) for m in t][-2:] == [("user", "agent"), ("assistant", "agent")]


def test_agent_then_chat_transcript_carries_agent_narration():
    # turn 1 agent (mock fixes the bug), turn 2 chat. The transcript after turn 1
    # must hold the agent assistant narration so turn-2 chat has context.
    import shutil, tempfile, os
    repo = tempfile.mkdtemp()
    shutil.copytree("examples/sample-repo", repo, dirs_exist_ok=True)
    router = _ScriptedRouter([_agent_fix(), _chat()])
    agent = _build(router)
    sid = agent._store.new(cwd=repo)
    _prompt(agent, sid, "fix the add bug")
    t_after_agent = agent._store.get(sid).transcript
    agent_assistant = [m for m in t_after_agent if m["origin"] == "agent" and m["role"] == "assistant"]
    assert agent_assistant, "agent turn must write an assistant narration"
    assert agent_assistant[0]["content"].strip()        # non-empty prose

    _prompt(agent, sid, "what did you change?")
    # turn-2 chat classify saw the agent turn in history
    hist = router.history_seen[1]
    assert any(m["origin"] == "agent" for m in hist)
