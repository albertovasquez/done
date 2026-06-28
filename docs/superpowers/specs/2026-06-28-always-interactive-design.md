# Always-Interactive TUI — design

**Date:** 2026-06-28
**Branch:** `repro-input-freeze`
**Status:** approved (brainstorming), pending spec review

## Problem

A user ran `dn --debug` and reported the app "hung for a minute or two" — they
could not type or click the composer before the response continued. Investigation
of the live trace (`harness/runs/20260628-151948-90163/trace.jsonl`) and a Pilot
reproduction established:

- The slow turn was an **agent-path** turn (`code_explain` over a 12.9KB file),
  **49s end to end**: ~4.5s uncached router classify, ~4.3s first LLM call,
  a file read, streamed analysis, ~5.1s finalize. No single 60–120s stall — it
  is several sequential LLM round-trips stacked in one turn.
- The first **4.5s** is a **pre-stream window**: a generic, static
  `LoadingIndicator` is shown while the router classifies, with **nothing else
  changing** → it reads as frozen. (Audit issue #110: uncached router/turn.)
- The response then arrives as ~1283 chunks; `_stream_message` re-parses the
  whole growing markdown buffer **per chunk** → O(n²) render pressure. (Audit
  issue #109.)

**Key finding (refutes the obvious theory):** a Pilot repro
(`tests/test_tui_input_freeze_repro.py`) drives the REAL `HarnessTui` against the
REAL fake-agent through the pre-stream gap and proves the composer is **not
logically frozen** — it is enabled, a real mouse click focuses it, and keystrokes
land. The "can't type" symptom is therefore **perceived** non-interactivity:
no visible liveness during the blank window + repaint lag under burst render —
not a dead event loop.

## Goal (the invariant)

**The app must never present a state in which the user cannot interact with it.**
Concretely, across every turn phase (pre-stream gap, mid-burst render, slow tool,
hung agent, cancel):

1. **Composer always typeable** — never disabled; focus/click always works;
   keystrokes never silently dropped.
2. **Always cancelable** — ESC interrupts an in-flight turn from the composer,
   regardless of input text or rail focus.
3. **Render never blocks input** — streaming a large response must not starve the
   event loop / repaint.
4. **Visible liveness signal** — an always-moving, phase-labeled indicator proves
   the app is alive so a slow phase never *reads* as frozen.

**Interaction model (unchanged):** type + queue. Typing during a turn enqueues
the next message (FIFO, drained on turn end); we do **not** run two prompts
concurrently on one session and we do **not** interrupt-and-replace. We only make
the queue affordance obvious.

**Out of scope:** caching the router classification (the larger #110 fix) — the
~4.5s stays; we make it stop *looking* dead, not make it faster. Interrupt-and-
replace input. Any agent-side / ACP wire change.

## Architecture

The guarantee is enforced as a **tested invariant**, not a feature. A single
Pilot invariant test asserts interactivity across all phases; the four fixes
below exist to keep it green.

### Component 1 — Invariant test (load-bearing)

`tests/test_always_interactive.py`. Drives the real `HarnessTui` via Pilot
against the real fake-agent. For each phase it asserts the same predicate
(`composer not disabled` AND `focusable` AND `a keystroke lands` AND, when a turn
is active, `ESC reaches action_cancel`):

- **pre-stream gap** — fake `SLOW` prompt (chip emitted, then a silent sleep).
- **mid-burst render** — fake prompt that emits many chunks rapidly; probe input
  while chunks are still arriving.
- **cancel** — during an active turn, ESC from the composer triggers `cancel()`.

The fake-agent already grew a `SLOW` path (silent pre-stream gap) for the repro;
a `BURST`-style path already exists for rapid chunks. No production code under
test is mocked — only the agent subprocess shape.

### Component 2 — Render coalescing (#109)

`harness/tui/app.py::_stream_message` + a new `_flush_stream` timer callback.

- On each chunk: append to `self._stream_buf` (cheap); set a `_stream_dirty`
  flag; ensure a throttled flusher is scheduled at a fixed **12 Hz (~80ms)**
  via Textual `set_interval` (single concrete rate; tunable in one place).
- The flusher, when `_stream_dirty`, calls `md.update(self._stream_buf)` once and
  clears the flag — coalescing many chunks per frame into one render.
- The **final** chunk / stream close forces a last flush so no text is lost.
- Mount-timing guarantee preserved: first render still routed so it lands after
  the widget mounts (today via `call_after_refresh`; the flusher inherits the
  same "update is a no-op until mounted, re-render on next tick" property).

Boundary: only *when* the buffer is painted changes; *what* is shown is identical.
No change to the reducer or `on_session_update`. Widget-identity / late-delta /
new-step routing (the `_boundary_after` logic) is untouched.

### Component 3 — ESC always cancels (always-cancelable)

`harness/tui/app.py::on_key` precedence + `action_cancel`.

- When `self._turn_active`, ESC routes to `action_cancel` **first**, before the
  clear-input-text (app.py:614) and rail-close (app.py:632) handlers consume it.
- `action_cancel` already emits `tx.cancel` and calls `conn.cancel()` (sets
  `cancel_flag`, checked by the engine every step — acp_agent.py:550). Add a
  visible `— canceling… —` muted line so the action has immediate feedback.
- When no turn is active, ESC keeps current behavior (clear text / close rail).

Boundary: a precedence tweak in `on_key` + one feedback line. The cancel wire
itself is unchanged.

### Component 4 — Phase-labeled liveness (#110 window, display only)

`harness/tui/app.py` working-indicator + the state reducer.

- Drive a phase label on the existing working indicator / `ActivityRegion` from
  events the TUI already receives:
  - `tx.prompt` sent → **"Classifying…"**
  - `task.classified` arrives → **"Reading `<file>`…"** when the first action is
    a read, else **"Working…"**
  - first `AgentMessageChunk` → existing **"Responding"** (unchanged).
- The spinner animation supplies the always-moving motion.
- Fallback: if a phase event is absent (e.g. mock agent that never classifies),
  the generic spinner label is used — no regression.

Boundary: event → label mapping in the existing path; no new agent-side wire,
display-only.

### Component 5 — Queue-visibility (interaction model)

`harness/tui/app.py` composer placeholder.

- While `_turn_active`, set the composer placeholder to
  **"Type to queue your next message…"**; restore the default when idle.
- FIFO queue behavior (app.py:516-519) is unchanged. One prompt per session;
  drained on turn end. No concurrency change.

## Data flow

```
user prompt ─▶ _submit_text ─▶ _turn_active=True
                              ─▶ placeholder="Type to queue…"   (C5)
                              ─▶ _send_prompt: _show_working("Classifying…")  (C4)
                              ─▶ await conn.prompt(...)          (loop stays live)

agent: task.classified ─▶ label "Reading <file>…" / "Working…"  (C4)
agent: AgentMessageChunk ×N ─▶ _stream_buf += text; mark dirty   (C2)
                            ◀─ _flush_stream @ ~12Hz: md.update(buf)
turn end ─▶ force final flush (C2); _turn_active=False;
            placeholder restored (C5); _hide_working; drain queue

ESC during turn ─▶ on_key: _turn_active ⇒ action_cancel first   (C3)
               ─▶ tx.cancel + conn.cancel() ⇒ cancel_flag set
               ─▶ "— canceling… —"
```

## Error handling

- **Missing phase event** (mock/old agent): liveness falls back to generic
  spinner; no crash, no stuck label.
- **Cancel mid-stream**: final flush still fires on turn end (ok or failed),
  so a partially-streamed answer is not lost; `TurnEnded(ok=False)` path
  unchanged.
- **Flusher after teardown**: flush is a guarded no-op when `_streaming_md is
  None` (reload/clear nulls it); the interval is cleared on stream close /
  reset so no timer leaks across turns.
- **Late deltas** (the #99 family): unaffected — coalescing changes paint
  cadence only; the terminal-state guard in the reducer still drops post-`DONE`
  item advances.

## Testing

- **Invariant test** (Component 1) — the durable guarantee; must stay green.
- **Render coalescing**: a unit/Pilot test that N rapid chunks produce a bounded
  number of `md.update` calls (assert coalescing) and the final buffer equals the
  concatenation of all chunks (assert no loss).
- **ESC cancel**: Pilot test — during an active turn, ESC from the composer (with
  and without text in the box) results in a `cancel()` call / `tx.cancel` trace.
- **Liveness label**: reducer test — `tx.prompt`→"Classifying…",
  classified-with-read→"Reading …", first chunk→"Responding".
- **Queue visibility**: Pilot test — placeholder is the queue prompt while a turn
  is active, default when idle; queued-message FIFO drain unchanged.
- Full suite green from the worktree root:
  `.venv/bin/python -m pytest tests/ -q` (run as
  `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/ -q`
  from the worktree so conftest resolves this worktree's src).

## Risks

- **Coalescing regressions** are the highest-risk change (hot stream path). The
  no-loss + mount-timing + late-delta tests are the guard. The `_boundary_after`
  widget-routing logic is explicitly out of scope and untouched.
- **ESC precedence** could shadow clear-text/close-rail if the `_turn_active`
  guard is wrong; tests cover both turn-active and idle ESC.
- Pilot tests exercise event-loop logic, **not** real-terminal repaint; they
  cannot prove the perceived paint-lag is gone. The coalescing test bounds the
  render count as the objective proxy.
