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
   `action.done` survives the bridge; ordinary `Exception` AND `BaseException`
   (e.g. `KeyboardInterrupt`) in the agent thread propagate to the caller without
   hanging; early `gen.close()` joins the worker thread; and the thin-client CLI
   still produces a genuine red→green mock run with contiguous `seq` in the JSONL
   (proving events are persisted via `write_event`, not re-`emit`ted).

> **Review note (2026-06-25):** This spec was revised after a Codex adversarial
> review. Must-fix items addressed: generator-cleanup overclaim corrected (no
> leak guarantee for *abandoned* generators; early close is blocking, no
> cancellation); `RunResult.submission` provenance + error-path default pinned;
> `Emitter.write_event` added so the thin client persists pre-built events
> without reassigning seq/t; worker catches `BaseException`; Tests 5 & 6 added
> for `BaseException` fidelity and early-close cleanup; thin-client preservation
> enumerated as explicit acceptance criteria.

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
  **Field provenance (verified against `default.py:122` + `tracing_agent.py:51`):**
  `DefaultAgent.run()` returns `self.messages[-1].get("extra", {})`, a dict whose
  top-level keys are `exit_status` and `submission`. So `RunResult.exit_status`
  and `submission` come from that returned dict. `ok`, `n_calls`, `total_cost`,
  and `error` come from the final `run.finished` event's data (they are NOT in
  the returned dict). **Error path:** when `agent.run()` raises (e.g.
  `AuthenticationError`), `super().run()` re-raises and returns NOTHING — so the
  returned dict is unavailable. In that case `RunResult` is built entirely from
  the `run.finished` event (always emitted in the agent's `finally` before the
  raise), with `submission=""` (it cannot be recovered) and `exit_status` taken
  from the event's `exit_status` field. `submission` is only populated on the
  success path.

