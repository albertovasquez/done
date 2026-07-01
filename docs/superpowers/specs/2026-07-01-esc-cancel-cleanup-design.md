# ESC / Cancel Cleanup — Design Spec

**Date:** 2026-07-01
**Branch:** `esc-cancel-cleanup`
**Status:** Design approved; ready for implementation plan.

## Problem

Pressing ESC during a running turn does not reliably end the turn. The
symptom the user sees: the turn keeps going and the working spinner (and the
status-bar "working" state) stays alive after a cancel.

The between-steps loop gap that used to cause this was fixed in PR #79 — the
engine loop, streaming callback, and bash tool all cooperatively poll
`state.cancel_flag`. But **verified live on 2026-07-01**, three gaps remain
where a turn ignores the flag and keeps the ACP `prompt()` call from
returning:

1. **Chat answers can't be interrupted.** `acp_agent.py` `pump()` (~L526) streams
   `handler.answer_stream` but never checks `cancel_flag`. Only the *agent* path's
   `emit_delta` (~L598) aborts mid-stream. ESC during a `chat_question` answer
   does nothing until it finishes naturally.
2. **The turn preamble is a dead zone.** `classify` (~L407), `persona.resolve`
   (~L388), `memory.resolve` (~L400), and `compose_context` (~L549) run via
   `run_in_executor` with no `cancel_flag` check. ESC during a slow classify or
   persona load does nothing until the executor call returns.
3. **A blocking / stalled LLM call can't be aborted.** `model.query()`
   (`tracing_agent.py:308`) blocks in-thread. The existing interrupt only lands
   *between* streamed tokens (`emit_delta`). A stalled connection, or one that
   has not emitted its first token yet, ignores ESC entirely. This is the
   biggest source of "spinner keeps spinning."

### The spinner symptom is the same root cause

Both working indicators are gated on the ACP `prompt()` call resolving:

- The `#working` `LoadingIndicator` is cleared in `_send_prompt`'s `finally`
  (`app.py:1252`, `_hide_working()`), which fires only when
  `await self._conn.prompt()` returns.
- The status-bar working state (`thinking` / `responding` / `running_tool`,
  `app.py:753-755`) flips off on `TurnEnded`, applied at `app.py:1237/1243`
  — again only after `prompt()` returns.

So "the spinner keeps spinning after cancel" is not a separate bug: the turn
never actually ends, so `prompt()` never returns, so neither indicator clears.
Closing the three gaps above makes `prompt()` return promptly and both
indicators clear on their existing paths.

## Goals

- ESC ends the current turn promptly at **every** stage: preamble, classify,
  chat answer, agent loop, streaming, and a stalled LLM call.
- Both working indicators stop after a cancel (verified by test, not assumed).
- Give the user **immediate** visual feedback that the cancel registered, even
  during the (now short) teardown window.

## Non-goals (explicitly out of scope)

- **`busy_input_mode` / steer / queue** (interrupt-and-redirect). Deferred to a
  possible follow-up spec.
