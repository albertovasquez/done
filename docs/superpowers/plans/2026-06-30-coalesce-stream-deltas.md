# Coalesce Streamed Prose Deltas Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the per-token blocking `run_coroutine_threadsafe(...).result()` on the streaming hot path by buffering prose worker-side and flushing it to the ACP connection at ~80ms intervals, with ordering and transcript integrity preserved.

**Architecture:** A small per-turn prose buffer (guarded by a `threading.Lock`) accumulates litellm deltas on the worker thread. A loop-side ~80ms timer drains it via one `session_update`. A single `_flush_prose` chokepoint puts prose on the wire; every non-prose event (step boundary, tool call, plan update) and the turn-end teardown flush the pending prose first, preserving call order on the wire. The failure-case transcript buffer (`streamed["buf"]`) is untouched — delivery and transcript stay separate sources of truth.

**Tech Stack:** Python 3.11+, asyncio, `threading`, ACP SDK, pytest. Agent runs on a worker thread via `loop.run_in_executor`; the ACP connection is owned by the asyncio loop.

## Global Constraints

- **Worktree only:** all work happens in `~/Work/Quiubo/harness/.worktrees/coalesce-stream-deltas` on branch `coalesce-stream-deltas`. Never edit the primary checkout. Run `pwd` before editing.
- **Test command (from worktree root):** `.venv/bin/python -m pytest tests/ -q` (target `tests/` only). For a single file: `.venv/bin/python -m pytest tests/test_acp_agent_streaming.py -q`.
- **Byte-identical rendered output:** the concatenation of all streamed prose must equal what the un-coalesced path produced. No prose may be dropped, reordered, or duplicated.
- **`streamed["buf"]` is untouched:** it stays appended synchronously per-piece inside `emit_delta`. It is NOT reconstructed from the delivery buffer.
- **Cancel latency unchanged:** the `state.cancel_flag` check stays per-piece at the top of `emit_delta` — not moved to the timer.
- **Permission/terminal round-trips untouched:** `check_permission` and `client_terminal` stay fully synchronous.
- **All changes live in `harness/acp_agent.py`** (plus tests). No TUI-side changes.

---

## File Structure

- **Modify:** `harness/acp_agent.py`
  - Add `import threading`.
  - In `_run_agent_turn`: add the prose buffer + lock + `_flush_prose` / `_flush_prose_sync` helpers + the per-turn flush timer; modify `emit_delta`, `emit_step_boundary`, `on_command`, `on_plan`; extend the `run_engine` `finally` teardown.
  - In the chat branch (`pump`, inside `prompt`): buffer + timed flush + final flush.
- **Modify:** `tests/test_acp_agent_streaming.py`
  - Relax `test_agent_path_streams_deltas_as_message_chunks` (drop 1:1 delta→chunk).
  - Add regression tests for ordering, turn-end flush, no-leftover-timer, cancel, chat-path flush.

### Testing reality (read before writing any test)

In the existing test fakes, the model's `query()` fires **all** deltas synchronously
in a tight loop, then returns — see `_StreamingSubmitModel.query` (`tests/test_acp_agent_streaming.py:100-104`). The agent engine runs on a worker thread via `loop.run_in_executor(None, run_engine)`; its `finally` runs the **turn-end final flush**. So:

> **Tests are deterministic via the turn-end final flush, NOT the 80ms timer.** Never `sleep` waiting for a timer tick or assert on timer-driven partial flushes. Assert on the *final* wire state after `prompt()` returns: the concatenation of streamed prose, its order relative to boundaries, and presence/absence of leftovers. This keeps tests fast and non-flaky.

---

## Task 1: Prose buffer + `_flush_prose` chokepoint, wired into `emit_delta`

Replace the per-piece blocking send in `emit_delta` with a lock-guarded buffer append, add the flush chokepoint, and add a turn-end final flush so the existing streaming test still delivers all prose (with the 1:1 assertion relaxed).

**Files:**
- Modify: `harness/acp_agent.py` (add `import threading`; `_run_agent_turn` buffer/lock/flush; `emit_delta`; `run_engine` `finally`)
- Test: `tests/test_acp_agent_streaming.py`

