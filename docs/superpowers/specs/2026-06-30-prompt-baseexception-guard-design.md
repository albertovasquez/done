# Guard `HarnessAgent.prompt()` against escaping BaseException

> **SUPERSEDED — root cause disproven by adversarial review (Opus 4.8), verified
> independently against the installed `acp` library.** Each ACP request runs as
> its own isolated `asyncio.Task` (`acp/task/dispatcher.py:88`); when that task
> finishes, `TaskSupervisor._on_done` (`acp/task/supervisor.py:46-48`) checks
> `if task.cancelled(): return` *before* calling `task.result()` — a cancelled
> or crashed request task is discarded silently and never reaches
> `Connection._run_request`'s `except Exception`, never touches the
> connection, and cannot kill the process by the mechanism this spec describes.
> "Connection closed" means the agent's **OS process** died (stdout hit EOF) —
> something this spec's fix (guarding `prompt()` against in-process
> `BaseException`) cannot address. Do not implement this spec. Follow-up:
> capture the agent subprocess's exit code/stderr on disconnect to find the
> real cause first.

## Problem

The TUI sometimes shows **"agent disconnected — restart to continue (Connection
closed)"** mid-turn. This is the same failure family fixed in PR #93
([[agent-disconnect-baseexception]]): a `BaseException`-only exception (most
plausibly `asyncio.CancelledError`) escapes the agent process's request
handler, which kills the process. The ACP client sees the agent's stdout reach
EOF and reports "Connection closed."

PR #93 fixed this for the **engine** layer: `run_engine()` (the closure passed
to `loop.run_in_executor` inside `_run_agent_turn`,
`harness/acp_agent.py:683-760`) now catches `BaseException` around
`agent.run(text, prior=prior)`, so any control-flow exception thrown while the
engine is reasoning/calling tools is caught on the **worker thread** and
converted to a clean `refusal`.

That fix does not cover everything. `run_engine`'s guard only wraps code
running **inside the executor thread**. The call site itself —

```python
engine = await loop.run_in_executor(None, run_engine)   # acp_agent.py:764
```

— runs on the **event-loop thread**. If this specific `await` is cancelled
(the enclosing asyncio Task receives a `CancelledError` — e.g. the ACP
dispatcher cancels the in-flight request task on client disconnect, or any
upstream cancellation reaches this point), the `CancelledError` is raised at
the `await` itself, completely outside `run_engine`'s try/except. Nothing in
`prompt()` (`harness/acp_agent.py:328-538`) or `_run_agent_turn` catches it —
neither has a top-level guard. The exception propagates up into the installed
`agent-client-protocol` package's `Connection._run_request`
(`acp/connection.py:208-239`), which only catches `Exception`. Since
`asyncio.CancelledError` is `BaseException`-only (not `Exception`, as of
Python 3.8+), it escapes that handler too, kills the request task, and ends
the process.

This was reproduced via a live `--debug` trace: a turn crashed mid tool-call
dispatch (a `ToolCallStart` for an `rg` search with no matching
`action.done`), with no exception captured in `harness.log` on either side —
consistent with a `BaseException` escaping a layer that has no logging on the
way out.

## Goal

No `BaseException` raised anywhere during a `prompt()` call should be able to
reach the installed `acp` library's request dispatcher. Every turn must end in
a `PromptResponse`, never an unhandled exception — matching the contract
`run_engine` already upholds for its own scope, but applied to the whole of
`prompt()`.

