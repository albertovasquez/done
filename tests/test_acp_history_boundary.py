"""cache.boundary integration: the alarm is observable end-to-end (#105/#139).

Drives HarnessAgent.prompt() directly (no subprocess) using the same fake-conn
driver as test_acp_session_context.py. Mock mode: the history summarizer
degrades to method="truncated" deterministically (no LLM available)."""

import asyncio
from unittest.mock import patch

import acp

from harness.acp_agent import build_harness_agent
from harness.chat_handler import ChatHandler
from harness.router import Classification


# --------------------------------------------------------------------------
# Driver (reused pattern from tests/test_acp_session_context.py)
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
        self.history_seen = []
        self.catalog = []

    def classify(self, prompt, history=None):
        self.history_seen.append(list(history) if history else [])
        return self._queue.pop(0)


def _chat(cls_skills=None):
    return Classification(task_type="chat_question", skills=cls_skills or [], confidence=1.0)


def _agent_cfg():
    import yaml
    from pathlib import Path
    return yaml.safe_load(Path("upstream/src/minisweagent/config/mini.yaml").read_text())["agent"]


def _build(router, worker_model_id=None):
    """Mock-mode harness with --debug ON — cache.boundary traces ride the
    with_meta channel, which _trace() no-ops unless debug=True."""
    from harness import acp_main
    from pathlib import Path
    agent = build_harness_agent(
        model_factory=acp_main._model_factory("mock"),
        agent_cfg=acp_main._agent_config() if hasattr(acp_main, "_agent_config") else _agent_cfg(),
        skills_dir=Path("skills"),
        router=router,
        worker_model_id=worker_model_id,
        backend="mock",
        debug=True,        # required: _trace() is a no-op when debug is off
    )
    agent._conn = _FakeConn()
    agent._client_caps = None
    return agent


def _prompt(agent, sid, text):
    return asyncio.run(agent.prompt([acp.text_block(text)], sid))


def _traces(agent):
    """All trace payloads captured by the fake conn, in emission order. Mirrors
    how the TUI reads field_meta['harness']['trace'] (see acp_emit.trace_event)."""
    out = []
    for u in agent._conn.updates:
        fm = getattr(u, "field_meta", None)
        h = fm.get("harness") if isinstance(fm, dict) else None
        t = h.get("trace") if isinstance(h, dict) else None
        if isinstance(t, dict) and "type" in t:
            out.append(t)
    return out


def _boundaries(agent, changed_contains=None):
    """cache.boundary trace events, optionally filtered to those whose `changed`
    field contains a given substring (block name)."""
    evs = [t for t in _traces(agent) if t["type"] == "cache.boundary"]
    if changed_contains is None:
        return evs
    return [e for e in evs if changed_contains in e["data"].get("changed", "")]


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------

def test_history_episode_emits_boundary_once():
    router = _ScriptedRouter([_chat(), _chat()])
    agent = _build(router)
    sid = agent._store.new(cwd=".")

    # Seed the store past budget: 40 * 500 tokens (~20k) > mock budget (16k).
    agent._store.extend(sid, [{"role": "user", "content": "y" * 2000, "origin": "chat"}] * 40)

    # Capture the `history` kwarg every consumer receives, without altering
    # behavior — wrap the real bound method so mock mode still yields its
    # normal string; we only record what was passed in.
    seen_history = []
    real_answer_stream = ChatHandler.answer_stream

    def _capturing_answer_stream(self, prompt, history=None, cancel_flag=None):
        seen_history.append(list(history) if history else [])
        yield from real_answer_stream(self, prompt, history=history, cancel_flag=cancel_flag)

    with patch.object(ChatHandler, "answer_stream", _capturing_answer_stream):
        _prompt(agent, sid, "hello")
        history_boundaries = _boundaries(agent, "history")
        assert len(history_boundaries) == 1
        data = history_boundaries[0]["data"]
        assert data["changed"] == "history"
        assert data["method"] == "truncated"
        assert agent._store.get(sid).compact_view is not None

        raw_len_before_second_prompt = len(agent._store.get(sid).transcript)

        _prompt(agent, sid, "again")
        # episodic, not per-turn: no additional history boundary on the second turn
        assert len(_boundaries(agent, "history")) == 1

    # Consumer switch (chat path): on the second prompt the chat handler must
    # receive the COMPACTED view (compact_view.messages + raw tail), not the
    # full raw transcript. Compute the expected length from live state, not a
    # magic number — reverting `history=history` -> `history=transcript` on
    # the chat call makes this assertion fail (see RED/GREEN check in the
    # task report).
    compact_view = agent._store.get(sid).compact_view
    assert compact_view is not None
    raw_tail_count = raw_len_before_second_prompt - compact_view.upto
    expected_len = len(compact_view.messages) + raw_tail_count
    assert len(seen_history) == 2
    second_prompt_history_len = len(seen_history[1])
    assert second_prompt_history_len == expected_len
    assert second_prompt_history_len < raw_len_before_second_prompt

    # Router-stays-raw invariant: the router must keep seeing the RAW
    # transcript (already tail-capped upstream), never the compacted view.
    assert len(router.history_seen) == 2
    assert len(router.history_seen[1]) == raw_len_before_second_prompt


def test_small_session_never_emits_history_boundary():
    router = _ScriptedRouter([_chat(), _chat()])
    agent = _build(router)
    sid = agent._store.new(cwd=".")

    _prompt(agent, sid, "hi")
    _prompt(agent, sid, "hi again")

    assert _boundaries(agent, "history") == []
    assert agent._store.get(sid).compact_view is None


def test_env_or_block_change_emits_named_boundary():
    router = _ScriptedRouter([_chat(), _chat()])
    agent = _build(router)
    sid = agent._store.new(cwd=".")

    _prompt(agent, sid, "hello")
    # first turn: no previous hash to compare against -> changed_blocks() == []
    assert _boundaries(agent, "memory") == []

    agent._store.get(sid).memory_block = "CHANGED MEMORY"
    _prompt(agent, sid, "again")

    memory_boundaries = _boundaries(agent, "memory")
    assert len(memory_boundaries) == 1
    assert "memory" in memory_boundaries[0]["data"]["changed"]