**Interfaces:**
- Consumes: existing `self._conn.session_update`, `message_chunk` (from `harness.acp_emit`), `loop`, `session_id` — all already in scope in `_run_agent_turn`.
- Produces (in-function closures, referenced by later tasks):
  - `prose = {"buf": ""}` and `prose_lock = threading.Lock()` — per-turn delivery buffer + guard.
  - `async def _flush_prose() -> None` — loop-side: swap buffer under lock, send accumulated text via `await self._conn.session_update(...)` if non-empty.
  - `def _flush_prose_sync() -> None` — worker-thread-callable: `asyncio.run_coroutine_threadsafe(_flush_prose(), loop).result()`.

- [ ] **Step 1: Relax the existing 1:1 test and add a "all prose delivered, order preserved" assertion**

Replace the body of `test_agent_path_streams_deltas_as_message_chunks` in `tests/test_acp_agent_streaming.py` (currently at lines ~193-204, ending after the `stop_reason` assert — note the current test file truncates there; the boundary/concat asserts that were in earlier revisions are being replaced wholesale by this version):

```python
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
```

- [ ] **Step 2: Run the test to confirm it still passes on current (un-coalesced) code**

Run: `.venv/bin/python -m pytest tests/test_acp_agent_streaming.py::test_agent_path_streams_deltas_as_message_chunks -q`
Expected: PASS (the relaxed assertion is satisfied by the current per-delta sends too — this confirms the new assertion is correct before we change the implementation).

- [ ] **Step 3: Add `import threading` to `harness/acp_agent.py`**

In the import block (near line 12, alongside `import time`), add:

```python
import threading
```

- [ ] **Step 4: Add the prose buffer, lock, and flush helpers in `_run_agent_turn`**

In `harness/acp_agent.py`, find the block in `_run_agent_turn` that initializes the streaming state (currently around lines 551-554):

```python
        streamed = {"buf": ""}
        agent_ref = {"agent": None}     # bound to the TracingAgent in run_engine
        last_step = {"n": -1}
        compacted = {"event": None}     # set if context.compacted fired this turn
```

Immediately after that block, add:

```python
        # Delivery-only prose buffer (distinct from streamed["buf"], the
        # failure-case transcript). Prose accumulates here on the worker thread and
        # is drained to the wire at ~80ms (matching the TUI's 12Hz render), so we
        # stop blocking the worker thread on a per-token RPC round-trip. Ordering
        # vs. boundaries/tool/plan events is preserved by flushing this buffer
        # BEFORE any of those events (the flush-before-send chokepoint).
        prose = {"buf": ""}
        prose_lock = threading.Lock()

        async def _flush_prose() -> None:
            # loop-side: atomically take the pending prose and send it as one chunk.
            with prose_lock:
                text, prose["buf"] = prose["buf"], ""
            if text:
                await self._conn.session_update(session_id, message_chunk(text))

        def _flush_prose_sync() -> None:
            # worker-thread-callable: marshal the flush to the loop and block, the
            # same idiom the boundary/tool/plan callbacks already use.
            asyncio.run_coroutine_threadsafe(_flush_prose(), loop).result()
```

- [ ] **Step 5: Rewrite `emit_delta` to buffer instead of blocking-send**

Find `emit_delta` (currently lines ~562-582) and replace the final two statements (the `streamed["buf"] += piece` followed by the `asyncio.run_coroutine_threadsafe(... message_chunk(piece) ...).result()`) so the function becomes:

```python
        def emit_delta(piece: str) -> None:
            # ESC mid-stream: raising here aborts the model's `for chunk in stream`
            # loop (streaming_model.py) on the next prose token. Cancel latency is
            # bounded by the model's chunk-yield rate, NOT the 80ms flush cadence.
            if state.cancel_flag.is_set():
                raise UserInterruption({
                    "role": "exit", "content": "Cancelled by user.",
                    "extra": {"exit_status": "cancelled", "submission": ""}})
            # first delta of a NEW step (new n_calls) → boundary first. emit_step_
            # boundary flushes the PREVIOUS step's prose tail before the boundary,
            # so the boundary correctly lands between old-step and new-step prose.
            n = getattr(agent_ref["agent"], "n_calls", 0)
            if n != last_step["n"]:
                last_step["n"] = n
                emit_step_boundary()
            # transcript source — UNCHANGED, synchronous, per-piece.
            streamed["buf"] += piece
            # delivery — buffer, do not block. The ~80ms timer + turn-end flush
            # drain it. No .result() per token.
            with prose_lock:
                prose["buf"] += piece
```

- [ ] **Step 6: Add the turn-end final flush in `run_engine`'s `finally`**

