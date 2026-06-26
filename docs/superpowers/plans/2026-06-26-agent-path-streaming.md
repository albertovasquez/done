# Agent-Path Prose Streaming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stream the mini-swe-agent model's prose to the TUI on every loop step, so an agent-path turn (e.g. `code_explain`) shows tokens live instead of sitting silent until the whole turn finishes.

**Architecture:** A new `StreamingLitellmModel` (subclass of upstream `LitellmModel`) overrides only `_query` to call litellm with `stream=True`, fires an `on_delta(piece)` callback per prose token, then rebuilds the full response with `litellm.stream_chunk_builder(...)` so the *inherited* `query()` runs upstream's tool-call parsing / cost / FormatError logic unchanged. `acp_agent.py` binds `on_delta` to a closure that marshals each delta to the asyncio loop as a `message_chunk` (reusing the chat-path idiom), accumulates the deltas into a per-turn buffer (used as the failure-case transcript), emits a step-boundary signal at the start of each step, and clears `on_delta` afterward. The TUI's `_stream_message` is adjusted so a prose delta after a tool/boundary widget opens a fresh block instead of extending the prior one.

**Tech Stack:** Python 3.11 (runner venv), `litellm` 1.89.4, `acp` (Agent Client Protocol over JSON-RPC), Textual (TUI), pytest. Upstream mini-swe-agent vendored at `upstream/src`.

## Global Constraints

- **No upstream edit.** Do not modify anything under `upstream/src`. (Same discipline as `TracingAgent`.)
- **`on_delta is None` ⇒ blocking path.** When no callback is bound, behavior is byte-identical to upstream `LitellmModel` (mock mode, tests, CLI).
- **Streamed-on-screen == stored transcript.** The assistant text recorded for a turn must equal what the user saw stream — never prior-turn content folded in by `flatten_agent_messages`.
- **No retry after any delta was emitted.** A blocking retry would swap a discarded generation under text the user already saw.
- **ACP serializes prompts per session** — VERIFIED: `acp/task/dispatcher.py:58-61` awaits each request to completion before pulling the next (`async for task in self._queue: await self._dispatch_request(...)`). No per-session turn lock is needed; do NOT add one.
- **Test command (from worktree root):** `.venv/bin/python -m pytest tests/ -q` — but the worktree has no `.venv`; use the main checkout's interpreter: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/ -q`. Tests prepend `upstream/src` and `.` to `sys.path` themselves.
- **STDOUT is the JSON-RPC wire** in `acp_main.py` — never add prints to stdout there.

---

### Task 1: `StreamingLitellmModel` — streaming `_query` with delta callback

**Files:**
- Create: `harness/streaming_model.py`
- Test: `tests/test_streaming_model.py`

**Interfaces:**
- Consumes: upstream `minisweagent.models.litellm_model.LitellmModel` (inherits `query`, `_prepare_messages_for_api`, `_calculate_cost`, `_parse_actions`); `minisweagent.models.utils.actions_toolcall.BASH_TOOL`; `litellm.completion`, `litellm.stream_chunk_builder`.
- Produces: `class StreamingLitellmModel(LitellmModel)` with constructor kwarg/attribute `on_delta: Callable[[str], None] | None = None` (settable after construction) and overridden `_query(self, messages, **kwargs)`. `query()` is inherited unchanged. A module-level helper `_extract_delta(chunk) -> str` returns the prose piece from one stream chunk (`""` if none).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_streaming_model.py
import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

import types
import pytest

from harness.streaming_model import StreamingLitellmModel, _extract_delta


def _chunk(content):
    """A minimal litellm-style stream chunk carrying delta.content."""
    delta = types.SimpleNamespace(content=content)
    choice = types.SimpleNamespace(delta=delta)
    return types.SimpleNamespace(choices=[choice])


def _make_model(on_delta=None):
    # api_base/api_key live in model_kwargs per LitellmModelConfig (no top-level fields).
    return StreamingLitellmModel(
        on_delta=on_delta,
        model_name="openai/fake",
        model_kwargs={"api_base": "http://localhost:1/v1", "api_key": "x"},
        cost_tracking="ignore_errors",
    )


