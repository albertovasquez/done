"""Task 4: agent-path streaming — on_delta is wired through acp_agent.py so each
prose delta reaches the TUI as a message_chunk, a per-step boundary signal is
sent, the deltas accumulate into a buffer, and on failure that buffer (never a
prior turn's prose) is the recorded assistant transcript.

These drive HarnessAgent.prompt() directly (no subprocess), modelled on
tests/test_acp_session_context.py: a scripted router routes to the agent path,
a recording connection captures every session_update, and a streaming-capable
fake model fires its bound on_delta for each delta the way StreamingLitellmModel
does — without a network call. The agent loop terminates via a real submit echo
(through LocalEnvironment), so these are full prompt()-level integration tests.
"""

import asyncio


import acp

from minisweagent.models.test_models import DeterministicToolcallModel, make_toolcall_output

from harness.acp_agent import build_harness_agent
from harness.router import Classification

_SUBMIT = "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"


# --------------------------------------------------------------------------
# Scaffolding
# --------------------------------------------------------------------------

class RecordingConn:
    """Captures every session_update; exposes the prose deltas (in order) and the
    per-step boundary (stream_reset) flags. message_chunk(text) builds an update
    whose .content.text holds the prose; with_meta nests the harness meta under
    field_meta['harness']."""

    def __init__(self):
        self.updates = []

    async def session_update(self, session_id, update, **kw):
        self.updates.append(update)

    def message_texts(self):
        out = []
        for u in self.updates:
            txt = getattr(getattr(u, "content", None), "text", "") or ""
            if txt:
                out.append(txt)
        return out

    def reset_flags(self):
        flags = []
        for u in self.updates:
            meta = getattr(u, "field_meta", None) or {}
            harness = meta.get("harness", {}) if isinstance(meta, dict) else {}
            if isinstance(harness, dict) and harness.get("stream_reset"):
                flags.append(True)
        return flags

    def usage_payloads(self):
        out = []
        for u in self.updates:
            meta = getattr(u, "field_meta", None) or {}
            harness = meta.get("harness", {}) if isinstance(meta, dict) else {}
            if isinstance(harness, dict) and isinstance(harness.get("usage"), dict):
                out.append(harness["usage"])
        return out


class _ScriptedRouter:
    """Routes every prompt to the agent path with no skills (so skills.compose is
    a no-op) and a deterministic classification."""

    catalog = []

    def classify(self, text, history=None):
        # code_fix routes to the agent path (not chat/ambiguous); empty skills.
        return Classification(task_type="code_fix", skills=[], confidence=1.0)


class _StreamingSubmitModel(DeterministicToolcallModel):
    """A DeterministicToolcallModel that fires on_delta for each prose delta the
    way StreamingLitellmModel does, then returns a submit tool call so the agent
    loop exits cleanly in one step. Has an on_delta attribute (unlike the mock
    model), so acp_agent binds emit_delta to it."""

    def __init__(self, deltas):
        out = make_toolcall_output(
            "".join(deltas),
            [{"id": "call_0", "type": "function",
              "function": {"name": "bash",
                           "arguments": '{"command": "' + _SUBMIT + '"}'}}],
            [{"command": _SUBMIT, "tool_call_id": "call_0"}],
        )
        out["extra"]["cost"] = 0.0
        super().__init__(outputs=[out], cost_per_call=0.0)
        self._deltas = list(deltas)
        self.on_delta = None

    def query(self, messages, **kw):
        for d in self._deltas:
            if self.on_delta:
                self.on_delta(d)
        return super().query(messages, **kw)


class _StreamThenFailModel(DeterministicToolcallModel):
    """Fires on_delta for each delta, then raises inside query() — emulating a
    model that streams some prose and then the engine fails. The streamed buffer
    must be what acp_agent records as this turn's assistant on the refusal."""

    def __init__(self, deltas):
        # outputs unused (we raise before returning), but the config needs one.
        super().__init__(outputs=[make_toolcall_output("", [], [])], cost_per_call=0.0)
        self._deltas = list(deltas)
        self.on_delta = None

    def query(self, messages, **kw):
        for d in self._deltas:
            if self.on_delta:
                self.on_delta(d)
        raise RuntimeError("model blew up mid-stream")


def _agent_cfg():
    import yaml
    from pathlib import Path
    return yaml.safe_load(Path("upstream/src/minisweagent/config/mini.yaml").read_text())["agent"]


def _build(model, conn):
    from pathlib import Path
    agent = build_harness_agent(
        model_factory=lambda *a, **k: model,   # the SAME instance the test inspects
        agent_cfg=_agent_cfg(),
        skills_dir=Path("skills"),
        router=_ScriptedRouter(),
        worker_model_id="gpt-5.4",
    )
    agent._conn = conn
    agent._client_caps = None    # standalone: auto-allow, LocalEnvironment fallback
    agent._yolo = True           # no permission round-trip for the submit echo
    return agent


def _prompt(agent, sid, text):
    return asyncio.run(agent.prompt([acp.text_block(text)], sid))


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------

class _ChatRouter:
    """Routes every prompt to the CHAT path (chat_question), so the turn goes
    through ChatHandler.answer_stream + the pump, not the engine. This is the
    path that hung in the reported session (--debug trace 20260628-124707)."""

    catalog = []

    def classify(self, text, history=None):
        return Classification(task_type="chat_question", skills=[], confidence=0.95)


def _prompt_with_timeout(agent, sid, text, timeout=10.0):
    async def go():
        return await asyncio.wait_for(
            agent.prompt([acp.text_block(text)], sid), timeout=timeout)
    return asyncio.run(go())