Find the `finally` inside `run_engine` (currently lines ~740-743):

```python
                finally:
                    # never marshal a delta to a dead loop after the turn ends.
                    if hasattr(model, "on_delta"):
                        model.on_delta = None
```

Replace it with:

```python
                finally:
                    # never marshal a delta to a dead loop after the turn ends.
                    if hasattr(model, "on_delta"):
                        model.on_delta = None
                    # final flush: deliver any prose still buffered (the last <80ms
                    # that never hit a timer tick). Runs on success, failure, AND
                    # cancellation (this finally covers all three). Flush BEFORE the
                    # timer is cancelled (Task 3) so the tail is never skipped.
                    _flush_prose_sync()
```

(The timer-cancel line is added in Task 3; for now the `finally` just does the final flush. Without the timer yet, this final flush is what delivers all prose in tests — which is exactly the deterministic path.)

- [ ] **Step 7: Run the streaming test — confirm all prose still delivered**

Run: `.venv/bin/python -m pytest tests/test_acp_agent_streaming.py -q`
Expected: PASS. `test_agent_path_streams_deltas_as_message_chunks` passes because the turn-end `_flush_prose_sync()` delivers the full buffered prose as one chunk. `test_failure_records_streamed_buffer_not_prior_turn` passes because `streamed["buf"]` is unchanged. `test_on_delta_cleared_after_turn` passes (the `on_delta = None` line is untouched).

- [ ] **Step 8: Commit**

```bash
git add harness/acp_agent.py tests/test_acp_agent_streaming.py
git commit -m "perf(stream): buffer prose deltas, flush at turn end (no per-token RPC block)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 2: Flush-before-send ordering at every non-prose emit

Make `emit_step_boundary`, `on_command`, and `on_plan` flush pending prose before sending their own event, so a tool/boundary/plan event never overtakes prose that arrived earlier. Add a regression test that proves prose-then-boundary order on the wire.

**Files:**
- Modify: `harness/acp_agent.py` (`emit_step_boundary`, `on_command`, `on_plan`)
- Test: `tests/test_acp_agent_streaming.py`

**Interfaces:**
- Consumes: `_flush_prose_sync` (Task 1).
- Produces: no new symbols; modifies behavior only.

- [ ] **Step 1: Write the failing ordering test**

The model must emit some prose, then trigger a step boundary, with more prose after. We need a fake whose `query()` interleaves an `on_delta` call, then bumps `n_calls` (so `emit_delta` fires a boundary on the next delta), then another `on_delta`. Add this fake and test to `tests/test_acp_agent_streaming.py`:

```python
class _TwoStepStreamingModel(DeterministicToolcallModel):
    """Streams 'A' in step 1, then (by advancing n_calls) streams 'B' in step 2,
    then submits. Used to assert prose-vs-boundary ORDER on the wire: the step-2
    boundary must land AFTER 'A' and BEFORE 'B', never reordered."""

    def __init__(self):
        out = make_toolcall_output(
            "AB",
            [{"id": "call_0", "type": "function",
              "function": {"name": "bash",
                           "arguments": '{"command": "' + _SUBMIT + '"}'}}],
            [{"command": _SUBMIT, "tool_call_id": "call_0"}],
        )
        out["extra"]["cost"] = 0.0
        super().__init__(outputs=[out], cost_per_call=0.0)
        self.on_delta = None
        self._agent = None  # set by the test so we can bump n_calls

    def query(self, messages, **kw):
        # step 1 prose
        if self.on_delta:
            self.on_delta("A")
        # advance the step counter so emit_delta treats "B" as a NEW step → boundary
        if self._agent is not None:
            self._agent.n_calls += 1
        # step 2 prose (fires a boundary first, inside emit_delta)
        if self.on_delta:
            self.on_delta("B")
        return super().query(messages, **kw)


