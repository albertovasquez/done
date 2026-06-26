# Agent-path prose streaming — design

**Status:** spec (ready for writing-plans)
**Date:** 2026-06-26
**Author:** Alberto Vasquez (with Claude Opus 4.8)
**Worktree/branch:** `worktree-agent-path-streaming`

---

## 1. Problem

On the **agent path** (any `task_type` that dispatches the mini-swe-agent loop —
`code_explain`, `code_edit`, etc.), the model's prose never streams to the TUI.
The whole turn runs to completion behind a blocking model call and the assistant
text materializes as a single block at the very end. A turn that is mostly
explanation (e.g. a `code_explain` question) shows an empty body with only a
`▣ Build · <model> · 14.9s` footer ticking — it reads as frozen.

Concretely, today:

- `acp_agent.py::_run_agent_turn → run_engine` calls `agent.run(text)` (blocking)
  and only *after* it returns produces `assistant = flatten_agent_messages(...)`.
- `TracingAgent.query()` calls `model.query()` → upstream
  `LitellmModel.query()` → `litellm.completion(tools=[BASH_TOOL])` **without**
  `stream=True`. One blocking request, whole message back.
- During the run, the only things sent to the TUI are tool-call start/done
  events (via `on_command`). Prose `content` is not emitted incrementally.

By contrast the **chat path** (`ChatHandler.answer_stream`) already streams
properly (`stream=True`, one `message_chunk` per delta), and the **TUI receiving
end already accumulates deltas** into a live Markdown widget. The gap is purely
on the agent path's *producing* side.

## 2. Goal & scope

**Goal:** stream the agent model's prose **on every step** of the loop (not just
the final answer), so narration appears live from the first token, while
tool-call events continue to render as they do today.

**In scope:**
- A streaming-capable model wrapper that emits prose deltas as they arrive and
  still returns the exact complete-response shape upstream parsing requires.
- Wiring an `on_delta` callback from the agent turn (`acp_agent.py`) through to
  that wrapper, marshaling each delta to the asyncio loop as a `message_chunk`.

**Explicitly out of scope (YAGNI):**
- Streaming tool-call *arguments* as they generate (tool start/done events
  already cover this surface).
- Any new TUI widget or TUI-side change (see §5 — the receiving end is complete).
- A user-facing "streaming on/off" config knob (streaming is implicitly on for
  the real-model path; off for mock/tests via `on_delta is None`).
- The CLI path (`run_traced.py`) and the chat path (already streams).

**Success criteria:**
1. A `code_explain` turn shows prose tokens appearing live, starting within the
   first model deltas, with no change to tool-call rendering.
2. Multi-step turns show each step's narration as its own block, separated by the
   tool-call lines already emitted between steps.
3. Tool-call parsing, cost tracking, and `FormatError` handling behave
   identically to today (verified by reassembly-fidelity test, §6).
4. Mock mode and all existing tests are unchanged (streaming falls through to the
   blocking path when `on_delta is None`).

## 3. Architecture

### 3.1 New component: `StreamingLitellmModel`

A thin subclass of upstream `LitellmModel`, living in `harness/` (no upstream
edit — same discipline as `TracingAgent`). It overrides **only `_query`**;
`query()` is inherited unchanged so all of upstream's post-call logic
(`_parse_actions`, `_calculate_cost`, `FormatError` persistence,
`message["extra"]` assembly) runs on the reassembled response exactly as before.

```python
class StreamingLitellmModel(LitellmModel):
    def __init__(self, *, on_delta=None, **kwargs):
        super().__init__(**kwargs)
        self.on_delta = on_delta   # Callable[[str], None] | None; set per run

    def _query(self, messages, **kwargs):
        if self.on_delta is None:
            return super()._query(messages, **kwargs)   # blocking path (mock/tests)
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
                piece = (chunk.choices[0].delta.content or "") if chunk.choices else ""
                if piece:
                    self.on_delta(piece)
        except litellm.exceptions.AuthenticationError as e:
            e.message += " You can permanently set your API key with `mini-extra config set KEY VALUE`."
            raise
        rebuilt = litellm.stream_chunk_builder(chunks, messages=messages)
        if rebuilt is None:
            # reassembly produced nothing usable → one blocking retry, then let
            # upstream's normal parsing/FormatError machinery take over.
            return super()._query(messages, **kwargs)
        return rebuilt
```