- **Force-closing the litellm/httpx socket** (Hermes's exact mechanism). We go
  through litellm and do not own the socket; we use a watchdog-abandon approach
  instead (see below).
- **`client_terminal` tool path** (`acp_env.py:71`): cancel depends on the ACP
  client; left as-is.
- **Non-bash file tools mid-op** (Read/Write/Edit): fast, low value; left as-is.

## Reference

Modeled on NousResearch/hermes-agent's `interruptible_api_call`
(`agent/chat_completion_helpers.py:155`), adapted: Hermes runs the API call on a
worker thread and force-closes the httpx socket on interrupt. We keep the
worker-thread shape but **abandon** the worker instead of closing its socket,
because litellm owns the socket. See the `hermes-cancellation-architecture`
memory for the full comparison.

## Design

### Core primitive — `harness/interruptible.py` (new module)

One reusable helper, the single new seam. Honors the zero-upstream-edits rule
(new file + call-site wiring only).

```python
def run_interruptible(fn, cancel_flag, *, poll_s=0.1, on_abort=None):
    """Run blocking fn() on a daemon worker thread. Poll cancel_flag from the
    caller thread; if it sets before fn() returns, raise UserInterruption
    immediately and abandon the worker (it's a daemon; its socket is torn down
    when it errors or is GC'd). Returns fn()'s result if it finishes first.
    Re-raises fn()'s exception if fn() raised.

    When cancel_flag is None: call fn() directly on the current thread —
    byte-identical to today for CLI / cron / mock / reviewer paths."""
```

Key properties:

- **Same exit path.** The raised exception is the *existing*
  `minisweagent.exceptions.UserInterruption` (an `InterruptAgentFlow`), so an
  abort lands in the handler the engine loop already has and produces the same
  `{"role": "exit", "extra": {"exit_status": "cancelled", "submission": ""}}`
  shape. No new exit type, no new teardown branch.
- **`cancel_flag is None` ⇒ no behavior change.** Degenerates to `fn()` on the
  current thread. Preserves the "no cancel_flag runs normally" invariant that
  already has a test (`test_no_cancel_flag_runs_normally`).
- **Abandonment cost.** The abandoned worker may run a little longer in the
  background before it notices the torn-down/errored connection. Acceptable: it
  is a daemon thread; it cannot block process exit and holds no locks the next
  turn needs. The `cancel_flag` is cleared at the next `prompt()` entry
  (`acp_agent.py:371`), so a late-finishing abandoned worker cannot corrupt the
  next turn's state (it writes to a per-request local, not shared session state).

### Where the primitive is applied (backend)

| Call site | File / anchor | Change |
|---|---|---|
| Agent-loop model call | `streaming_model.py` `_query` (both branches, L50-91); reached via `tracing_agent.py` `query()` L308 | Run `litellm.completion(...)` through `run_interruptible(..., cancel_flag)`. The existing per-token `emit_delta` abort stays as the fast path (fires first when tokens flow); the watchdog covers the stalled / pre-first-token case. |
| Chat answer | `acp_agent.py` `pump()` ~L526-537 | Poll `cancel_flag` per streamed piece; raise `UserInterruption` when set (mirror `emit_delta`). Covers the streaming chat path with the fast per-piece check; the underlying `answer_stream`'s litellm call is itself wrapped by the model-layer change above. |
| Classify / router | `acp_agent.py` ~L407 | `run_interruptible` around the executor classify call. |
| Preamble | `acp_agent.py` persona ~L388, memory ~L400, compose_context ~L549 | Check `cancel_flag` before/after each executor call; short-circuit to a `cancelled` return when set. |

Threading the flag: `streaming_model` needs access to `cancel_flag`. The
`StreamingLitellmModel` already receives per-run wiring from `acp_agent` (it sets
`model.on_delta = emit_delta` at L770). Add a parallel `model.cancel_flag`
binding set at the same site (bound to `state.cancel_flag`, cleared to `None`
for mock/CLI). `_query` passes it into `run_interruptible`.

### TUI — immediate feedback + verified clearing

- **`action_cancel`** (`app.py`): on ESC, flip the working indicator to a
  **"Cancelling…"** state immediately, before `prompt()` returns. Small,
  local; gives the user instant acknowledgement. (Analogous to Hermes printing
  "⚡ Breaking out of tool loop due to interrupt…".)
- **Clearing stays on the existing paths.** `_send_prompt`'s `finally`
  (`_hide_working()`) and the `TurnEnded` reducer already clear both indicators;
  they now fire promptly because the turn ends. No change to the clearing logic
  itself — the fix is upstream (the turn actually ending).

## Data / control flow

```
ESC → action_cancel → (show "Cancelling…") → conn.cancel() over ACP
    → HarnessAgent.cancel sets state.cancel_flag (threading.Event)

turn worker thread (run_in_executor):
    preamble check  → cancel_flag set? → cancelled return
    classify        → run_interruptible(cancel_flag) → raises UserInterruption
    chat pump()     → per-piece cancel_flag check → raises UserInterruption
    agent loop      → top-of-loop check (existing) → break with cancelled exit
      model.query   → run_interruptible(cancel_flag) → raises on stall
        emit_delta  → per-token check (existing) → raises on next token
    → prompt() returns promptly
        → _send_prompt finally: _hide_working(), TurnEnded → indicators clear
```

## Error handling

- `run_interruptible` re-raises the worker's own exception unchanged when `fn()`
  fails before a cancel (auth errors, transport errors, litellm exceptions keep
  their existing handling in `_query` / upstream `query()`).
- A cancel that fires while the worker is mid-flight raises `UserInterruption`,
  never a transport/network error — so a forced cancel is not misreported as an
  agent disconnect. (Aligns with the existing `except BaseException` refusal
  handler in `acp_agent.py` ~L787 and the `agent-disconnect-baseexception` fix.)
- Preamble short-circuits return the existing cancelled result shape
  (`{"stop_reason": "cancelled", "exit_status": "cancelled", "assistant": ""}`,
  as at `acp_agent.py:805-809`).

## Testing (deterministic, mock-model — no live proxy)

New / extended tests, all runnable via `.venv/bin/python -m pytest tests/ -q`:

1. **`run_interruptible` unit tests** (`tests/test_interruptible.py`, new):
   - returns `fn()`'s result when it finishes before cancel;
   - raises `UserInterruption` when `cancel_flag` sets while `fn()` blocks
     (use an `Event`-gated fake `fn`);
   - re-raises `fn()`'s own exception;
   - `cancel_flag=None` runs `fn()` on the current thread (no worker), result
     identical.
2. **Chat path interruptible** (`tests/`): a mock chat handler whose
   `answer_stream` blocks; set `cancel_flag`; assert the turn ends `cancelled`
   and does not stream further pieces.
3. **Preamble interruptible**: set `cancel_flag` before classify; assert the
   turn returns `cancelled` without classifying.
4. **Stalled model call**: a mock model whose `query` blocks on an `Event`; set
   `cancel_flag`; assert `UserInterruption` → `cancelled` exit and the loop does
   not proceed to a second call.
5. **Indicators clear after cancel** (TUI, headless Textual): drive a
   mock-model turn, press ESC, assert (a) the indicator shows "Cancelling…"
   immediately, and (b) after the turn resolves, `#working` is gone and the
   status-bar working state is cleared.
6. **Regression**: existing `test_cancel_flag_stops_loop_between_steps` and
   `test_no_cancel_flag_runs_normally` still pass unchanged.

## Files touched (estimate)

- `harness/interruptible.py` — new.
- `harness/streaming_model.py` — wrap both `_query` branches; accept `cancel_flag`.
- `harness/acp_agent.py` — bind `model.cancel_flag`; wrap classify; poll in
  `pump()`; preamble checks.
- `harness/tui/app.py` — "Cancelling…" immediate feedback in `action_cancel`.
- `tests/…` — new + extended per above.

Explicitly untouched: upstream `minisweagent/*`, `acp_env.py` `client_terminal`
path, non-bash file tools.

## Related memory

- `esc-interrupt-rootcause` — our checkpoints + the verified remaining gaps.
- `hermes-cancellation-architecture` — the reference model and what we adapt.
- `agent-disconnect-baseexception` — why a forced cancel must not read as a
  disconnect.
- `two-process-boundary-rationale` — why the TUI↔agent split shapes the cancel
  path.