def test_prose_flushed_before_step_boundary(tmp_path):
    """A step boundary must never overtake prose buffered before it: on the wire,
    'A' (step-1 prose) precedes the stream_reset boundary, which precedes 'B'."""
    conn = RecordingConn()
    model = _TwoStepStreamingModel()
    agent = _build(model, conn)
    sid = agent._store.new(cwd=str(tmp_path))

    # bind the agent so the model can bump n_calls mid-query (mirrors how the real
    # TracingAgent increments n_calls per model call).
    orig_factory = agent._model_factory
    def _factory(*a, **k):
        m = orig_factory(*a, **k) if False else model
        return m
    # capture the agent_ref binding: acp_agent sets agent_ref["agent"] = agent in
    # run_engine; we reach it by letting the model see the TracingAgent via on_delta
    # timing. Simplest: monkeypatch model._agent after construction in run_engine is
    # not exposed, so instead drive n_calls through the model's own counter proxy:
    # the model holds its own n_calls and acp_agent reads agent_ref["agent"].n_calls.
    # Bind them: make the model BE the n_calls source the agent reads.
    sid_resp = _prompt(agent, sid, "fix the bug")
    assert sid_resp.stop_reason == "end_turn"

    # Build the ordered event stream: each update is either prose text or a boundary.
    seq = []
    for u in conn.updates:
        txt = getattr(getattr(u, "content", None), "text", "") or ""
        meta = getattr(u, "field_meta", None) or {}
        harness = meta.get("harness", {}) if isinstance(meta, dict) else {}
        is_boundary = isinstance(harness, dict) and harness.get("stream_reset")
        if is_boundary:
            seq.append("<RESET>")
        elif txt:
            seq.append(txt)
    joined = "".join(s for s in seq if s != "<RESET>")
    # 'A' before the (last) reset, 'B' after it.
    assert "A" in joined and "B" in joined
    a_idx = next(i for i, s in enumerate(seq) if "A" in s and s != "<RESET>")
    b_idx = next(i for i, s in enumerate(seq) if "B" in s and s != "<RESET>")
    reset_idxs = [i for i, s in enumerate(seq) if s == "<RESET>"]
    assert a_idx < reset_idxs[-1] < b_idx, (
        f"boundary reordered relative to prose: seq = {seq!r}"
    )
```

> **Implementer note on `n_calls` binding:** `emit_delta` reads `agent_ref["agent"].n_calls`, where `agent_ref["agent"]` is the real `TracingAgent` bound in `run_engine`, NOT the model. The model fake above can't reach it directly. Before writing the final test, check how `TracingAgent.n_calls` advances per `model.query` call (`harness/tracing_agent.py` — `n_calls` is incremented in `query()` BEFORE `model.query()` fires `on_delta`, per the comment at `acp_agent.py:571-575`). If a single model output yields a single step (one `n_calls` value), you cannot get two boundaries from one output. **Adjust the fake to use TWO outputs** (two `DeterministicToolcallModel` outputs → two `query()` calls → two `n_calls` values → a boundary before step 2's first delta), with the first output emitting prose "A" + a non-submit no-op tool call and the second emitting "B" + the submit. This is the faithful way to exercise the boundary path; wire it to mirror the real per-step increment rather than mutating a counter by hand.

- [ ] **Step 2: Run the test to verify it fails (or is flaky) on current code**

Run: `.venv/bin/python -m pytest tests/test_acp_agent_streaming.py::test_prose_flushed_before_step_boundary -q`
Expected: FAIL — on the post-Task-1 code, prose is buffered and only flushed at turn end, so the step-2 boundary (sent immediately by `emit_step_boundary`) lands BEFORE all prose, which is flushed once at the end. The assertion `a_idx < reset_idxs[-1] < b_idx` fails because both "A" and "B" arrive together after the reset.

- [ ] **Step 3: Add `_flush_prose_sync()` to the top of `emit_step_boundary`**

Find `emit_step_boundary` (currently lines ~556-560) and add the flush as the first statement:

```python
        def emit_step_boundary() -> None:
            # Drain pending prose FIRST so this boundary lands after the prose that
            # preceded it on the wire (ordering invariant). Then emit the boundary.
            _flush_prose_sync()
            # tell the TUI: a NEW prose block begins (close any open one).
            upd = with_meta(message_chunk(""), {"stream_reset": True})
            asyncio.run_coroutine_threadsafe(
                self._conn.session_update(session_id, upd), loop).result()
```

- [ ] **Step 4: Add `_flush_prose_sync()` to the top of `on_command`**

Find `on_command` (currently lines ~584-598). Add the flush as the first statement inside the function (before the `if phase == "start":` block):

```python
        def on_command(phase: str, command: str, out: dict | None) -> None:
            # runs on the worker thread → marshal to the loop and block until sent.
            # Flush pending prose FIRST so a tool-call event never overtakes the
            # prose that preceded it.
            _flush_prose_sync()
            if phase == "start":
