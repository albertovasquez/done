# Phase 1 — The AgentRunner abstraction

**Date:** 2026-06-25
**Status:** Approved design, pre-implementation
**Scope:** Phase 1 only. Extract a client-facing `AgentRunner` so clients depend
on the runner, not on `minisweagent`. Do **not** build the HTTP/SSE protocol
(Phase 2), the knowledge/skills layer (Phase 3), the TUI (Phase 5), or workspace
isolation (a later supporting primitive). Do **not** edit `upstream/` or the
reviewed Phase-0 files except the one small, behavior-preserving factor noted in
§2 for `events.py`.

---

## 1. Goal & success criteria

**Goal:** Extract a client-facing `AgentRunner` interface. The runner yields
events live (a generator) and exposes a `RunResult` after iteration. Phase 0's
`TracingAgent`, its event model, and the zero-upstream-edits property all stay
intact. The runner bridges `TracingAgent`'s pushed events (it calls
`emitter.emit()` deep in the loop) to the generator's pull, using a background
thread + a thread-safe queue.

**Done when:**

1. `AgentRunner` is an abstract interface: `run(task, **kwargs) -> Iterator[Event]`
   (a generator), plus a `result` attribute that holds a `RunResult` after the
   generator is exhausted.
2. `MiniSweAgentRunner` implements it by running the **unchanged** `TracingAgent`
   on a background thread, bridging pushed events to the generator via a queue.
3. `run_traced.py` is rewired to a **thin client**: it builds a runner, iterates
   `run()`, and feeds each event to console + JSONL sinks. `./run.sh --model
   mock|vibeproxy` behaves identically to Phase 0 (same UX, same artifacts).
4. The event model is **unchanged** from Phase 0 (same `Event`, same 6 types).
5. Tests prove: the runner yields the full event sequence ending in
   `run.finished`; `RunResult` reflects `exit_status`; the terminal `Submitted`
   `action.done` survives the bridge; exceptions in the agent thread propagate to
   the caller; the thin-client CLI still produces a genuine red→green mock run.

---

## 2. Architecture

The phase rests on a queue+thread bridge that turns `TracingAgent`'s push into
the runner's pull, with **zero changes to `TracingAgent` or upstream**.

```
trace/
  events.py          # near-unchanged — Event + Emitter (console/JSONL sinks).
                     #   One small, behavior-preserving factor (see below) so
                     #   QueueEmitter can reuse seq/clock logic without dup.
  tracing_agent.py   # UNCHANGED — the reviewed Phase-0 agent
  models_mock.py     # UNCHANGED
  runner.py          # NEW — AgentRunner (ABC) + RunResult + QueueEmitter
                     #       + MiniSweAgentRunner
  run_traced.py      # REWIRED — thin client over the runner
```

### Components (each one clear responsibility)

- **`Event`** — reused from Phase 0, unchanged: `Event(seq, t, type, data)`.

- **`RunResult`** — dataclass:
  ```python
  @dataclass
  class RunResult:
      exit_status: str       # e.g. "Submitted", "LimitsExceeded", or exc type
      ok: bool               # True if no uncaught exception
      n_calls: int
      total_cost: float
      submission: str = ""   # the agent's final submission text, if any
      error: str | None = None  # exception_str when ok is False
  ```
  Assembled from `TracingAgent.run()`'s returned dict (which carries
  `exit_status`/`submission`) plus the final `run.finished` event's data.

- **`QueueEmitter`** — satisfies the exact Emitter contract used by
  `TracingAgent`: `emit(type, **data) -> Event`, `set_clock(clock)`, `close()`.
  Instead of writing to console/JSONL, it `put`s each `Event` onto a
  `queue.Queue`. It reuses the seq counter + clock logic from the Phase-0
  `Emitter`. To avoid duplicating that logic, factor the seq/clock/`Event`
  construction in `events.py` into a small shared base (e.g. an
  `_EventSource` mixin or a base class the file's `Emitter` and the new
  `QueueEmitter` both use). This factor MUST be behavior-preserving for the
  existing `Emitter`: all four Phase-0 `events.py` tests must still pass
  unchanged. **Decision order for the implementer:** (1) FIRST try implementing
  `QueueEmitter` as a subclass of the existing `Emitter` (or by composition)
  with NO edit to `events.py` — if `Emitter`'s structure allows overriding the
  write sinks while reusing `seq`/clock, do that and touch nothing. (2) ONLY if
  that is not cleanly possible, do the minimal shared-base extraction in
  `events.py`. Either way, `QueueEmitter` lives in `runner.py`, and `events.py`
  changes (if any) are limited to the non-behavioral factor. This is the only
  permitted change to a reviewed Phase-0 file.

- **`AgentRunner` (ABC)** — the interface:
  ```python
  class AgentRunner(ABC):
      result: RunResult | None
      @abstractmethod
      def run(self, task: str, **kwargs) -> Iterator[Event]: ...
  ```