def test_extract_delta_returns_piece_or_empty():
    assert _extract_delta(_chunk("hi")) == "hi"
    assert _extract_delta(_chunk(None)) == ""
    assert _extract_delta(types.SimpleNamespace(choices=[])) == ""


def test_query_streams_each_prose_token_in_order(monkeypatch):
    seen = []
    pieces = ["Hel", "lo ", "world"]
    rebuilt_sentinel = object()

    def fake_completion(**kwargs):
        assert kwargs["stream"] is True
        return iter([_chunk(p) for p in pieces])

    def fake_builder(chunks, **kwargs):
        assert len(chunks) == len(pieces)
        return rebuilt_sentinel

    import litellm
    monkeypatch.setattr(litellm, "completion", fake_completion)
    monkeypatch.setattr(litellm, "stream_chunk_builder", fake_builder)

    model = _make_model(on_delta=seen.append)
    result = model._query([{"role": "user", "content": "x"}])
    assert seen == pieces                      # one callback per prose token, in order
    assert result is rebuilt_sentinel          # returns the rebuilt full response


def test_query_with_no_callback_takes_blocking_path(monkeypatch):
    called = {"stream": False, "blocking": False}

    def fake_completion(**kwargs):
        called["stream"] = kwargs.get("stream", False)
        # mimic a non-stream ModelResponse enough for the blocking path to return it
        return "BLOCKING_RESPONSE"

    import litellm
    monkeypatch.setattr(litellm, "completion", fake_completion)

    model = _make_model(on_delta=None)
    result = model._query([{"role": "user", "content": "x"}])
    assert called["stream"] is False           # blocking branch: stream not requested
    assert result == "BLOCKING_RESPONSE"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_streaming_model.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'harness.streaming_model'`.

- [ ] **Step 3: Write the implementation**

```python
# harness/streaming_model.py
"""StreamingLitellmModel: a LitellmModel that streams prose deltas to a callback
while still returning the complete-response shape upstream query() requires.

Overrides ONLY _query. query() is inherited unchanged, so all of upstream's
post-call logic (tool-call parsing, cost, FormatError persistence) runs on the
response rebuilt by litellm.stream_chunk_builder. on_delta is None => blocking
path (mock/tests/CLI), byte-identical to upstream.
"""

from __future__ import annotations

from collections.abc import Callable

import litellm

from minisweagent.models.litellm_model import LitellmModel
from minisweagent.models.utils.actions_toolcall import BASH_TOOL


def _extract_delta(chunk) -> str:
    """The prose piece from one stream chunk; '' when the chunk carries none."""
    choices = getattr(chunk, "choices", None) or []
    if not choices:
        return ""
    delta = getattr(choices[0], "delta", None)
    return getattr(delta, "content", None) or ""