```

(Leave the rest of `on_command` unchanged.)

- [ ] **Step 5: Add `_flush_prose_sync()` to the top of `on_plan`**

Find `on_plan` (currently lines ~600-604). Add the flush as the first statement:

```python
        def on_plan(entries: list[tuple[str, str]]) -> None:
            # runs on the worker thread → marshal the ACP plan update to the loop.
            # Flush pending prose FIRST (ordering invariant). Full-snapshot replace:
            # the agent re-emits the whole list each time.
            _flush_prose_sync()
            asyncio.run_coroutine_threadsafe(
                self._conn.session_update(session_id, plan_update(entries)), loop).result()
```

- [ ] **Step 6: Run the ordering test — confirm it passes**

Run: `.venv/bin/python -m pytest tests/test_acp_agent_streaming.py::test_prose_flushed_before_step_boundary -q`
Expected: PASS — `emit_step_boundary` now flushes "A" before sending the reset, and "B" is flushed at turn end after the reset, giving order `A, <RESET>, B`.

- [ ] **Step 7: Run the full streaming test file**

Run: `.venv/bin/python -m pytest tests/test_acp_agent_streaming.py -q`
Expected: PASS (all tests, including the relaxed one from Task 1).

- [ ] **Step 8: Commit**

```bash
git add harness/acp_agent.py tests/test_acp_agent_streaming.py
git commit -m "perf(stream): flush pending prose before boundary/tool/plan events

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 3: The ~80ms flush timer + cross-turn no-leftover guarantee

Add the loop-side periodic flush so prose is delivered mid-turn (not only at turn end), and cancel it in the `finally` so no timer from turn N can fire into turn N+1.

**Files:**
- Modify: `harness/acp_agent.py` (`_run_agent_turn`: schedule timer before `run_engine`; cancel in `finally`)
- Test: `tests/test_acp_agent_streaming.py`

**Interfaces:**
- Consumes: `_flush_prose` (Task 1), `loop`.
- Produces: `flush_task` — an `asyncio.Task` running the periodic flush loop; cancelled at turn end.

- [ ] **Step 1: Write the no-leftover-timer regression test**

Two sequential turns on the same agent/session; assert turn 2's wire output contains none of turn 1's prose and that the first turn's prose is fully delivered within its own turn.

```python
def test_no_leftover_flush_across_turns(tmp_path):
    """A flush timer from turn N must not fire into turn N+1. Run two turns; each
    turn's prose appears only within that turn's updates."""
    conn = RecordingConn()
    agent = _build(_StreamingSubmitModel(["one"]), conn)
    sid = agent._store.new(cwd=str(tmp_path))

    r1 = _prompt(agent, sid, "first")
    assert r1.stop_reason == "end_turn"
    after_turn1 = len(conn.updates)
    assert "one" in "".join(conn.message_texts())

    # swap in a model that streams different prose for turn 2
    agent._model_factory = lambda *a, **k: _StreamingSubmitModel(["two"])
    r2 = _prompt(agent, sid, "second")
    assert r2.stop_reason == "end_turn"

    turn2_texts = "".join(
        (getattr(getattr(u, "content", None), "text", "") or "")
        for u in conn.updates[after_turn1:]
    )
    assert "two" in turn2_texts
    assert "one" not in turn2_texts, "turn-1 prose leaked into turn 2 (leftover timer)"
```

- [ ] **Step 2: Run it to confirm it passes pre-timer (baseline) — it should**

Run: `.venv/bin/python -m pytest tests/test_acp_agent_streaming.py::test_no_leftover_flush_across_turns -q`
Expected: PASS even now (no timer exists yet; final flush per turn already isolates turns). This test is the *guard* we must not break when we add the timer in Step 3 — running it first establishes the baseline.

- [ ] **Step 3: Schedule the periodic flush timer before `run_engine`, cancel it in `finally`**

Find where `run_engine` is dispatched (currently line 764):

```python
        engine = await loop.run_in_executor(None, run_engine)
```

Replace it with a guarded schedule + dispatch + cancel:

```python
        async def _flush_loop() -> None:
            # ~80ms cadence matches the TUI's 12Hz render; finer delivery is
            # invisible (the TUI buffers and paints on its own timer).
            try:
                while True:
                    await asyncio.sleep(0.08)
                    await _flush_prose()
            except asyncio.CancelledError:
                return

        flush_task = loop.create_task(_flush_loop())
        try:
            engine = await loop.run_in_executor(None, run_engine)
        finally:
            # stop the periodic flusher so it can never fire into a later turn.
            # run_engine's own finally already did the FINAL prose flush, so no
            # tail is lost by cancelling here.
            flush_task.cancel()
```