- **`MiniSweAgentRunner(AgentRunner)`** — the first adapter. Its `__init__`
  takes already-built collaborators: `MiniSweAgentRunner(model, env, *, agent_cfg:
  dict)`. It does NOT build the model/env itself — that keeps the runner
  decoupled from `litellm`/config loading and easy to test with the mock. The
  Phase-0 wiring (load `mini.yaml`, build mock-or-vibeproxy model, build
  `LocalEnvironment`, set `output_path`) stays in `run_traced.py` (the thin
  client), which constructs those and hands them to the runner. Its `run()`
  generator:
  1. creates a `QueueEmitter` over a `queue.Queue`,
  2. builds `TracingAgent(model, env, emitter=queue_emitter, **cfg)`,
  3. starts a background thread running `agent.run(task)`, capturing the returned
     dict OR any raised `BaseException`,
  4. yields events pulled from the queue until a `_DONE` sentinel,
  5. joins the thread; if it raised, re-raises on the caller's side; otherwise
     sets `self.result` from the captured dict + final event.

### Data flow

```
        background thread                      main thread (caller)
TracingAgent.run(task)                 MiniSweAgentRunner.run(task) [generator]
   emit(e) → QueueEmitter.put(e) ───►  queue.get() → yield e ──► client sinks
   ...                                    ...
   returns result dict ─┐
   (or raises) ─────────┤
     put(_DONE, payload)─┘            get(_DONE) → stop; join thread
                                       if thread raised: raise it
                                       else: self.result = RunResult(...)
```

### Two key mechanisms

- **Sentinel + exception capture.** The thread wraps `agent.run()` in
  try/except/finally. In `finally` it puts a `_DONE` sentinel onto the queue
  carrying either the returned result dict or the captured exception. The
  generator stops on `_DONE` and, if an exception was captured, re-raises it on
  the caller's thread. So `for event in runner.run(): ...` raises exactly like
  Phase 0's direct call did — same exception type, after the terminal event.
- **`run.finished` flows through the queue first.** It is emitted in
  `TracingAgent.run()`'s `finally` *before* the function returns/raises, so the
  caller sees it as the last yielded event; `RunResult` is then assembled from
  it + the returned dict.

---

## 3. Error handling

- **Exception fidelity (critical).** The thread captures any `BaseException`
  from `agent.run()`; the generator re-raises it on the caller's thread after
  the queue drains. `run.finished` with `ok=False` flows through first, so the
  caller sees the terminal event and then the exception — matching Phase 0
  semantics (e.g. the VibeProxy `AuthenticationError` run).
- **No deadlocks / no leaked threads.** The `queue.Queue` is unbounded, so
  `put` never blocks the agent thread. The generator has a `finally` that joins
  the thread (and drains any remaining items) even if the caller `break`s out of
  iteration early — an abandoned iteration cannot leak the thread.
- **No new limits.** Phase-0 cost/step/time limits already terminate the agent;
  the runner adds none.
- **Sink failures stay client-side.** The runner only yields events. Console
  swallow / loud-JSONL behavior lives in the Phase-0 `Emitter` used by the thin
  client, not in the runner.

---

## 4. Testing

All tests use the deterministic mock (no network; real regression guards).

- **Test 1 — event sequence (mock).** Iterate
  `MiniSweAgentRunner(mock).run(task)`; assert yielded types are
  `run.started → llm.call → llm.return → action → action.done → … →
  run.finished`, `seq` contiguous from 0, `result.exit_status == "Submitted"`,
  `result.ok is True`. (Thread+queue analog of Phase-0 Test A.)
- **Test 2 — terminal submission survives the bridge.** Single-turn submit mock;
  assert the final `action.done` is yielded AND `run.finished` follows — proving
  the `Submitted` path crosses the queue intact (Phase-0 Test B, re-proven
  through the runner).
- **Test 3 — exception propagation.** A mock whose action raises (the test
  models support a `raise` action); assert iterating `run()` re-raises that
  exception on the caller side, and that `run.finished` with `ok=False` was
  yielded before the raise.
- **Test 4 — thin-client integration.** Invoke the rewired `run_traced.py
  main()` with `--model mock` against a temp cwd; assert it exits cleanly,
  writes a parseable `events.jsonl`, and the genuine red→green still happens
  (Phase-0 deliverable preserved through the new architecture).

Existing Phase-0 tests (events, models_mock, tracing_agent) MUST continue to
pass unchanged.

---

## 5. Out of scope (explicitly deferred)

- HTTP/SSE protocol and any network transport (Phase 2).
- AGENTS.md / profile / skills / context assembly (Phase 3).
- TUI (Phase 5); GitHub PR worker (Phase 6).
- Workspace isolation (git worktree / container; the agent-execution-environment
  decision from the Phase-0 learning log) — a later supporting primitive.
- Additional runner adapters (ClaudeRunner, OpenAIResponsesRunner, etc.). Phase
  1 ships exactly one adapter: `MiniSweAgentRunner`. The ABC exists so they slot
  in later, but building them now is out of scope.
- Enriching the event model (full content/output fields). Reuse Phase-0 events
  verbatim; enrich only when a real client proves the need.
