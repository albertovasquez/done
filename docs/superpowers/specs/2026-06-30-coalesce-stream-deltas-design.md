# Coalesce streamed prose deltas before crossing the RPC boundary

**Issue:** #151 (`[performance][high]` Per-delta RPC `.result()` blocks the worker
thread on every streamed token — no emit coalescing)

**Date:** 2026-06-30

**Status:** Design approved (brainstorming → spec). Reviewed adversarially by Opus
4.6, which caught an ordering bug in the original single-queue design; this spec
reflects the revised, simpler approach.

---

## Problem

The TUI and the agent run in **two separate OS processes** connected by ACP/RPC.
This boundary is deliberate — it buys crash isolation (the agent can OOM or crash
without taking the UI down) and a responsive TUI loop (ESC/cancel/scroll never
contend with a blocking litellm call). The standing decision is to **keep the
boundary and harden it**, not collapse to in-process.

On the streaming hot path, every individual litellm stream chunk triggers a
**synchronous, blocking RPC round-trip** from the worker thread (the thread
pulling tokens from litellm) to the asyncio event loop (which owns the ACP
connection):

```python
# harness/acp_agent.py — emit_delta(), inside _run_agent_turn (agent path)
def emit_delta(piece: str) -> None:
    if state.cancel_flag.is_set():
        raise UserInterruption({...})
    n = getattr(agent_ref["agent"], "n_calls", 0)
    if n != last_step["n"]:
        last_step["n"] = n
        emit_step_boundary()
    streamed["buf"] += piece
    asyncio.run_coroutine_threadsafe(
        self._conn.session_update(session_id, message_chunk(piece)), loop).result()
```

`.result()` blocks the worker thread until the loop has accepted and sent that one
chunk. So the worker alternates "pull one chunk from litellm" with "block on a
full cross-thread RPC round-trip" — once per chunk. The same blocking idiom also
sits on the **chat path** (`pump()`, a flat loop over `handler.answer_stream(...)`).

litellm calls `on_delta` **once per raw stream chunk** (`streaming_model.py`:
`for chunk in stream: ... self.on_delta(piece)`), so this is genuinely per-token
serialization between the model and the wire.

### Why this is wasted work

The TUI side **already coalesces rendering** to a 12Hz timer. `on_session_update`
just appends incoming text to `_stream_buf` and marks it dirty; the actual paint
(`md.update`) happens only on the `set_interval(1/12, self._flush_stream)` tick
(`app.py`), plus two specific sync-flush edge cases (stream open, stream
closed-then-reopened). Delivering `session_update` calls faster than ~80ms buys
**nothing visible** — the bytes just sit in `_stream_buf` until the next tick.

So we are paying for a per-token cross-process round-trip to feed a consumer that
repaints at most 12 times a second.

---

## Goal