- [ ] **Step 4: Run the no-leftover test — confirm still passing with the timer present**

Run: `.venv/bin/python -m pytest tests/test_acp_agent_streaming.py::test_no_leftover_flush_across_turns -q`
Expected: PASS — `flush_task.cancel()` in the `finally` guarantees turn 1's timer is dead before turn 2 starts.

- [ ] **Step 5: Run the full streaming file**

Run: `.venv/bin/python -m pytest tests/test_acp_agent_streaming.py -q`
Expected: PASS (all). The final flush in `run_engine`'s `finally` runs before `flush_task.cancel()` in the outer `finally`, so determinism holds.

- [ ] **Step 6: Commit**

```bash
git add harness/acp_agent.py tests/test_acp_agent_streaming.py
git commit -m "perf(stream): add ~80ms loop-side flush timer, cancel at turn end

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 4: Cancel-mid-stream clean teardown test

Prove that ESC mid-stream tears down cleanly: prose buffered up to the cancel point is delivered by the final flush, the timer is cancelled, and nothing is delivered after the turn resolves.

**Files:**
- Test: `tests/test_acp_agent_streaming.py` (no production change expected — this verifies Task 1-3 behavior)

**Interfaces:**
- Consumes: existing cancel path (`state.cancel_flag`, `UserInterruption`).

- [ ] **Step 1: Write the cancel-mid-stream test**

A model that streams a delta, then sets the session's cancel flag, then streams again (the second `emit_delta` raises `UserInterruption`). The turn must resolve and the pre-cancel prose must be delivered.

```python
class _StreamThenCancelModel(DeterministicToolcallModel):
    """Streams 'before', trips the cancel flag, then streams 'after' — the second
    on_delta call raises UserInterruption inside emit_delta. Used to assert clean
    teardown: 'before' is delivered, the turn resolves, nothing lands afterward."""

    def __init__(self, cancel_flag):
        super().__init__(outputs=[make_toolcall_output("", [], [])], cost_per_call=0.0)
        self.on_delta = None
        self._cancel_flag = cancel_flag

    def query(self, messages, **kw):
        if self.on_delta:
            self.on_delta("before")
        self._cancel_flag.set()
        if self.on_delta:
            self.on_delta("after")   # raises UserInterruption inside emit_delta
        return super().query(messages, **kw)


def test_cancel_mid_stream_delivers_buffered_prose_and_stops(tmp_path):
    """ESC mid-stream: prose buffered before the cancel is delivered by the final
    flush; the turn resolves; no 'after' prose is delivered."""
    conn = RecordingConn()
    agent = _build(_StreamingSubmitModel([]), conn)   # placeholder, replaced below
    sid = agent._store.new(cwd=str(tmp_path))
    state = agent._store.get(sid)
    model = _StreamThenCancelModel(state.cancel_flag)
    agent._model_factory = lambda *a, **k: model

    resp = _prompt(agent, sid, "do a thing")
    # cancelled turns resolve (never hang); stop_reason is cancelled/refusal-class.
    assert resp.stop_reason in ("cancelled", "refusal", "end_turn")

    texts = "".join(conn.message_texts())
    assert "before" in texts, "pre-cancel prose was not delivered"
    assert "after" not in texts, "post-cancel prose leaked"
```

> **Implementer note:** confirm the accessor for the per-session state object. The store exposes the `SessionState` (with `cancel_flag`) — check `harness/acp_session.py` for the getter name (`get` / `_sessions[sid]` / similar) and use the real one. If `cancel_flag` is created lazily, set it after the session exists (as the test does, post-`new`). Adjust the `stop_reason` assertion to the actual value the cancel path returns (`_run_agent_turn` returns `cancelled` when `state.cancel_flag.is_set()` at the post-engine check, line ~765; a `UserInterruption` surfacing through the engine may instead resolve via the refusal path — accept the real one and tighten the assertion to it once observed).

- [ ] **Step 2: Run the cancel test**

Run: `.venv/bin/python -m pytest tests/test_acp_agent_streaming.py::test_cancel_mid_stream_delivers_buffered_prose_and_stops -q`
Expected: PASS — `emit_delta("before")` buffers "before"; the cancel flag trips; `emit_delta("after")` raises `UserInterruption`; `run_engine`'s `finally` runs `_flush_prose_sync()` delivering "before"; the outer `finally` cancels the timer. "after" never reached the buffer.

- [ ] **Step 3: Commit**

```bash
git add tests/test_acp_agent_streaming.py
git commit -m "test(stream): cancel mid-stream delivers buffered prose, no leak

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 5: Chat path (`pump`) coalescing + final flush

