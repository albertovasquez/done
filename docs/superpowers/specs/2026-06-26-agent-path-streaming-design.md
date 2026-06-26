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
- Any *new* TUI widget. (A *scoped* `_stream_message` boundary fix IS in scope —
  see §5; the earlier "no TUI change" claim was wrong, per Codex finding #1.)
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
        TUI _end_stream() on the tool line  → STEP BOUNDARY (see §5)
```

## 5. Step boundaries — a scoped TUI change IS required

> **Correction (Codex review, finding #1 — verified against live code).** An
> earlier draft claimed no TUI change was needed. That is false. The existing
> `_stream_message` late-delta logic mis-routes per-step prose.

**The verified failure.** Walk a two-step run through `harness/tui/app.py`:

1. Step 1 prose → opens a live Markdown widget (`_stream_message`, app.py:549).
2. Tool call → `on_session_update` `kind=="tool"` calls `_end_stream()`
   (`_stream_closed=True`) and appends the tool line (app.py:590–593). The
   streaming widget is now **no longer the last child**.
3. Step 2's first delta re-enters `_stream_message`. With `_stream_closed==True`
   and the streaming widget not-last, line 545 matches the **late-delta branch**
   (app.py:545–548 `pass`) → step 2's prose is appended **into step 1's widget**,
   sitting *above* the tool line — not a fresh block below it.

The current logic was built for *one* answer plus trailing late deltas; it cannot
tell "a genuinely new prose block after a tool line" from "a lagging delta of the
just-closed answer." Streaming agent prose introduces the first case, which never
occurred before (the agent path emitted prose only once, at the end).

**The fix (small, localized).** Distinguish the two cases by *what* is currently
last in the transcript:

- If the last child is a **non-message** widget (a tool line, thought, meta line,
  or the working indicator) and `_stream_closed`, the next delta is a **new
  block** → open a fresh widget.
- The true late-delta case (extend the prior widget in place) only applies when
  **nothing newer than the streaming widget has been appended** yet the stream was
  closed by a turn boundary — i.e. the streaming widget is still effectively the
  tail of the answer.

Concretely this is a change to the branch condition at app.py:545–556 (and
nothing else): the "late delta → extend in place" branch must additionally
require that no boundary widget (tool/thought/meta) was appended after the
streaming widget. A boundary widget after it means "new block." This keeps the
genuine notification-lag protection the original comment describes while making
tool-separated steps open distinct blocks.

`render_update` already maps `AgentMessageChunk` → `kind == "message"`
(render.py:38–43; proven by the chat path), so the *routing* is unchanged — only
the new-vs-late decision inside `_stream_message` changes.

**FormatError steps (finding #4).** A model response with prose but no parseable
tool call raises `FormatError`, which `TracingAgent.run` catches and loops on
*without* emitting any tool/thought ACP event (tracing_agent.py:76–84) — so no
boundary widget is appended and the next step's prose would merge into the prior
block. To give every step a boundary regardless of outcome, the agent path emits
an explicit **step-boundary signal** at the start of each LLM call's streamed
prose: before the first delta of a step, `emit_delta` sends a zero-width
boundary (reusing the `thought`/close path the TUI already understands, or a
dedicated empty `message_chunk` carrying a `_meta` "stream_reset" flag the TUI
treats as `_end_stream` + fresh-block). The exact wire form is chosen in the plan
(see §9); the requirement is: **each LLM step's prose begins a fresh TUI block,
whether or not it ends in a tool call.**

## 6. Error handling

1. **Stream raises mid-flight** (e.g. network drop after N tokens): the partial
   `chunks` are discarded and the exception propagates exactly as a blocking-call
   failure does today → existing `run_engine` `except` branch → `stop_reason
   = "refusal"`. We do **not** salvage a partial response. Already-streamed text
   stays on screen (it is what the model actually said); the turn ends via the
   existing error path. *(User-confirmed default.)*

   **Transcript semantics on failure (Codex finding #2 — verified).**
   `flatten_agent_messages` (transcript.py:11–24) joins **every** assistant-role
   message in `agent.messages`, and `TracingAgent.run` seeds the injected `prior`
   transcript *before* the current user prompt (tracing_agent.py:67–71). So the
   `run_engine` failure return `flatten_agent_messages(getattr(agent, "messages",
   []))` (acp_agent.py:276–278) can fold **prior-turn** assistant prose into the
   string that acp_agent.py:168–173 then records as **this** turn's assistant —
   diverging stored transcript from what the user saw streamed.
   **Required behavior:** on a failed turn, the recorded assistant text for this
   turn MUST be exactly the prose this turn produced (the accumulated streamed
   deltas), or empty — never prior-turn content. The plan implements this by
   accumulating the emitted deltas in `run_engine` (same buffer the chat path
   keeps) and, on failure, recording **that** buffer as the assistant turn rather
   than `flatten_agent_messages(all messages)`. This makes streamed-on-screen and
   stored-transcript identical by construction.
2. **`stream_chunk_builder` returns `None`/unusable — NO retry after any delta
   was emitted (Codex finding #3 — verified).** A blocking retry would produce a
   *different* response than the prose the user already saw stream, so the visible
   text and the committed actions/transcript would come from two different
   generations. Therefore:
   - If **zero** deltas were emitted when reassembly returns `None` (e.g. an
     empty/garbled stream), fall back to one blocking `super()._query()` — safe,
     nothing was shown yet.
   - If **any** delta was emitted, do **not** retry. Treat it as a stream failure
     per case #1 (propagate → `refusal`), recording the streamed buffer as the
     turn's assistant per finding #2. The user keeps the text they saw; we never
     swap in a discarded generation.
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
7. **Concurrent prompts on one session (Codex finding #5 — UNVERIFIED
   assumption, flagged, not a blocker).** `SessionState` (acp_session.py:11–16)
   has no per-session turn lock, and `prompt()` reads/writes shared state
   (acp_agent.py:98–107, 166–174). The TUI has a *client-side* busy guard
   (app.py:491–511) but that is not an ACP-layer invariant. Token-level streaming
   does not *create* this race — it would already affect interleaved tool events —
   but it makes interleaving more visible. This spec **does not** add a lock; it
   records the assumption that **the ACP layer serializes prompts per session.**
   The plan MUST verify this against `acp.run_agent`'s request handling before
   implementation; if prompts are not serialized, add a per-session active-turn
   guard as a prerequisite. (Pushing back on framing this as streaming-specific:
   it is a pre-existing property we are now relying on more visibly.)

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
- **Unit — `stream_chunk_builder` returns None, zero deltas:** assert one
  blocking `super()._query()` fallback occurs and its result is returned.
- **Unit — `stream_chunk_builder` returns None, after ≥1 delta:** assert **no**
  retry; the failure propagates (finding #3).
- **Unit — TUI step boundary (finding #1):** drive `_stream_message` with
  delta(s) → a `tool` update → delta(s); assert the second prose run lands in a
  **new** Markdown widget below the tool line, not appended to the first widget.
  Also assert the genuine late-delta case (delta, close-by-turn-end, no boundary
  widget appended, late delta) STILL extends the prior widget in place.
- **Unit — FormatError step boundary (finding #4):** a step that yields prose but
  raises `FormatError` (no tool event) followed by a retry step's prose lands in a
  fresh block (verifies the explicit step-boundary signal, §5).
- **Integration — acp_agent:** a fake model whose `query` invokes its bound
  `on_delta` twice; assert exactly two `message_chunk` `session_update`s are sent
  in order and the turn completes `end_turn` (mirrors the existing chat-path
  test); assert `on_delta` is cleared after the turn.
- **Integration — failure transcript (finding #2):** a fake model that emits two
  deltas then raises mid-stream, run on a session with a non-empty `prior`
  transcript; assert the recorded assistant turn equals the two streamed deltas
  (or empty) — NEVER the prior-turn assistant prose.
- **Regression:** a mock-mode agent run emits no `message_chunk` deltas and
  produces the same final transcript as today.

## 8. Files touched

- **New:** `harness/streaming_model.py` (`StreamingLitellmModel`).
- **Edit:** `acp_agent.py` —
  - add `emit_delta` closure in `_run_agent_turn` (marshal each delta as a
    `message_chunk`), accumulating deltas into a per-turn buffer;
  - emit a per-step boundary signal at each LLM step's first delta (§5/§4 fix);
  - set/clear `model.on_delta` around `run_engine` (clear in `finally`);
  - on failure, record the streamed buffer as this turn's assistant text instead
    of `flatten_agent_messages(all messages)` (finding #2).
- **Edit (TUI — REQUIRED, was wrongly omitted):** `harness/tui/app.py`
  `_stream_message` — refine the new-block-vs-late-delta decision so a boundary
  widget (tool/thought/meta) appended after the streaming widget forces a fresh
  block (finding #1). Possibly a tiny `on_session_update` handler for the
  step-boundary signal if a dedicated wire form is chosen (§9).
- **Edit:** the real-model factory (wherever `_model_factory` builds the vibeproxy
  `LitellmModel`) to construct `StreamingLitellmModel` instead — confirm exact
  location during planning (`run_traced.py::_build_vibeproxy_model` and/or the ACP
  model factory).
- **New tests:** `tests/test_streaming_model.py`; TUI `_stream_message` boundary
  tests; additions to the acp_agent test module.
- **No upstream edit.** (TUI edit is required — correcting the earlier draft.)

## 9. Open items for the plan (not blockers)

- **Verify ACP per-session prompt serialization** (finding #5) before coding; add
  a per-session active-turn guard if prompts are not serialized.
- **Choose the step-boundary wire form** (§5/§4): reuse the existing
  thought/close path vs. a dedicated empty `message_chunk` + `_meta`
  "stream_reset" flag. Pick the one that needs the smallest TUI handler.
- Confirm the single source of the agent-path model factory and whether the CLI
  (`run_traced.py`) should also adopt the streaming wrapper (it prints joined
  pieces, so streaming there is cosmetic — likely leave as-is).
- Decide mock-mode `on_delta` handling: set-and-ignore vs. capability check
  (trivial; pick the simpler at implementation time).