Key properties:
- `on_delta is None` ⇒ identical to upstream (mock, unit tests, any non-TUI
  caller). Streaming is opt-in by setting the callback.
- The streamed prose is emitted live via `on_delta`; the **complete** response is
  rebuilt with `litellm.stream_chunk_builder(chunks)` — verified available in the
  vendored litellm (1.89.4), returns a `ModelResponse` with `tool_calls`
  reassembled, the same type a blocking `completion()` returns.
- The `AuthenticationError` message augmentation from upstream `_query` is
  preserved on the streaming path so behavior matches.

### 3.2 Wiring the callback (`acp_agent.py`)

`_run_agent_turn` already builds an `AcpEnvironment` and the `on_command`
marshaling closure. We add a sibling closure `emit_delta(piece)` that uses the
**same** `asyncio.run_coroutine_threadsafe(... session_update(message_chunk(piece)) ...)`
idiom already proven on the chat path (`acp_agent.py:145–149`) and the tool path
(`on_command`). No new concurrency mechanism.

The model is constructed via `self._model_factory(self._worker_model_id)`. The
factory must return a `StreamingLitellmModel` for the real-model path. We set
`model.on_delta = emit_delta` for the duration of `run_engine`, and clear it
(`model.on_delta = None`) in a `finally` so a model instance reused across turns
never holds a stale callback bound to a finished turn's loop.

Mock mode: the mock model has no `on_delta` attribute / ignores it ⇒ no deltas,
unchanged. (Setting an attribute the mock ignores is harmless; we guard with a
`setattr`-only-if-supported check or simply set it and rely on the mock not
reading it — decided in planning, both are trivial.)

## 4. Data flow

```
TracingAgent.query()                      (worker thread)
  model.on_delta already set by run_engine
        │
        ▼
model.query() → _query()  stream=True
  ├─ per prose token ─────────────▶ on_delta(piece) = emit_delta(piece)
  │                                      │  run_coroutine_threadsafe → loop
  │                                      ▼
  │                            conn.session_update(session_id, message_chunk(piece))
  │                                      ▼
  │                            TUI on_session_update → render_update → kind=="message"
  │                                      ▼
  │                            _stream_message(piece)  → live Markdown widget (EXISTS)
  └─ at stream end ───────────▶ stream_chunk_builder(chunks) → full ModelResponse
                                         │
                                         ▼
        query() (inherited): _parse_actions / _calculate_cost / extra  (UNCHANGED)
                                         ▼
        emit "llm.return" (unchanged) ; loop continues to execute_actions
                                         ▼
        tool call → on_command → tool_call_start  → TUI kind=="tool"
                                         ▼
        TUI _end_stream() on the tool line  → next step's prose opens a fresh block
```

## 5. Step boundaries — no TUI change required

The TUI is **already** equipped for per-step prose, because its
`on_session_update` (`harness/tui/app.py`) closes the streaming block on a tool
call:

- `kind == "message"` deltas accumulate into one live Markdown widget
  (`_stream_message`).
- `kind == "tool"` **already calls `_end_stream()`** (app.py:591), and a
  `thought` likewise (app.py:585).

Since every loop step ends with a tool call (the agent acts via `bash`), the
existing tool-call event that already fires between steps auto-closes step N's
prose block; step N+1's first delta opens a fresh widget via the existing
"new answer" branch in `_stream_message`. **Therefore the producing side
(`StreamingLitellmModel` + the `emit_delta` wiring) is the only thing that
changes.** No new TUI widget, no new close signal, no renderer edit.