Stop blocking the worker thread once per token. Batch prose deltas worker-side to
~80ms windows (matching the TUI's 12Hz render rate) before sending them across the
RPC boundary. Keep the two-process boundary, the crash isolation, and **byte-
identical rendered output**.

Explicit non-goal: this is **not** "go in-process for speed." We keep the
boundary; we just stop paying for it once per token, synchronously.

---

## Approach (revised after review)

> **Why not a queue?** The original design routed *all* emits — prose, step
> boundaries, tool start/done, plan updates — through a single per-turn
> `asyncio.Queue` drained by a consumer coroutine, with prose time-buffered and
> non-prose pushed immediately. Adversarial review found this **reintroduces the
> #81/#138 ordering bug**: a tool-call-start firing while prose sits unflushed in
> the staging buffer reaches the wire *before* the prose that preceded it, and the
> TUI then misattributes that prose to the next step's widget. It also added a new
> per-turn lifecycle object (queue + consumer coroutine + timer) — exactly the
> teardown surface this codebase has bled from (#81/#99/#138). The revised design
> below drops the queue entirely.

**Only the two per-token hot paths change** — `emit_delta` (agent) and `pump`
(chat). Everything rare stays exactly as it is today.

### What changes

1. **Prose is buffered, not sent per-piece.** `emit_delta`/`pump` append the piece
   to a small per-turn prose buffer instead of calling
   `run_coroutine_threadsafe(...).result()` per piece.

2. **A loop-side timer flushes the buffer every ~80ms.** Scheduled at turn start,
   it sends one `session_update(message_chunk(<accumulated>))` per tick — and only
   when the buffer is non-empty (an idle tick sends nothing).

3. **One flush chokepoint, `_flush_prose()`.** It atomically swaps the prose
   buffer for `""` under a lock and, if non-empty, sends the accumulated text via
   `session_update`. This is the single function that puts prose on the wire.

4. **Flush-before-send is the ordering invariant.** Everything that must stay
   ordered relative to prose — `emit_step_boundary`, `on_command`, `on_plan`, and
   turn-end teardown — calls `_flush_prose()` **synchronously, first**, then sends
   its own event exactly as today. Because the rare events still block, and prose
   is flushed immediately ahead of them, call order is preserved on the wire by
   construction. This replaces the queue's (false) "ordering for free" with a real,
   single-chokepoint guarantee.

### What stays exactly as today

- **`emit_step_boundary`, `on_command`, `on_plan`** keep their blocking
  `run_coroutine_threadsafe(...).result()` sends. They fire **per-tool / per-plan /
  per-step, not per-token** — rare enough that blocking is free, and blocking is
  what makes their ordering correct. The *only* addition is a `_flush_prose()` call
  at the top of each.
- **`check_permission` and `client_terminal`** stay fully synchronous and
  untouched. They need a real round-trip (allow/deny verdict, command output) and
  are not on the per-token path. Out of scope.
- **`streamed["buf"]`** (the failure-case transcript) is **untouched**. It is still
  appended synchronously, per-piece, inside `emit_delta`, exactly where it is today
  (independent of the new delivery buffer). The coalescer is **delivery-only** and
  never becomes a second source of truth. (See "Transcript integrity" below — this
  is load-bearing.)
- **The cancel check** stays per-piece at the top of `emit_delta`. Cancel latency
  stays bounded by litellm's chunk-yield rate, *not* the 80ms flush cadence,
  exactly as today.

---

## Components

### `_flush_prose()` — the delivery chokepoint (new, per turn)

Defined inside `_run_agent_turn` (and an equivalent inside the chat branch), closing
over the same `loop` / `session_id` / `self._conn` the existing callbacks use.

Responsibilities:
- Acquire the prose lock, swap `prose["buf"]` → `""`, release.
- If the swapped-out text is non-empty, `await self._conn.session_update(session_id,
  message_chunk(text))` on the loop.
- Idempotent and safe to call when the buffer is empty (no-op send).

Called from: the 80ms timer, `emit_step_boundary`, `on_command`, `on_plan`, and the
turn-end teardown.

Threading note: `_flush_prose` mutates only the plain prose buffer (guarded by a
`threading.Lock`) and performs the `session_update` **on the loop**. There are two
call shapes, by caller thread:

- **`_flush_prose()`** — the async, loop-side form, awaited directly by the 80ms
  timer task (which runs on the loop).
- **`_flush_prose_sync()`** — the worker-thread-callable form, used by
  `emit_step_boundary` / `on_command` / `on_plan` / the turn-end teardown (all on
  the worker thread). It marshals the flush to the loop and blocks via
  `run_coroutine_threadsafe(...).result()` — the exact idiom those callbacks
  already use today. Both forms drain the same lock-guarded buffer; there is no new
  cross-thread primitive beyond that lock.

### The prose buffer + lock (new, per turn)

- `prose = {"buf": ""}` — accumulated, not-yet-sent prose for the current turn.
- A `threading.Lock` guarding append (worker thread) vs. swap-and-clear (flush).
  `threading` must be added to the imports in `acp_agent.py` (currently absent).

### The flush timer (new, per turn)

- Scheduled on the loop at turn start (e.g. an asyncio task that loops
  `await asyncio.sleep(0.08); await _flush_prose()`, or a `call_later` chain).
- **Explicitly cancelled at turn end** in the existing `finally` (see Lifecycle).
- Created on the **loop thread**, never lazily from the worker thread.

### `emit_delta` (agent path — modified)

```
def emit_delta(piece):
    if state.cancel_flag.is_set():        # UNCHANGED — per-piece cancel
        raise UserInterruption({...})
    n = agent_ref["agent"].n_calls        # UNCHANGED — boundary detection
    if n != last_step["n"]:
        last_step["n"] = n
        emit_step_boundary()              # now flushes prose first (see below)
    streamed["buf"] += piece              # UNCHANGED — failure-case transcript
    with prose_lock:
        prose["buf"] += piece             # NEW — buffer instead of blocking send
```

Note the boundary subtlety the review flagged by name: `emit_step_boundary` is
called from *inside* `emit_delta`, before the current piece is appended to
`prose["buf"]`. `emit_step_boundary` flushes the prose buffer (which holds the
*previous* step's tail) first, then emits the boundary, then control returns and the
*new* step's first piece is appended. So the boundary correctly lands between the
old step's prose and the new step's prose. This ordering must be preserved exactly.

### `emit_step_boundary` / `on_command` / `on_plan` (modified — one line each)

```
def emit_step_boundary():
    _flush_prose_sync()                   # NEW — drain pending prose first
    asyncio.run_coroutine_threadsafe(... stream_reset ...).result()   # UNCHANGED
```

(`_flush_prose_sync` is the worker-thread-callable form that marshals the flush to
the loop and blocks — same idiom these callbacks already use.)

### `pump` (chat path — modified)

```
for piece in handler.answer_stream(text, history=transcript):
    pieces.append(piece)                  # UNCHANGED — full answer for transcript
    with prose_lock:
        prose["buf"] += piece             # NEW — buffer instead of blocking send
# after the loop: final flush (see Lifecycle) — drains the tail
```

The chat path has no boundaries, tool calls, or plan updates, so it needs only
buffer + timed flush + a final flush after the loop. It shares the same small
`_flush_prose` helper shape; it does **not** need the agent path's boundary logic.
We deliberately share the *small* piece (buffer + timed flush), not a heavier
`StreamCoalescer` class — the chat path is far simpler and forcing both through one
rich abstraction is unjustified.

---

## Lifecycle & teardown (load-bearing)

The single most important correctness property after ordering: **no prose is
dropped at turn end, and no timer/flush from turn N can fire into turn N+1.**

Teardown hooks into the **existing `finally`** in `run_engine` (`acp_agent.py`
~L740-743), which already runs on all three exit paths (success, failure via the
`except BaseException`, and cancellation). In that `finally`, in order:

1. **Final synchronous prose flush** — `_flush_prose_sync()` once more, so the last
   <80ms of prose (which may never have hit a timer tick) is delivered.
2. **Cancel the flush timer** — stop the per-turn timer/task so it cannot fire
   again. This is the guard against the "late delta after close" class (#99): a
   leftover timer firing into turn N+1 (same `session_id`) is exactly that bug.
3. **Clear `model.on_delta`** — already present today; keep it.

The chat path mirrors this: final flush after the `for piece in ...` loop, and the
chat branch's timer is cancelled in its own `finally`/cleanup.

Ordering of steps 1→2 matters: flush *then* cancel, so the final flush is never
skipped by an early timer cancel.

### Cancel (ESC) path

`emit_delta` still raises `UserInterruption` per-piece (unchanged). That unwinds the
worker thread into `run_engine`'s `finally`, which runs the same teardown: final
flush (delivering whatever prose was buffered up to the cancel point) → cancel timer
→ clear `on_delta`. No special cancel-only path; the `finally` covers it.

---

## Transcript integrity (load-bearing)

`streamed["buf"]` is the **failure-case transcript source**. On the refusal path,
`assistant = engine.get("streamed", "") or ...` (`acp_agent.py:529`) — that text
*becomes the stored assistant transcript*.

**Invariant:** `streamed["buf"]` accumulation is **unchanged** — appended
synchronously, per-piece, inside `emit_delta`, on the worker thread, in lockstep
with the model producing tokens. It is **not** reconstructed from the new prose
delivery buffer. The two buffers serve different masters:

- `streamed["buf"]` → transcript (correctness; must reflect every token the model
  produced, regardless of whether it was flushed to the wire).
- `prose["buf"]` → delivery (UX; a transient ~80ms staging area, drained to the
  wire and emptied).

If they were unified, a turn that fails/cancels with unflushed prose would store a
truncated transcript. They must stay separate. (The chat path's `pieces` list plays
the same role and likewise stays as-is.)

---

## Testing

### Existing test that must change (expected, signed-off)

- `test_agent_path_streams_deltas_as_message_chunks` currently asserts a **1:1
  delta→message-chunk** mapping. With coalescing, multiple deltas legitimately merge
  into fewer, larger `session_update` calls. Relax to assert: **deltas arrive in
  order, the concatenation of streamed text equals the joined deltas, and exactly
  one boundary**. Drop the 1:1 assumption. This is a deliberate, approved behavior
  change — not a regression.

### New regression tests (one per failure mode the review surfaced)

1. **Boundary-after-unflushed-prose ordering** (regression for the bug that killed
   the queue design): emit prose, then fire a tool-call start / step boundary within
   the 80ms window, assert the **wire order is prose-then-boundary/tool**, never the
   reverse. This is the test that proves the flush-before-send chokepoint works.
2. **Turn-end final flush**: prose shorter than one flush window, then the turn ends
   → assert the tail **is delivered on the wire AND present in `streamed`/transcript**.
3. **No leftover timer across turns** (regression for the #99 class): turn N ends
   with <80ms unflushed prose; turn N+1 begins → assert nothing from N's timer lands
   in N+1.
4. **Cancel mid-stream**: ESC with prose buffered → assert clean teardown, the
   buffered-up-to-cancel prose is flushed (or deterministically handled), and **no
   post-cancel delivery**.
5. **Chat-path flush + final flush**: the chat path is currently under-covered for
   this change — add a test that `pump` buffers and that the final flush after the
   loop delivers the tail.

### Acceptance criteria

- **Byte-identical rendered output** before/after on the same prompt (the joined
  streamed text equals what the un-coalesced path produced).
- Reduced cross-process frame count for prose on a representative long turn (fewer
  `session_update(message_chunk)` calls than deltas).
- Reduced worker-thread time blocked on RPC (the `.result()` per-prose-delta cost is
  gone).
- All existing streaming/ordering tests green (with the one relaxation above).

### Profiling note (from the issue)

The issue suggests profiling the per-delta `.result()` cost before implementing.
We are **skipping the profiling gate** — the per-token blocking cross-process
round-trip is a clear structural cost on the hot path, and the fix is low-risk and
self-contained. We proceed straight to implementation. (A before/after frame-count
and worker-thread-blocked-time number is still captured as part of the acceptance
check below — we just don't gate the work on measuring it first.)

---

## Files touched

- `harness/acp_agent.py` — the whole change lives here:
  - add `import threading`
  - `_run_agent_turn`: new prose buffer + lock + `_flush_prose`/`_flush_prose_sync`
    + per-turn flush timer; modify `emit_delta`, `emit_step_boundary`, `on_command`,
    `on_plan`; extend the `run_engine` `finally` teardown.
  - chat branch (`pump`): buffer + timed flush + final flush.
- `tests/test_acp_agent_streaming.py` — relax the 1:1 test; add the new regression
  tests. Possibly a small new `tests/test_acp_stream_coalesce.py` for the
  ordering/lifecycle cases.

No TUI-side changes — `on_session_update`/`_flush_stream` already coalesce and are
order-sensitive on the wire, which this design preserves.

---

## Out of scope (explicitly)

- Permission and terminal round-trips (`check_permission`, `client_terminal`) — stay
  synchronous; correct as-is.
- The chat-vs-agent path unification into one rich `StreamCoalescer` class — we share
  only the minimal buffer+flush shape.
- The other per-turn perf issues (#105 history resend, #139 prompt caching, #110
  router fast-path, #148/#149/#150/#153) — separate work.