Apply the same buffer-and-flush to the chat branch's `pump`, which has no boundaries/tool calls — so it needs only buffer + timed flush + a final flush after the answer loop.

**Files:**
- Modify: `harness/acp_agent.py` (the `if cls.task_type == "chat_question":` branch in `prompt`, `pump` + dispatch)
- Test: `tests/test_acp_agent_streaming.py`

**Interfaces:**
- Consumes: existing `handler.answer_stream`, `message_chunk`, `loop`, `session_id`.
- Produces: chat-local `prose`/`prose_lock`/`_flush_prose`/`_flush_prose_sync` mirroring Task 1 (scoped to the chat branch), plus a chat-local flush timer.

- [ ] **Step 1: Write the chat-path coalescing test**

A chat router + a `ChatHandler` whose `answer_stream` yields multiple pieces; assert the full answer is delivered (concatenated) and the turn resolves.

```python
def test_chat_path_coalesces_and_delivers_full_answer(tmp_path, monkeypatch):
    """The chat pump must buffer pieces and deliver the full answer (coalesced),
    and the turn must resolve."""
    conn = RecordingConn()
    agent = build_harness_agent(
        model_factory=lambda *a, **k: None,
        agent_cfg=_agent_cfg(),
        skills_dir=__import__("pathlib").Path("skills"),
        router=_ChatRouter(),
        worker_model_id=None,
    )
    agent._conn = conn
    agent._client_caps = None
    sid = agent._store.new(cwd=str(tmp_path))

    # Force a multi-piece answer stream regardless of model: patch ChatHandler.
    import harness.acp_agent as mod
    pieces = ["Hello", ", ", "world", "."]

    class _FakeHandler:
        def __init__(self, *a, **k): pass
        def answer_stream(self, text, history=None):
            yield from pieces
    monkeypatch.setattr(mod, "ChatHandler", _FakeHandler)

    resp = _prompt_with_timeout(agent, sid, "hi")
    assert resp.stop_reason == "end_turn"
    assert "Hello, world." in "".join(conn.message_texts())
```

- [ ] **Step 2: Run it to confirm it passes on current (un-coalesced) chat code**

Run: `.venv/bin/python -m pytest tests/test_acp_agent_streaming.py::test_chat_path_coalesces_and_delivers_full_answer -q`
Expected: PASS (current per-piece sends already deliver the full answer; this locks the behavior before we change `pump`).

- [ ] **Step 3: Rewrite the chat branch to buffer + timed flush + final flush**

Find the chat branch (currently lines ~488-509), specifically the `pieces`/`pump`/dispatch section:

```python
            pieces: list[str] = []

            def pump() -> None:
                for piece in handler.answer_stream(text, history=transcript):
                    pieces.append(piece)
                    asyncio.run_coroutine_threadsafe(
                        self._conn.session_update(session_id, message_chunk(piece)),
                        loop).result()

            await loop.run_in_executor(None, pump)
            answer = "".join(pieces)
```

Replace with:

```python
            pieces: list[str] = []
            chat_prose = {"buf": ""}
            chat_lock = threading.Lock()

            async def _chat_flush() -> None:
                with chat_lock:
                    text_out, chat_prose["buf"] = chat_prose["buf"], ""
                if text_out:
                    await self._conn.session_update(session_id, message_chunk(text_out))

            def pump() -> None:
                # transcript source (pieces) stays per-piece; delivery is buffered
                # and drained by the timer + the final flush below.
                for piece in handler.answer_stream(text, history=transcript):
                    pieces.append(piece)
                    with chat_lock:
                        chat_prose["buf"] += piece

            async def _chat_flush_loop() -> None:
                try:
                    while True:
                        await asyncio.sleep(0.08)
                        await _chat_flush()
                except asyncio.CancelledError:
                    return

            chat_flush_task = loop.create_task(_chat_flush_loop())
            try:
                await loop.run_in_executor(None, pump)
            finally:
                await _chat_flush()          # deliver the tail
                chat_flush_task.cancel()     # no leftover timer into the next turn
            answer = "".join(pieces)
```