def test_chat_path_prompt_returns(tmp_path):
    """A chat_question turn must RETURN a PromptResponse — if prompt() never
    resolves, the TUI's await blocks forever and 'Responding…' sticks with a
    locked composer (the reported bug). model_id=None → ChatHandler yields one
    honest message without a network call; the turn must still complete."""
    conn = RecordingConn()
    agent = build_harness_agent(
        model_factory=lambda *a, **k: None,
        agent_cfg=_agent_cfg(),
        skills_dir=__import__("pathlib").Path("skills"),
        router=_ChatRouter(),
        worker_model_id=None,                       # mock → ChatHandler honest-message path
    )
    agent._conn = conn
    agent._client_caps = None
    sid = agent._store.new(cwd=str(tmp_path))

    resp = _prompt_with_timeout(agent, sid, "what can you do?")
    assert resp.stop_reason == "end_turn", f"chat prompt() did not end cleanly: {resp}"


def test_agent_path_streams_deltas_as_message_chunks(tmp_path):
    """The agent-path turn must deliver all prose to the connection as message
    chunks, in order, preceded by exactly one step-boundary signal. After
    coalescing, deltas may be merged into fewer/larger chunks — so we assert the
    CONCATENATION and ORDER, not a 1:1 delta→chunk mapping."""
    deltas = ["Look", "ing ", "into ", "it."]
    conn = RecordingConn()
    model = _StreamingSubmitModel(deltas)
    agent = _build(model, conn)
    sid = agent._store.new(cwd=str(tmp_path))

    resp = _prompt(agent, sid, "fix the bug")
    assert resp.stop_reason == "end_turn", f"unexpected stop_reason: {resp.stop_reason}"

    # The submit echo's tool output also appears as a non-empty text; isolate the
    # prose stream by reassembling and checking the joined deltas are a contiguous,
    # in-order substring of the concatenated message texts.
    full = "".join(conn.message_texts())
    assert "".join(deltas) in full, (
        f"prose not delivered intact/in order: texts = {conn.message_texts()!r}"
    )
    # exactly one step boundary (one model call → one new n_calls value)
    assert conn.reset_flags() == [True], (
        f"expected exactly one stream_reset boundary, got {conn.reset_flags()!r}"
    )


def test_agent_path_relays_llm_return_usage_without_debug(tmp_path):
    """Usage from llm.return must reach the TUI via field_meta even when --debug is off."""
    out = make_toolcall_output(
        "done",
        [{"id": "call_0", "type": "function",
          "function": {"name": "bash",
                       "arguments": '{"command": "' + _SUBMIT + '"}'}}],
        [{"command": _SUBMIT, "tool_call_id": "call_0"}],
    )
    out["extra"]["cost"] = 0.0
    out["extra"]["response"] = {
        "usage": {
            "prompt_tokens": 1200,
            "completion_tokens": 34,
            "total_tokens": 1234,
        }
    }
    conn = RecordingConn()
    model = DeterministicToolcallModel(outputs=[out], cost_per_call=0.0)
    agent = _build(model, conn)
    assert not agent._debug
    sid = agent._store.new(cwd=str(tmp_path))

    _prompt(agent, sid, "fix the bug")

    assert conn.usage_payloads() == [{
        "total": 1234,
        "prompt": 1200,
        "completion": 34,
    }]


def test_on_delta_cleared_after_turn(tmp_path):
    """After the turn completes, the model's on_delta must be None — the agent
    must never marshal a delta to a dead event loop on a later turn."""
    model = _StreamingSubmitModel(["A", "B"])
    conn = RecordingConn()
    agent = _build(model, conn)
    sid = agent._store.new(cwd=str(tmp_path))

    _prompt(agent, sid, "fix the bug")

    assert model.on_delta is None, "on_delta was not cleared after the turn"


def test_failure_records_streamed_buffer_not_prior_turn(tmp_path, caplog):
    """A turn that streams 'AB' then FAILS must record 'AB' (this turn's streamed
    buffer) as the assistant transcript — never a prior turn's assistant prose."""
    model = _StreamThenFailModel(["A", "B"])
    conn = RecordingConn()
    agent = _build(model, conn)
    sid = agent._store.new(cwd=str(tmp_path))

    # Seed a non-empty prior transcript whose assistant prose must NOT be folded
    # into the failing turn (flatten_agent_messages over agent.messages would
    # include the injected prior; the failure path must use 'streamed' instead).
    agent._store.extend(sid, [
        {"role": "user", "content": "earlier question", "origin": "agent"},
        {"role": "assistant", "content": "OLD ANSWER", "origin": "agent"}])

    with caplog.at_level("ERROR", logger="harness.acp_agent"):
        resp = _prompt(agent, sid, "do the thing")
    assert resp.stop_reason == "refusal", f"expected refusal, got {resp.stop_reason}"
    # the engine failure must be logged with a traceback, not swallowed into a
    # bare refusal with no diagnostic
    assert any("agent engine failed" in r.message for r in caplog.records), \
        f"engine failure must be logged; got {[r.message for r in caplog.records]}"

    transcript = agent._store.get(sid).transcript
    last_assistant = [m for m in transcript if m["role"] == "assistant"][-1]
    assert last_assistant["content"] == "AB", (
        f"failure must record the streamed buffer 'AB', got {last_assistant['content']!r}"
    )
    assert "OLD ANSWER" not in last_assistant["content"], (
        "prior-turn assistant prose was folded into the failing turn's transcript"
    )
    # the prior turn's assistant is still present, unchanged, as an earlier entry
    assert any(m["content"] == "OLD ANSWER" for m in transcript), (
        "prior transcript must be preserved, not overwritten"
    )