class StreamingLitellmModel(LitellmModel):
    def __init__(self, *, on_delta: Callable[[str], None] | None = None, **kwargs):
        super().__init__(**kwargs)
        self.on_delta = on_delta   # set/cleared per run by the caller

    def _query(self, messages, **kwargs):
        if self.on_delta is None:
            return super()._query(messages, **kwargs)   # blocking path
        chunks = []
        try:
            stream = litellm.completion(
                model=self.config.model_name,
                messages=messages,
                tools=[BASH_TOOL],
                stream=True,
                **(self.config.model_kwargs | kwargs),
            )
            for chunk in stream:
                chunks.append(chunk)
                piece = _extract_delta(chunk)
                if piece:
                    self.on_delta(piece)
        except litellm.exceptions.AuthenticationError as e:
            e.message += " You can permanently set your API key with `mini-extra config set KEY VALUE`."
            raise
        rebuilt = litellm.stream_chunk_builder(chunks, messages=messages)
        if rebuilt is None and not chunks:
            # nothing was emitted and reassembly produced nothing → safe to fall
            # back to one blocking call (no discarded generation shown to the user).
            return super()._query(messages, **kwargs)
        return rebuilt
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_streaming_model.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add harness/streaming_model.py tests/test_streaming_model.py
git commit -m "feat(streaming): StreamingLitellmModel streams prose deltas via on_delta"
```

---

### Task 2: Reassembly fidelity + no-retry-after-delta

**Files:**
- Modify: `harness/streaming_model.py` (only if a fidelity gap surfaces; otherwise no change)
- Test: `tests/test_streaming_model.py` (add cases)

**Interfaces:**
- Consumes: `StreamingLitellmModel` from Task 1; inherited `query()`.
- Produces: no new symbols — locks the contracts "tool-calls survive reassembly" and "no retry once a delta was emitted".

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_streaming_model.py

def test_no_blocking_retry_after_a_delta_was_emitted(monkeypatch):
    """If reassembly returns None but deltas were already shown, do NOT retry —
    the user must not see one generation then have a different one committed."""
    seen = []
    blocking_calls = {"n": 0}

    def fake_completion(**kwargs):
        if kwargs.get("stream"):
            return iter([_chunk("partial")])   # one delta emitted
        blocking_calls["n"] += 1               # a retry would land here
        return "RETRY_RESPONSE"

    import litellm
    monkeypatch.setattr(litellm, "completion", fake_completion)
    monkeypatch.setattr(litellm, "stream_chunk_builder", lambda chunks, **kw: None)

    model = _make_model(on_delta=seen.append)
    result = model._query([{"role": "user", "content": "x"}])
    assert seen == ["partial"]                 # the user saw the streamed text
    assert blocking_calls["n"] == 0            # and we did NOT retry
    assert result is None                       # None propagates (treated as failure upstream)


def test_blocking_fallback_only_when_zero_deltas(monkeypatch):
    """Reassembly None AND no deltas emitted → one safe blocking fallback."""
    def fake_completion(**kwargs):
        if kwargs.get("stream"):
            return iter([])                    # empty stream, zero deltas
        return "FALLBACK"

    import litellm
    monkeypatch.setattr(litellm, "completion", fake_completion)
    monkeypatch.setattr(litellm, "stream_chunk_builder", lambda chunks, **kw: None)

    model = _make_model(on_delta=lambda p: None)
    assert model._query([{"role": "user", "content": "x"}]) == "FALLBACK"


def test_reassembled_response_preserves_tool_calls(monkeypatch):
    """A streamed bash tool-call survives stream_chunk_builder so the inherited
    query() parses the same actions a blocking response would."""
    seen = []

    # Real litellm.stream_chunk_builder over real chunk objects is the safest
    # fidelity check; build chunks the same way litellm emits a tool call.
    from litellm.types.utils import (ModelResponseStream, StreamingChoices,
                                     Delta, ChatCompletionDeltaToolCall, Function)

    def tc_chunk(idx, name=None, args="", finish=None):
        tcs = None
        if name is not None or args:
            tcs = [ChatCompletionDeltaToolCall(
                index=0, id=("call_1" if name else None), type="function",
                function=Function(name=name, arguments=args))]
        return ModelResponseStream(choices=[StreamingChoices(
            index=0, delta=Delta(content=None, tool_calls=tcs), finish_reason=finish)])

    chunks = [
        tc_chunk(0, name="bash", args=""),
        tc_chunk(0, args='{"command": "ls"}'),
        tc_chunk(0, finish="tool_calls"),
    ]

    def fake_completion(**kwargs):
        return iter(chunks)

    import litellm
    monkeypatch.setattr(litellm, "completion", fake_completion)

    model = _make_model(on_delta=seen.append)
    rebuilt = model._query([{"role": "user", "content": "x"}])
    # the rebuilt response must carry the tool call so upstream parsing works
    tool_calls = rebuilt.choices[0].message.tool_calls
    assert tool_calls and tool_calls[0].function.name == "bash"
    assert '"command": "ls"' in tool_calls[0].function.arguments
    assert seen == []                          # tool-call deltas are not prose
```