- **`QueueEmitter`** — satisfies the exact Emitter contract that `TracingAgent`
  actually calls: it calls only `set_clock(clock)` and `emit(type, **data) ->
  Event` (verified: `tracing_agent.py:37,51` — it never calls `close()`).
  `QueueEmitter` also defines `close()` as a **no-op** (for interface symmetry;
  the runner's cleanup must not call it concurrently with the worker thread).
  Instead of writing to console/JSONL, `emit()` builds the `Event` (assigning its
  own monotonic `seq` and `t` from the clock) and `put`s it on a `queue.Queue`.

  **Subclassing `Emitter` is NOT clean** (verified `events.py:31`): `Emitter
  .__init__(jsonl_path, ...)` opens a file unconditionally, so a `QueueEmitter`
  subclass would either create an unwanted file sink (calling `super().__init__`)
  or have to skip `super().__init__` and re-init `_seq`/`_clock` anyway.
  **Therefore: do the minimal shared-base extraction.** Factor the
  seq/clock/`Event`-construction logic in `events.py` into a small shared base
  (e.g. `_EventSource` with `_next_event(type, **data) -> Event` that owns `_seq`
  and `_clock` + `set_clock`). `Emitter` uses it for its sinks; `QueueEmitter`
  (in `runner.py`) uses it to build then enqueue. This factor MUST be
  behavior-preserving: all four Phase-0 `events.py` tests pass unchanged. This is
  one of exactly two permitted, bounded changes to a reviewed Phase-0 file (the
  other is `Emitter.write_event`, below).

- **`Emitter.write_event(event: Event) -> None`** (NEW method on the Phase-0
  `Emitter`) — the thin client consumes events the runner already built; it must
  NOT re-`emit()` them, because `Emitter.emit()` constructs a *fresh* `Event`
  with a new `seq` and a new `t` (verified `events.py:39-40`), which would corrupt
  the JSONL's seq/timestamps. `write_event(event)` writes the *given* event's
  `to_dict()` to the JSONL sink and prints it to console, WITHOUT reassigning
  `seq`/`t`. `emit()` is refactored to `event = self._next_event(...);
  self.write_event(event); return event` so the two share one write path
  (DRY, behavior-preserving — the four existing tests still pass). This is the
  second bounded change to `events.py`.

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
  3. starts a background thread running `agent.run(task)`, in a wrapper that
     catches **`BaseException`** (not just `Exception`) and, in a `finally`, puts
     a `_DONE` sentinel carrying the returned dict (success) or the captured
     exception (failure),
  4. yields events pulled from the queue until the `_DONE` sentinel,
  5. on `_DONE`: joins the thread; if an exception was captured, sets
     `self.result` from the final `run.finished` event (with `submission=""`) and
     re-raises; otherwise sets `self.result` from the captured dict + final event.
     The generator also has a `finally` that drains-to-`_DONE` and joins on early
     close (blocking — see §3 cleanup semantics).

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
- **Single producer + synchronous enqueue (the ordering guarantee depends on
  this).** Exactly ONE thread (the agent thread) ever calls `QueueEmitter.emit`,
  and `emit` does a synchronous `queue.put(event)` (no async buffering). The same
  thread puts `_DONE` from `agent.run()`'s wrapper `finally`, which runs only
  AFTER `TracingAgent.run()`'s own `finally` (where `run.finished` is emitted).
  Because a single producer writes both `run.finished` and then `_DONE` to one
  FIFO queue, the generator is guaranteed to dequeue `run.finished` before
  `_DONE`. This guarantee breaks if `_DONE` is ever put from a different thread or
  if `emit` buffers asynchronously — neither is permitted. `seq` and the clock
  are touched only on this one producer thread, so they need no locking.

---

## 3. Error handling

- **Exception fidelity (critical).** The worker wrapper catches **`BaseException`**
  (NOT just `Exception`) from `agent.run()` — `TracingAgent` itself catches
  `BaseException` (`tracing_agent.py:46`), so the wrapper must too, or a
  worker-side `KeyboardInterrupt` would skip the `_DONE` put and the generator
  would block forever on `queue.get()`. The captured exception travels on the
  `_DONE` payload; the generator re-raises it on the caller's thread after the
  queue drains. `run.finished` with `ok=False` flows through first (it is emitted
  in the agent's `finally` before the raise), so the caller sees the terminal
  event and then the exception — matching Phase 0 (e.g. the VibeProxy
  `AuthenticationError` run).
- **Generator cleanup — precise semantics (no overclaim).** The generator has a
  `finally` that, on `gen.close()` or an exception during iteration (including a
  `for`-loop `break`, which calls `close()`), drains the queue to `_DONE` and
  joins the worker thread. **This cleanup is BLOCKING:** if the worker is mid-`agent.run()`
  (e.g. blocked in model/env I/O), close/`break` waits until the agent finishes
  and emits `_DONE` — there is NO cooperative cancellation in Phase 0. **Caveat
  the spec does NOT overclaim:** a generator that is merely *abandoned* (created,
  partially iterated, never closed and never garbage-collected) does not run its
  `finally` deterministically, so its worker thread can outlive the iteration.
  **Caller contract:** consumers MUST either exhaust the generator or call
  `gen.close()` (a `for` loop that completes or `break`s does this automatically;
  a `with closing(runner.run(task)) as gen:` is the safe pattern for partial
  consumption). Cooperative cancellation/timeout is explicitly deferred to a
  later phase.
- **No producer deadlock.** The `queue.Queue` is unbounded, so `put` never blocks
  the agent thread (unbounded queue prevents *producer* deadlock; it does not
  bound *worker lifetime* — see cleanup semantics above).
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
- **Test 3 — `Exception` propagation.** A mock whose action raises an ordinary
  `Exception` (`{"raise": RuntimeError(...)}`, supported at `test_models.py:78`);
  assert iterating `run()` re-raises that exception type on the caller side, and
  that `run.finished` with `ok=False` was yielded before the raise.
- **Test 5 — `BaseException` fidelity (the concurrency regression guard).** A
  mock whose action raises a `KeyboardInterrupt` (or a custom `BaseException`
  subclass). Assert: (a) `run.finished ok=False` IS yielded, (b) the same
  `BaseException` type re-raises on the caller, and critically (c) the call
  returns/raises within a short timeout — i.e. it does NOT hang. This is the test
  that fails if the worker wrapper catches only `Exception`: a `KeyboardInterrupt`
  would then skip `_DONE`, and `queue.get()` would block forever. Use a hard
  timeout (e.g. run the iteration under a watchdog) so a hang is a test FAILURE,
  not a hung test run.
- **Test 6 — early-close cleanup.** Iterate one event (`run.started`) then call
  `gen.close()`. Assert the worker thread terminates (`thread.join(timeout=...)`
  succeeds, `thread.is_alive()` is False afterward) — proving the generator's
  `finally` joins on early close. (Uses the mock, which finishes fast, so the
  documented blocking-until-agent-finishes behavior completes promptly.)
- **Test 4 — thin-client integration.** Invoke the rewired `run_traced.py
  main()` with `--model mock` against a temp cwd; assert it exits cleanly,
  writes a parseable `events.jsonl` whose `seq` values are contiguous from 0 (this
  catches the `write_event` regression — if the client re-`emit()`ed instead of
  `write_event`ing, seq/t would be wrong), and the genuine red→green still
  happens (Phase-0 deliverable preserved through the new architecture).

**Thin-client preservation — explicit acceptance criteria (not just "behaves
identically").** The rewired `run_traced.py` MUST preserve every current
behavior (verified against `run_traced.py`): (1) `load_dotenv(REPO_ROOT/.env)`
before reading env (`:63`); (2) set `agent_cfg["output_path"]` so `traj.json` is
written per run (`:77`); (3) the `except KeyboardInterrupt` branch around runner
iteration (`:82`) — distinct from `Exception`; (4) the VibeProxy error hint on
failure in vibeproxy mode (`:86`); (5) close the client's file `Emitter` in a
`finally` (`:92`); (6) print the events/trajectory paths at the end (`:93-94`);
(7) same `--model mock|vibeproxy`, `--task`, `--cwd` CLI and exit code 0 on
success. The client builds the file/console `Emitter` and feeds each yielded
`Event` to it via `Emitter.write_event(event)` (NOT `emit`).

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