`render_update` already maps an ACP `message_chunk` to `kind == "message"`
(proven by the chat path, which emits the same `message_chunk`), so streamed
agent prose lands in `_stream_message` with no routing change.

## 6. Error handling

1. **Stream raises mid-flight** (e.g. network drop after N tokens): the partial
   `chunks` are discarded and the exception propagates exactly as a blocking-call
   failure does today → existing `run_engine` `except` branch → `stop_reason
   = "refusal"`. We do **not** salvage a partial response. Already-streamed text
   stays on screen (it is what the model actually said); the turn ends via the
   existing error path. *(User-confirmed default.)*
2. **`stream_chunk_builder` returns `None`/unusable**: one blocking
   `super()._query()` retry (§3.1); if that too fails to parse, upstream's normal
   `FormatError` machinery handles it as today. Cheap insurance against
   reassembly edge cases.
3. **Mock mode / no real model**: `on_delta` unset/ignored ⇒ blocking path ⇒
   identical to today.
4. **Cost tracking**: `stream_chunk_builder` yields a response
   `litellm.cost_calculator.completion_cost` accepts. If a provider omits usage
   on streamed responses, the existing `cost_tracking="ignore_errors"` (set on
   the vibeproxy model) already swallows the error; cost may read 0.0 for that
   call, which is the pre-existing behavior for unpriced responses.
5. **Stale callback across turns**: `run_engine` clears `model.on_delta = None`
   in a `finally`, so a model instance reused by a later turn never marshals to a
   dead event loop.
6. **Cancellation**: the existing `state.cancel_flag` checks around `run_engine`
   are unchanged. A cancel mid-stream lets the in-flight `_query` finish or raise
   per litellm; the post-run cancel check already returns `cancelled`. No new
   cancellation path is introduced. (If mid-stream hard-stop is later desired,
   it is a follow-up — out of scope here.)

## 7. Testing

All tests run offline (target `tests/`, per AGENTS.md).

- **Unit — delta emission & branch selection** (`StreamingLitellmModel._query`):
  monkeypatch `litellm.completion` to return a fake chunk iterator.
  - Assert `on_delta` is called once per prose token, in order.
  - Assert `on_delta=None` takes the `super()._query()` blocking branch (mock).
- **Unit — reassembly fidelity:** feed a recorded chunk sequence containing a
  `bash` tool-call; assert the object returned by `_query` yields, after the
  inherited `query()`, the same `extra.actions` a blocking response would (i.e.
  tool-calls survive `stream_chunk_builder`).
- **Unit — `stream_chunk_builder` returns None:** assert one blocking retry
  occurs and its result is returned.
- **Integration — acp_agent:** a fake model whose `query` invokes its bound
  `on_delta` twice; assert exactly two `message_chunk` `session_update`s are sent
  in order and the turn completes `end_turn` (mirrors the existing chat-path
  test); assert `on_delta` is cleared after the turn.
- **Regression:** a mock-mode agent run emits no `message_chunk` deltas and
  produces the same final transcript as today.

## 8. Files touched

- **New:** `harness/streaming_model.py` (`StreamingLitellmModel`).
- **Edit:** `acp_agent.py` — add `emit_delta` closure in `_run_agent_turn`, set/
  clear `model.on_delta` around `run_engine`.
- **Edit:** the real-model factory (wherever `_model_factory` builds the vibeproxy
  `LitellmModel`) to construct `StreamingLitellmModel` instead — confirm exact
  location during planning (`run_traced.py::_build_vibeproxy_model` and/or the ACP
  model factory).
- **New tests:** `tests/test_streaming_model.py`, additions to the acp_agent test
  module.
- **No upstream edit. No TUI edit.**

## 9. Open items for the plan (not blockers)

- Confirm the single source of the agent-path model factory and whether the CLI
  (`run_traced.py`) should also adopt the streaming wrapper (it prints joined
  pieces, so streaming there is cosmetic — likely leave as-is).
- Decide mock-mode `on_delta` handling: set-and-ignore vs. capability check
  (trivial; pick the simpler at implementation time).