- [ ] **Step 2: Run the tests to verify they fail / surface gaps**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_streaming_model.py -q`
Expected: `test_no_blocking_retry_after_a_delta_was_emitted` and `test_blocking_fallback_only_when_zero_deltas` PASS already (Task 1 implements the guard). `test_reassembled_response_preserves_tool_calls` is the real verification — if the `litellm.types.utils` import path differs in 1.89.4, fix the import to the version's actual location (run `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -c "from litellm.types.utils import ModelResponseStream, StreamingChoices, Delta, ChatCompletionDeltaToolCall, Function; print('ok')"` first and adjust names to match). Do not change production code unless reassembly genuinely drops tool-calls.

- [ ] **Step 3: Implementation (only if a gap was found)**

If and only if `test_reassembled_response_preserves_tool_calls` fails because `stream_chunk_builder` drops tool calls, add explicit tool-call accumulation in `_query` before calling the builder. Otherwise NO production change — this task locks behavior via tests.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_streaming_model.py -q`
Expected: PASS (6 passed total).

- [ ] **Step 5: Commit**

```bash
git add tests/test_streaming_model.py harness/streaming_model.py
git commit -m "test(streaming): lock reassembly fidelity + no-retry-after-delta"
```

---

### Task 3: Factory returns `StreamingLitellmModel`

**Files:**
- Modify: `harness/acp_main.py:54-64` (the vibeproxy `make` closure)
- Test: `tests/test_acp_main_factory.py` (create)

**Interfaces:**
- Consumes: `StreamingLitellmModel` (Task 1).
- Produces: the agent-path (`vibeproxy`) factory returns a `StreamingLitellmModel` (with `on_delta=None` at construction). Mock branch unchanged.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_acp_main_factory.py
import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

from harness.acp_main import _model_factory
from harness.streaming_model import StreamingLitellmModel


def test_vibeproxy_factory_builds_streaming_model(monkeypatch):
    monkeypatch.setenv("VIBEPROXY_MODEL", "gpt-5.4")
    make = _model_factory("vibeproxy")
    model = make("claude-opus-4-8")
    assert isinstance(model, StreamingLitellmModel)
    assert model.on_delta is None                 # unbound until a turn sets it
    assert model.config.model_name == "openai/claude-opus-4-8"


def test_mock_factory_unchanged():
    make = _model_factory("mock")
    model = make()
    assert not isinstance(model, StreamingLitellmModel)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_acp_main_factory.py -q`
Expected: FAIL — `assert isinstance(model, StreamingLitellmModel)` fails (factory still builds plain `LitellmModel`).

- [ ] **Step 3: Edit the factory**

In `harness/acp_main.py`, change the vibeproxy `make` closure (currently constructs `LitellmModel`):

```python
    def make(current_model=None):
        from harness.streaming_model import StreamingLitellmModel
        model_id = current_model or os.getenv("VIBEPROXY_MODEL", "gpt-5.4")
        return StreamingLitellmModel(
            model_name="openai/" + model_id,
            model_kwargs={
                "api_base": os.getenv("VIBEPROXY_BASE_URL", "http://localhost:8317/v1"),
                "api_key": os.getenv("VIBEPROXY_API_KEY", "dummy-not-used"),
            },
            cost_tracking="ignore_errors",
        )
    return make
```

(Only the import and the class name change; `on_delta` defaults to `None`.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_acp_main_factory.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add harness/acp_main.py tests/test_acp_main_factory.py
git commit -m "feat(streaming): ACP vibeproxy factory builds StreamingLitellmModel"
```

---

### Task 4: Wire `on_delta` in `acp_agent.py` — stream, buffer, step-boundary, failure transcript

**Files:**
- Modify: `harness/acp_agent.py` — `_run_agent_turn` (≈176-285): add `emit_delta`, bind/clear `on_delta`, step-boundary signal, failure-buffer return.
- Modify: `harness/acp_agent.py` — `prompt()` agent-path block (≈160-174): on failure, record the streamed buffer as the assistant turn.
- Test: `tests/test_acp_agent_streaming.py` (create)

**Interfaces:**
- Consumes: `StreamingLitellmModel.on_delta` (Task 1/3); existing `message_chunk`, `with_meta` helpers in `acp_agent.py`; `asyncio.run_coroutine_threadsafe` (same idiom as the chat path at lines 145-149 and `on_command`).
- Produces: per loop step, each prose token is sent as a `message_chunk` `session_update`; the first delta of each step is preceded by a step-boundary `message_chunk` carrying `_meta={"stream_reset": True}`; `run_engine` returns an added key `streamed: str` (the accumulated deltas); on engine failure the recorded assistant text is `streamed` (never `flatten_agent_messages` over prior turns).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_acp_agent_streaming.py
import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