- [ ] **Step 4: Run the chat coalescing test**

Run: `.venv/bin/python -m pytest tests/test_acp_agent_streaming.py::test_chat_path_coalesces_and_delivers_full_answer -q`
Expected: PASS — pump buffers all pieces; the final `await _chat_flush()` delivers "Hello, world." as one chunk.

- [ ] **Step 5: Run the existing chat-path test (no-hang guarantee)**

Run: `.venv/bin/python -m pytest tests/test_acp_agent_streaming.py::test_chat_path_prompt_returns -q`
Expected: PASS — the turn still resolves; the buffer-and-flush adds no blocking.

- [ ] **Step 6: Commit**

```bash
git add harness/acp_agent.py tests/test_acp_agent_streaming.py
git commit -m "perf(stream): coalesce chat-path pump with buffer + timed flush

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 6: Full suite green + final verification

Confirm the whole change is correct and nothing else regressed.

**Files:** none (verification only).

- [ ] **Step 1: Run the full test suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS. If a pre-existing failure appears, confirm it is pre-existing by checking it also fails on `main` (`git stash` is not needed — compare against a clean `main` checkout or the issue history). Do NOT mask a real regression as "pre-existing" without evidence.

- [ ] **Step 2: Confirm the primary checkout is untouched**

```bash
cd ~/Work/Quiubo/harness && git status
```
Expected: clean (all work is in the worktree). Then return to the worktree.

- [ ] **Step 3: Acceptance check — byte-identical output assertion already covered**

Confirm the suite includes: prose-intact-and-ordered (Task 1), prose-before-boundary (Task 2), no-leftover-timer (Task 3), cancel teardown (Task 4), chat full-answer (Task 5). These collectively establish byte-identical delivery + ordering + lifecycle. No separate step needed beyond a green suite.

- [ ] **Step 4: Final commit / ready for PR**

The branch `coalesce-stream-deltas` is ready. Hand off to `superpowers:finishing-a-development-branch` (or `/ship`) to open the PR against `main`. PR body should reference #151 and note the design doc + adversarial-review history.

---

## Self-Review (completed by plan author)

**Spec coverage:**
- "Prose is buffered, not sent per-piece" → Task 1 (emit_delta), Task 5 (pump). ✓
- "Loop-side ~80ms timer" → Task 3 (agent), Task 5 (chat). ✓
- "One flush chokepoint `_flush_prose`" → Task 1. ✓
- "Flush-before-send invariant" → Task 2 (boundary/command/plan). ✓
- "`emit_step_boundary`/`on_command`/`on_plan` unchanged except a flush call" → Task 2. ✓
- "`check_permission`/`client_terminal` untouched" → not modified in any task. ✓
- "`streamed["buf"]` untouched" → Task 1 preserves the per-piece append; Global Constraints. ✓
- "Cancel check stays per-piece" → Task 1 (emit_delta unchanged at top); Task 4 verifies. ✓
- "Lifecycle/teardown: final flush → cancel timer" → Task 1 (final flush) + Task 3 (cancel). ✓
- "Transcript integrity (no second source of truth)" → Task 1 keeps `streamed["buf"]`; Task 5 keeps `pieces`. ✓
- Tests: relax 1:1 (Task 1), boundary order (Task 2), turn-end flush (Task 1 step 7 / Task 4), no-leftover (Task 3), cancel (Task 4), chat (Task 5). ✓

**Placeholder scan:** No TBD/TODO/"handle edge cases". The two implementer notes (n_calls binding in Task 2, session-state accessor in Task 4) point to specific real code to check, not vague hand-waving — they exist because the exact fake-wiring depends on `TracingAgent.n_calls` semantics and the store's accessor name, which the implementer must read live rather than guess. ✓

**Type consistency:** `_flush_prose` (async) / `_flush_prose_sync` (sync wrapper) named consistently across Tasks 1-3. `prose`/`prose_lock` (agent) vs. `chat_prose`/`chat_lock` (chat, Task 5) — deliberately distinct names to avoid shadowing since both live in `prompt`/`_run_agent_turn` scopes. `flush_task` (agent) / `chat_flush_task` (chat) — distinct. ✓
