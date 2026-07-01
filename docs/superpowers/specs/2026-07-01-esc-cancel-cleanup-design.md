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

> **Caveman-review correction (2026-07-01):** the recurring error in the first
> draft was assuming every LLM call goes through the model object. It does not —
> the **chat** and **classify** paths each make their *own* `litellm.completion`
> call with their *own* exception handling. The table and notes below are
> corrected to wrap each real call site, and to respect each site's existing
> `except` so a cancel is never misreported.

| Call site | File / anchor | Change |
|---|---|---|
| Agent-loop model call | `streaming_model.py` `_query` (both branches, L50-91); reached via `tracing_agent.py` `query()` L308 | Run `litellm.completion(...)` through `run_interruptible(..., cancel_flag)`. The existing per-token `emit_delta` abort stays as the fast path (fires first when tokens flow); the watchdog covers the stalled / pre-first-token case. |
| Chat answer | `chat_handler.py` `answer_stream` L201 (the **real** litellm call) + `acp_agent.py` `pump()` ~L526-537 | **(Finding 1+2)** `answer_stream` calls `litellm.completion(stream=True)` DIRECTLY — NOT via `StreamingLitellmModel`. Wrap that `completion()` call in `run_interruptible(cancel_flag)` so a *stalled* chat call (blocked before the first chunk) is killable. Keep a per-piece `cancel_flag` check in the `for chunk` loop (and/or `pump()`) as the fast path once pieces flow. `answer_stream` takes an optional `cancel_flag` param (default `None` ⇒ unchanged for CLI/mock). |
| Classify / router | `acp_agent.py` ~L406-417 | **(Finding 3)** classify sits inside `except Exception → return refusal("router unavailable")`. `UserInterruption` IS an `Exception`, so raising inside that try would be swallowed as a fake router failure. Do a **check-and-return BEFORE the try** (`if cancel_flag.is_set(): return cancelled`), then wrap the executor classify call in `run_interruptible`, and make the `except` **re-raise `InterruptAgentFlow`** before its refusal branch. |
| Preamble | `acp_agent.py` persona ~L388, memory ~L400, compose_context ~L549 | **(Finding 5)** per-site, explicit: add `if state.cancel_flag.is_set(): return _cancelled()` immediately BEFORE persona.resolve, memory.resolve, classify, and compose_context. These are cheap cooperative gates (the executor calls themselves are short); no watchdog needed here. `_cancelled()` returns the existing cancelled shape. |

**Threading the flag (Finding 4).** Not every path touches the model object, so
"bind `model.cancel_flag`" alone is insufficient:

- **Model path:** bind `model.cancel_flag = state.cancel_flag` at the same site
  that sets `model.on_delta` (`acp_agent.py:770`), guarded by
  `hasattr(model, "cancel_flag")` so the mock model is untouched. `_query`
  reads `self.cancel_flag` (default `None`) and passes it to `run_interruptible`.
  `StreamingLitellmModel.__init__` gains `cancel_flag=None`.
- **Chat path:** pass `state.cancel_flag` directly into
  `handler.answer_stream(text, history=..., cancel_flag=state.cancel_flag)`.
- **Classify path:** pass `state.cancel_flag` directly into the
  `run_interruptible` wrapper in `prompt()`; the router itself stays
  flag-agnostic.

In every case `cancel_flag=None` (CLI / cron / mock / reviewer) ⇒ the call runs
inline, byte-identical to today.

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
    preamble gates  → cancel_flag set before each? → cancelled return
    classify        → pre-try check + run_interruptible + except re-raises → cancelled
    chat answer_stream → run_interruptible(completion) + per-piece check → UserInterruption
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
- **classify must not misreport a cancel as a router failure (Finding 3).** Its
  `except Exception` refusal branch re-raises `InterruptAgentFlow` first, so a
  `UserInterruption` from the wrapped classify call becomes a clean cancelled
  turn, never a "router unavailable" message.
- **chat: a stalled call is killed at `completion()` (Finding 1+2)**, not only
  between pieces — the watchdog wraps the blocking `litellm.completion` call in
  `answer_stream`, so a chat answer that never produces a first token is still
  interruptible.

## Testing (deterministic, mock-model — no live proxy)

New / extended tests, all runnable via `.venv/bin/python -m pytest tests/ -q`:

1. **`run_interruptible` unit tests** (`tests/test_interruptible.py`, new):
   - returns `fn()`'s result when it finishes before cancel;
   - raises `UserInterruption` when `cancel_flag` sets while `fn()` blocks
     (use an `Event`-gated fake `fn`);
   - re-raises `fn()`'s own exception;
   - `cancel_flag=None` runs `fn()` on the current thread (no worker), result
     identical.
2. **Chat path interruptible — stalled call (Finding 1+2)**: patch
   `litellm.completion` (used by `answer_stream`) to block on an `Event` before
   yielding any chunk; set `cancel_flag`; assert `run_interruptible` raises and
   the turn ends `cancelled` — proving the pre-first-token case is covered, not
   just the between-pieces case.
3. **Preamble interruptible**: set `cancel_flag` before classify; assert the
   turn returns `cancelled` without classifying.
3b. **Classify cancel not misreported (Finding 3)**: set `cancel_flag` so the
   wrapped classify raises `UserInterruption`; assert the turn resolves as
   `cancelled`, NOT as a `refusal` / "router unavailable" message.
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
- `harness/streaming_model.py` — wrap both `_query` branches; `__init__` gains
  `cancel_flag=None`.
- `harness/chat_handler.py` — `answer_stream` gains `cancel_flag=None`; wrap its
  `litellm.completion` in `run_interruptible`; per-piece check in the loop.
- `harness/acp_agent.py` — bind `model.cancel_flag`; pre-try classify check +
  wrap + re-raise `InterruptAgentFlow` in the `except`; pass `cancel_flag` into
  `answer_stream`; per-site preamble gates.
- `harness/tui/app.py` — "Cancelling…" immediate feedback in `action_cancel`.
- `tests/…` — new + extended per above (incl. stalled-chat and
  classify-not-misreported cases).

Explicitly untouched: upstream `minisweagent/*`, `acp_env.py` `client_terminal`
path, non-bash file tools.

## Related memory

- `esc-interrupt-rootcause` — our checkpoints + the verified remaining gaps.
- `hermes-cancellation-architecture` — the reference model and what we adapt.
- `agent-disconnect-baseexception` — why a forced cancel must not read as a
  disconnect.
- `two-process-boundary-rationale` — why the TUI↔agent split shapes the cancel
  path.