import asyncio
import pytest

from harness.acp_agent import HarnessAgent
from harness.acp_session import SessionStore


class RecordingConn:
    """Captures every session_update; exposes the text deltas and meta flags."""
    def __init__(self):
        self.updates = []

    async def session_update(self, session_id, update):
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
            if isinstance(meta, dict) and meta.get("stream_reset"):
                flags.append(True)
        return flags


def _agent_with_streaming_model(deltas, store, session_id):
    """A HarnessAgent whose model.query() invokes its bound on_delta for each
    delta, emulating StreamingLitellmModel without a network call. classify()
    routes to the agent path; skills.compose returns empty."""
    class FakeModel:
        on_delta = None
        config = type("C", (), {"model_name": "openai/fake"})()
        def query(self, messages, **kw):
            for d in deltas:
                if self.on_delta:
                    self.on_delta(d)
            return {"role": "assistant", "content": "".join(deltas),
                    "extra": {"actions": [], "cost": 0.0}}

    # Router stub: agent path, no skills.
    class R:
        catalog = []
        def classify(self, text, history=None):
            return type("Cls", (), {"task_type": "code_explain", "skills": [],
                                    "confidence": 0.99, "needs_clarification": False,
                                    "clarifying_question": None,
                                    "suggested_model": None})()

    agent = HarnessAgent(
        model_factory=lambda *a, **k: FakeModel(),
        agent_cfg={}, skills_dir=[], router=R(),
        worker_model_id="gpt-5.4", yolo=True, backend="vibeproxy")
    return agent


# NOTE: This test calls the agent's prompt handler directly. Match the actual
# entrypoint name/signature in acp_agent.py (e.g. `prompt`) when implementing;
# adapt the call below to the real method and PromptRequest shape.
def test_agent_path_streams_deltas_as_message_chunks(monkeypatch):
    # See Step 3 for how to invoke the prompt handler with the project's helpers.
    pytest.skip("wired in Step 3 against the real prompt() signature")
```

> Implementer note: the precise `prompt()` invocation in tests depends on the
> ACP `PromptRequest`/text-block constructors already used elsewhere — model the
> call on `tests/test_acp_smoke.py` / `tests/test_acp_session_context.py` (search
> for how they build a prompt and a `SessionStore`). Replace the skipped test
> with two real assertions: (1) `conn.message_texts()` contains the deltas in
> order; (2) after the turn, the model instance's `on_delta is None` (cleared).

- [ ] **Step 2: Run to verify it fails/【skips】**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_acp_agent_streaming.py -q`
Expected: 1 skipped (placeholder), confirming the file imports cleanly before wiring.

- [ ] **Step 3: Implement the wiring in `acp_agent.py`**

In `_run_agent_turn`, alongside the existing `on_command` / `request_permission`
closures (before `env = AcpEnvironment(...)`), add the buffer, an `agent_ref`
holder, and the two emit closures. The step boundary is keyed on the agent's
`n_calls`, which increments once per model call BEFORE `model.query()` fires
`on_delta` (verified: tracing_agent.py:121 precedes :122). So the first delta of
each step sees a new `n_calls` value → exactly one boundary per step, with no
TracingAgent change:

```python
        streamed = {"buf": ""}
        agent_ref = {"agent": None}     # bound to the TracingAgent in run_engine
        last_step = {"n": -1}

        def emit_step_boundary() -> None:
            # tell the TUI: a NEW prose block begins (close any open one).
            upd = with_meta(message_chunk(""), {"stream_reset": True})
            asyncio.run_coroutine_threadsafe(
                self._conn.session_update(session_id, upd), loop).result()

        def emit_delta(piece: str) -> None:
            # first delta of a NEW step (new n_calls) → boundary first. This covers
            # FormatError steps that never emit a tool event (finding #4).
            n = getattr(agent_ref["agent"], "n_calls", 0)
            if n != last_step["n"]:
                last_step["n"] = n
                emit_step_boundary()
            streamed["buf"] += piece
            asyncio.run_coroutine_threadsafe(
                self._conn.session_update(session_id, message_chunk(piece)), loop).result()
```