Out of scope (explicitly deferred per user's prioritization): improving
diagnostics/stderr capture for this failure class. This pass is about
preventing the disconnect, not about making the next occurrence easier to
debug. (Tracked separately — see "Fix the diagnostics" option not chosen this
round.)

## Design

Wrap the **entire body of `HarnessAgent.prompt()`** in one outer
`try/except BaseException`, mirroring the precedent already proven in PR #93's
`run_engine` fix.

### Why wrap the whole method, not just the `run_in_executor` await

Two narrower alternatives were considered and rejected:

- **Patch only the `run_in_executor` await site.** Narrower, but `prompt()`
  has other unguarded `BaseException`-exposed code: the persona/memory
  `run_in_executor` calls (lines 352-353, 364-365), the classify step's
  `except Exception` (line 373, which still lets a `BaseException` through),
  and the direct `await self._conn.session_update(...)` calls sprinkled
  through the method. A single outer guard closes all of these at once and
  stays correct as the method evolves, instead of requiring every new
  await-point to remember to add its own guard.
- **Patch the vendored `acp` library's `_run_request`.** Not viable —
  `agent-client-protocol` is a pinned pip dependency
  (`pyproject.toml: "agent-client-protocol>=0.10.1,<0.11"`), not vendored
  code. Editing the installed package wouldn't survive a reinstall/upgrade,
  and is out of scope for this repo to maintain a fork of third-party
  protocol-handling code.

A single outer `try/except BaseException` around `prompt()`'s body is the
smallest change that gives a hard guarantee: nothing this method does can ever
escape uncaught.

### Handling cancellation correctly

`prompt()` already has two intentional checks for user-initiated cancellation
via `state.cancel_flag` (lines 762, 765 inside `_run_agent_turn`), returning
`stop_reason="cancelled"`. That is a deliberate, clean outcome (ESC mid-turn)
and must keep working exactly as today — it must NOT be reclassified as a
generic "refusal" by the new outer guard.

The new outer handler in `prompt()` must therefore:

1. Catch `BaseException` around the whole method body (everything currently
   between `async def prompt(...)` and its final `return`).
2. On catch, check `state.cancel_flag.is_set()`:
   - If set → this is (or overlaps with) a user-initiated cancel; return
     `acp.PromptResponse(stop_reason="cancelled")`, consistent with the
     existing cancel path's contract.
   - If not set → genuine unexpected failure; log via `logger.exception` (same
     pattern as `run_engine`'s existing handler and the classify-step
     handler), then return `acp.PromptResponse(stop_reason="refusal")`.
3. The existing inner guards (classify's `except Exception`, `run_engine`'s
   `except BaseException`) are left as-is — they already produce clean,
   specific `refusal` responses for their own cases (e.g. router-unavailable
   gets a tailored user-facing message at line 379-380 before returning). The
   new outer guard is a backstop for everything those don't cover, not a
   replacement for them.

### Logging

Mirror the existing pattern: `logger.exception(...)` with enough context to
identify the session/persona, matching the style already used at line 377 and
in `run_engine`'s handler. This stays a plain `logger.exception` call — no new
diagnostics/stderr-capture work, per the explicit scope decision above.

### Non-goals / explicitly not changing

- Not touching `run_engine`'s existing `except BaseException` — it stays as a
  defense-in-depth inner guard.
- Not modifying the vendored `acp` package.
- Not adding new trace event types or improving `--debug` stderr capture
  (deferred).
- Not changing the classify step's existing `except Exception` handler (it
  already returns a useful tailored message; the new outer guard only adds
  coverage for the `BaseException` case it doesn't catch).

## Testing

Extend the existing regression test pattern from PR #93
(`test_engine_baseexception_yields_refusal_not_disconnect`) with a sibling
test that injects a `BaseException` (e.g. `asyncio.CancelledError`) at a point
in `prompt()` **outside** `run_engine`'s scope — for example by monkeypatching
`loop.run_in_executor` itself to raise, simulating the await-cancellation
case — and asserts:

- `prompt()` returns a `PromptResponse` (does not raise).
- `stop_reason` is `"refusal"` when `cancel_flag` is not set.
- `stop_reason` is `"cancelled"` when `cancel_flag` is set at the time of the
  exception.

Also re-run the full existing test suite (`.venv/bin/python -m pytest tests/
-q`) to confirm no regression to the cancel-flag / refusal paths already
covered.

## Risks

- **Over-broad catch:** wrapping the whole method in `except BaseException`
  could mask a `KeyboardInterrupt`/`SystemExit` intended to stop the process
  (e.g. operator-initiated shutdown). PR #93 accepted this same tradeoff for
  `run_engine` already — this turns an intermittent disconnect into a clean
  refusal, which is the desired behavior for a single request handler in a
  long-running server process. Process-level shutdown should be handled by
  the process supervisor/signal handling, not by an individual request's
  control flow.
