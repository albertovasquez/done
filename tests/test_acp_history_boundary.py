"""cache.boundary integration: the alarm is observable end-to-end (#105/#139).

Drives HarnessAgent.prompt() directly (no subprocess) using the same fake-conn
driver as test_acp_session_context.py. Mock mode: the history summarizer
degrades to method="truncated" deterministically (no LLM available)."""

import asyncio

import acp

from harness.acp_agent import build_harness_agent
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

    _prompt(agent, sid, "hello")
    history_boundaries = _boundaries(agent, "history")
    assert len(history_boundaries) == 1
    data = history_boundaries[0]["data"]
    assert data["changed"] == "history"
    assert data["method"] == "truncated"
    assert agent._store.get(sid).compact_view is not None

    _prompt(agent, sid, "again")
    # episodic, not per-turn: no additional history boundary on the second turn
    assert len(_boundaries(agent, "history")) == 1


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