In `run_engine`, after constructing the `TracingAgent`:

```python
            agent_ref["agent"] = agent
            model = agent.model
            model.on_delta = emit_delta if hasattr(model, "on_delta") else None
            try:
                result = agent.run(text, prior=prior)
                return {"stop_reason": "end_turn",
                        "exit_status": result.get("exit_status", "end_turn"),
                        "assistant": flatten_agent_messages(agent.messages),
                        "streamed": streamed["buf"]}
            except Exception:
                return {"stop_reason": "refusal", "exit_status": "refusal",
                        "assistant": flatten_agent_messages(getattr(agent, "messages", [])),
                        "streamed": streamed["buf"]}
            finally:
                if hasattr(model, "on_delta"):
                    model.on_delta = None     # never marshal to a dead loop later
```

(The model is created by `self._model_factory(...)`; bind `on_delta` on it
directly. The mock model has no `on_delta` attribute, so `hasattr` guards it →
mock mode emits nothing, unchanged.)

In `prompt()`'s agent-path block, change the recorded assistant text on failure
to the streamed buffer (finding #2):

```python
        engine = await self._run_agent_turn(loop, session_id, state, text, load.block, transcript)
        stop_reason = engine["stop_reason"]
        if stop_reason == "refusal":
            # streamed-on-screen == stored: never fold prior-turn prose in.
            assistant = engine.get("streamed", "") or engine["exit_status"] or stop_reason
        else:
            assistant = engine["assistant"] or engine["exit_status"] or stop_reason
        self._store.record(session_id, {"prompt": text, "stop_reason": stop_reason, "kind": "agent"})
        self._store.extend(session_id, [
            {"role": "user", "content": text, "origin": "agent"},
            {"role": "assistant", "content": assistant, "origin": "agent"}])
        return acp.PromptResponse(stop_reason=stop_reason)
```

Now replace the skipped test from Step 1 with the two real assertions (deltas in
order via `conn.message_texts()`; `model.on_delta is None` after the turn), and
add a third:

```python
def test_failure_records_streamed_buffer_not_prior_turn(...):
    """A turn that streams 'AB' then fails records 'AB' (or '') as this turn's
    assistant — never a prior turn's assistant prose."""
    # prior transcript has an assistant 'OLD ANSWER'; the failing turn streams
    # 'AB'; assert store.transcript's last assistant == 'AB' (or empty), not 'OLD ANSWER'.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_acp_agent_streaming.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add harness/acp_agent.py tests/test_acp_agent_streaming.py
git commit -m "feat(streaming): emit agent-path deltas + step boundary + failure transcript"
```

---

### Task 5: TUI — fresh block after a boundary widget (findings #1, #4)

**Files:**
- Modify: `harness/tui/app.py` — `_stream_message` (≈521-560) new-vs-late decision; `on_session_update` (≈562-600) handle the `stream_reset` meta flag.
- Test: `tests/test_tui_pilot.py` (add cases, following the existing patterns)

**Interfaces:**
- Consumes: the `stream_reset` `_meta` flag emitted in Task 4; `acp.start_tool_call`, `acp.update_agent_message_text` (used by existing tests).
- Produces: per-step prose opens a distinct Markdown widget; the genuine late-delta case still extends the prior widget.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_tui_pilot.py
from acp import start_tool_call   # tool-call start update constructor


def test_prose_after_tool_opens_new_block():
    """Step-1 prose, then a tool line, then step-2 prose must land in a SEPARATE
    Markdown widget below the tool line — not be appended into step-1's widget."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await app._enter_conversation()
            app._session_id = "fake-session"

            app.on_session_update(SessionUpdate(update_agent_message_text("step one")))
            await pilot.pause()
            app.on_session_update(SessionUpdate(start_tool_call(
                tool_call_id="tc1", title="$ ls")))
            await pilot.pause()
            app.on_session_update(SessionUpdate(update_agent_message_text("step two")))
            await pilot.pause()

            scroll = app.query_one("#transcript", VerticalScroll)
            md_sources = [_md_source(md) for md in scroll.query(Markdown)]
        assert md_sources == ["step one", "step two"], (
            f"step-2 prose did not open a new block: {md_sources!r}")

    asyncio.run(go())


def test_explicit_stream_reset_opens_new_block():
    """A message_chunk carrying _meta stream_reset closes the open block so the
    next delta starts fresh (covers FormatError steps with no tool event)."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await app._enter_conversation()
            app._session_id = "fake-session"

            app.on_session_update(SessionUpdate(update_agent_message_text("aaa")))
            await pilot.pause()
            reset = update_agent_message_text("")
            reset.field_meta = {"stream_reset": True}
            app.on_session_update(SessionUpdate(reset))
            await pilot.pause()
            app.on_session_update(SessionUpdate(update_agent_message_text("bbb")))
            await pilot.pause()

            scroll = app.query_one("#transcript", VerticalScroll)
            md_sources = [_md_source(md) for md in scroll.query(Markdown)]
        assert md_sources == ["aaa", "bbb"], f"stream_reset did not split blocks: {md_sources!r}"

    asyncio.run(go())
```

The existing `test_late_prior_turn_delta_does_not_start_block_under_next_prompt`
(line 132) MUST still pass — it locks the genuine late-delta behavior.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest "tests/test_tui_pilot.py::test_prose_after_tool_opens_new_block" "tests/test_tui_pilot.py::test_explicit_stream_reset_opens_new_block" -q`
Expected: FAIL — `test_prose_after_tool_opens_new_block` shows `["step onestep two"]` (merged into one widget); `test_explicit_stream_reset_opens_new_block` shows `["aaabbb"]`.

- [ ] **Step 3: Implement the TUI changes**

In `_stream_message` (app.py ≈542-556), change the new-vs-late decision so that a
**non-message widget appended after the streaming widget** forces a new block.
Replace the branch block:

```python
        kids = list(self._transcript.children)
        prior_is_last = self._streaming_md is not None and kids and kids[-1] is self._streaming_md
        # A boundary widget (tool line / thought / meta / working indicator)
        # appended AFTER the streaming widget means a genuinely new prose block
        # follows — NOT a late delta of the just-closed answer.
        boundary_after = (self._streaming_md is not None and not prior_is_last
                          and self._streaming_md in kids)

        if self._stream_closed and self._streaming_md is not None \
                and not prior_is_last and not boundary_after:
            # true late delta (closed by turn end, nothing newer appended that the
            # widget isn't already adjacent to) → extend the prior widget in place.
            pass
        elif self._streaming_md is None or self._stream_closed:
            # new answer / new step → fresh widget at the bottom; stream now OPEN.
            self._hide_working()
            self._streaming_md = Markdown("")
            self._append(self._streaming_md)
            self._stream_buf = ""
            self._stream_closed = False
        self._stream_buf += text
        md, buf = self._streaming_md, self._stream_buf
        self.call_after_refresh(md.update, buf)
        self._transcript.scroll_end(animate=False)
```

> Implementer note: `boundary_after` distinguishes "the streaming widget is still
> in the tree but no longer last because a tool/meta line was appended after it"
> (→ new block) from the late-delta case the original code targeted (widget
> finalized at turn end, a lagging delta arrives, nothing structurally newer). If
> the existing late-delta test breaks, the discriminator is wrong — the late case
> keys on `_stream_closed` set by `_add_user_message`/turn-end with the widget
> still the effective tail. Adjust `boundary_after` so BOTH new tests AND
> `test_late_prior_turn_delta_does_not_start_block_under_next_prompt` pass.

In `on_session_update` (app.py ≈575), before the `harness_chips` loop, handle the
reset flag so an empty `stream_reset` chunk closes the block without rendering an
empty line:

```python
        meta = getattr(msg.update, "field_meta", None)
        if isinstance(meta, dict) and meta.get("stream_reset"):
            self._end_stream()
            return
```

- [ ] **Step 4: Run the full TUI pilot suite to verify pass + no regression**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_tui_pilot.py -q`
Expected: PASS — the two new tests plus all existing ones (especially the
late-delta and single-widget streaming tests).

- [ ] **Step 5: Commit**

```bash
git add harness/tui/app.py tests/test_tui_pilot.py
git commit -m "feat(streaming): TUI opens a fresh block per agent step (tool + stream_reset boundaries)"
```

---

### Task 6: Full-suite regression + manual smoke

**Files:**
- No production change unless a regression surfaces.
- Test: whole `tests/` suite.

**Interfaces:**
- Consumes: everything above.
- Produces: green suite; documented manual check.

- [ ] **Step 1: Run the whole suite**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/ -q`
Expected: all pass. Pay attention to `test_acp_agent.py`, `test_acp_smoke.py`,
`test_run_traced.py`, `test_tui_pilot.py`, `test_chat_handler.py`.

- [ ] **Step 2: Mock-mode regression check (no deltas, unchanged transcript)**

Confirm a mock-mode agent run still produces tool events only and no
`message_chunk` prose deltas (the FakeModel/mock has no `on_delta`). This is
covered by the existing mock pilot tests; verify they pass unchanged.

- [ ] **Step 3: Manual smoke (real model) — optional, documented**

If VibeProxy is reachable, launch the TUI against it and send a `code_explain`
prompt; confirm prose tokens stream live and each step opens its own block. If
VibeProxy is not available, note that automated tests cover the behavior and
record this step as deferred. Do NOT block completion on network availability.

- [ ] **Step 4: Commit (if any regression fix was needed)**

```bash
git add -A
git commit -m "test(streaming): full-suite green for agent-path streaming"
```

---

## Self-Review

**Spec coverage:**
- §3.1 `StreamingLitellmModel` → Task 1. ✓
- §3.2 wiring `on_delta` / marshaling / clear-in-finally → Task 4. ✓
- §4 data flow (per-step deltas) → Task 4 (`n_calls`-keyed boundary). ✓
- §5 finding #1 TUI boundary fix → Task 5 (`boundary_after`). ✓
- §5/§4 finding #4 FormatError step boundary → Task 4 emits `stream_reset` on first delta of each step; Task 5 handles it. ✓
- §6.1 mid-stream failure → propagates to `refusal` (Task 4 except branch). ✓
- §6.1 finding #2 transcript semantics → Task 4 records `streamed` buffer on failure. ✓
- §6.2 finding #3 no-retry-after-delta → Task 1 guard + Task 2 test. ✓
- §6.3 mock mode unchanged → `hasattr(model, "on_delta")` guard (Task 4) + Task 6. ✓
- §6.4 cost tracking → unchanged (inherited `query`); `cost_tracking="ignore_errors"` preserved in Task 3 factory. ✓
- §6.5 stale callback → cleared in `finally` (Task 4). ✓
- §6.7 finding #5 concurrency → VERIFIED ACP serializes (Global Constraints); no lock added. ✓
- §7 tests → Tasks 1,2,4,5,6. ✓
- Reassembly fidelity (§3.1) → Task 2. ✓

**Placeholder scan:** Task 4's Step 1 ships a `pytest.skip` placeholder test by
design, with an explicit implementer note to replace it in Step 3 against the
real `prompt()` signature (which must be modeled on existing `test_acp_smoke.py`
/ `test_acp_session_context.py`). This is the one spot the plan defers concrete
code, because the exact `PromptRequest` construction is project-idiomatic and
must match neighbors. All other steps carry full code.

**Type consistency:** `on_delta` (attr, `Callable[[str], None] | None`),
`_extract_delta(chunk) -> str`, `streamed["buf"]` / `engine["streamed"]`,
`stream_reset` meta key, `boundary_after` — names are consistent across Tasks
1, 3, 4, 5.

**Known soft spot (flag for the executor):** Task 4 Step 3's step-boundary keying
on `agent.n_calls` assumes `n_calls` increments once per model call before
`emit_delta` fires (true per tracing_agent.py:121). If a future upstream change
moves that increment, the per-step boundary would misfire; the Task 5 tests
(tool-separated blocks) would still pass, but a no-tool multi-step FormatError
sequence could merge — covered by the `test_explicit_stream_reset_opens_new_block`
contract. Keep the boundary logic in `acp_agent.py`, not upstream.
