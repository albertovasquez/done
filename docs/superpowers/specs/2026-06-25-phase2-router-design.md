# Phase 2 — The Router (triage & dispatch)

**Date:** 2026-06-25
**Status:** Approved design, pre-implementation
**Scope:** Phase 2 only. Add a Router that classifies a request and dispatches it
to the right handler. The router **classifies and dispatches; it does NOT answer
requests itself, and it does NOT pick the worker model.** Do not build the skills
*loading* mechanism (knowledge layer — next phase), the HTTP/SSE protocol, the
TUI, or workspace isolation.

> Naming note: the original roadmap labeled the HTTP/SSE protocol "Phase 2." We
> are reordering: this Router work is the more valuable next step (it fixes
> incorrect behavior on varied inputs and is the foundation for skills). The
> protocol becomes a later phase. "Phase 2" here = the Router.

---

## 1. Goal & success criteria

**Goal:** Add a **Router** — a standalone classifier that reads a request and
determines *what kind of work it is* and *which skills it needs*, so the harness
dispatches to the right handler instead of forcing every input through the
SWE-bench bug-fixer path (the cause of the "what is 1+1" → edits-the-calculator
failure).

**Design tenets (from the brainstorm, explicit):**
- The router **does not answer** requests — not even "simple" ones. (It cannot
  reliably judge a question's difficulty, and the harness exists for real work,
  not Q&A. Answering is a *different* component's job.)
- The router **does not pick the worker model.** The user's `--model` choice is
  authoritative. The router may emit a non-binding `suggested_model`, printed but
  never auto-applied.
- The router has its **own fixed, cheap, prompt-controllable model** for
  classification, separate from the worker model. (Lesson from the spike:
  Claude-via-VibeProxy is persona-locked and ignores a custom system prompt;
  gpt-5.4-mini via VibeProxy respects it and classified 8/8 cleanly.)

**Done when:**

1. `Router.classify(prompt) → Classification` exists, using a fixed cheap router
   model (gpt-5.4-mini via VibeProxy), independent of the user's worker model.
2. `Classification` carries `task_type`, `skills` (validated against a catalog),
   `confidence`, `suggested_model` (advisory, may be None), `reasoning`,
   `needs_clarification`, `clarifying_question`.
3. The CLI orchestrates: classify → if `needs_clarification`, ask ONE clarifying
   question and re-classify once → dispatch:
   - `chat_question` → `ChatHandler.answer(prompt)` using the **user's** worker
     model.
   - `code_explain` / `code_fix` / `code_feature` / `code_refactor` / `ops_task`
     → the existing `MiniSweAgentRunner` (unchanged path). (`code_explain` goes to
     the agent, not the ChatHandler, because explaining usually needs repo access
     — see §2.)
4. A `task.classified` event is emitted (observability), reusing the existing
   `Event` type. The user's `--model` is always authoritative; `suggested_model`
   is printed, never applied.
5. The "what is 1+1" case routes to the chat handler (or a clarify prompt), NOT
   the calculator-editing agent.
6. Skills are *selected and recorded*, **not loaded** into agent context (that is
   the next phase, the knowledge layer).

> **Review note (2026-06-25):** Revised after a Codex adversarial review. Must-fix
> items addressed: (1) router VibeProxy error hint is independent of `--model`
> (router uses VibeProxy even in mock mode); (2) `--model mock` chat dispatch can't
> use the tool-call mock to answer — it prints an honest "needs --model vibeproxy"
> message instead; (3) ambiguous-after-one-clarification does NOT fall through to
> the agent (prevents recreating the 1+1 failure); (4) `task.classified` event
> seq must stay contiguous given two independent emitter counters. Should-fixes:
> router uses an injected `complete(system,user)->str` wrapper (not the tool-call
> `LitellmModel.query`); fenced-JSON stripping; unknown `task_type` → `ambiguous`;
> `input()` EOF/empty fails safe; `route_and_dispatch` takes explicit injected
> deps; suggested-model compares against the *resolved* `VIBEPROXY_MODEL` id.

---

## 2. Architecture

The Router is a standalone component that sits *in front of* the runner. **No
changes to `runner.py`, `events.py`, or `tracing_agent.py`.**

```
trace/
  router.py          # NEW — Router, Classification, build_router_model(), SKILL_CATALOG
  chat_handler.py    # NEW — ChatHandler: one-shot answer with the USER's model
  runner.py          # unchanged (the worker path)
  events.py          # unchanged (Event reused for task.classified)
  run_traced.py      # MODIFIED — orchestrates classify -> clarify? -> dispatch
```

### Components

- **`Classification`** (dataclass):
  ```python
  @dataclass
  class Classification:
      task_type: str               # chat_question|code_explain|code_fix|code_feature|code_refactor|ops_task|ambiguous
      skills: list[str]            # validated against the catalog
      confidence: float            # 0.0-1.0
      reasoning: str
      suggested_model: str | None  # advisory only; never auto-applied
      needs_clarification: bool
      clarifying_question: str | None
  ```

- **`Router`** — `__init__(self, complete_fn, *, catalog: list[tuple[str, str]],
  confidence_threshold: float = 0.6)` where `complete_fn(system, user) -> str`;
  `classify(self, prompt: str) -> Classification`. It calls `complete_fn` with the
  triage system prompt (the validated spike prompt) + the user prompt, strips code
  fences and parses the JSON, **normalizes unknown `task_type` to `"ambiguous"`**,
  **validates `skills` against `catalog`** (drops any non-catalog name), and sets
  `needs_clarification = (confidence < threshold or task_type == "ambiguous")`.
  `complete_fn` is injected, so tests pass a stub returning canned text — no
  network, no model object.

- **`complete(system, user) -> str`** — a thin module-level completion function
  the router uses (NOT `LitellmModel.query`, which always sends `tools=[BASH_TOOL]`
  and parses tool calls — wrong for text→JSON, verified `litellm_model.py:66`).
  It calls `litellm.completion(model="openai/gpt-5.4-mini",
  api_base=VIBEPROXY_BASE_URL, api_key=VIBEPROXY_API_KEY,
  cost_tracking ignored, messages=[system, user])` and returns
  `response.choices[0].message.content`. The `Router` takes this callable
  injected (`Router(complete_fn, ...)`), so tests pass a stub returning canned
  text — no "LitellmModel-style model" object, no reaching into `.config`. There
  is no separate `build_router_model()`; `complete` IS the router's model slot,
  fixed to gpt-5.4-mini for now (swappable later — the persona-lock constraint:
  the router model must be prompt-controllable).

- **`SKILL_CATALOG`** — list of `(name, description)` tuples. Placeholder content
  (the spike's catalog: laravel-migrations, react-native-release,
  poker-domain-rules, python-testing, git-pr-flow) until the knowledge-layer phase
  defines real skills. The router only ever sees descriptions.

- **`ChatHandler`** — `__init__(self, model)`; `answer(self, prompt: str) -> str`:
  one LLM completion with the **user's** worker model, returns answer text.
  Minimal now; grows later.

  **Scope clarification — what goes to ChatHandler:** Only `chat_question` is
  dispatched to the ChatHandler this phase. `code_explain` is a known wrinkle: a
  good explanation often needs to *read the actual code in the repo*, which a
  bare one-shot ChatHandler (no repo access) cannot do well. To avoid building a
  half-answer, **`code_explain` routes to the agent path** this phase (the agent
  can read files), NOT the ChatHandler. So the dispatch is: `chat_question` →
  ChatHandler; everything else (`code_explain`, `code_fix`, `code_feature`,
  `code_refactor`, `ops_task`) → `MiniSweAgentRunner`. A repo-aware chat/explain
  handler is a later refinement.

### Data flow (in `run_traced.py`)

```
parse args (--model = mock|vibeproxy) + load_dotenv
resolve worker model id:  mock -> "mock";  vibeproxy -> VIBEPROXY_MODEL (e.g. "gpt-5.4")
        │
        ▼
router = Router(complete, catalog=SKILL_CATALOG)   # `complete` = the gpt-5.4-mini wrapper
try: cls = router.classify(prompt)
except <router model error>: print "Is VibeProxy running on :8317? (router uses it
        even when --model mock)"; exit  ──  INDEPENDENT of args.model (router always
                                            uses VibeProxy; see Error handling)
emit "task.classified" via the CLI Emitter (assigns seq 0)   ── see event-seq note below
        │
   needs_clarification?  ──yes──► print clarifying_question; read user answer (input());
        │ no                       re-classify with prompt + "\n\n[clarification]: " + answer
        │                          (re-classify AT MOST ONCE)
        ▼
   dispatch on cls.task_type (after one clarification, see Error handling for the
   ambiguous-fallback rule — it does NOT blindly enter the agent):
     chat_question                       ──► ChatHandler(worker_model).answer(prompt) → print
     code_explain | code_* | ops_task    ──► MiniSweAgentRunner(worker_model, env, agent_cfg).run(prompt)
        │                                     (the existing unchanged worker path; explain needs repo access)
  (if cls.suggested_model and it differs from the RESOLVED worker model id)
        print ONE advisory line; do NOT apply
```

### Event sequencing (must-fix from review)

There are TWO emitters with independent `seq` counters: the CLI's `Emitter`
(events.py, `emit()` starts `_seq` at 0) and the runner's internal `QueueEmitter`
(also starts at 0; runner events reach the CLI as already-built `Event`s written
via `Emitter.write_event`, which PRESERVES their seq). If the CLI calls
`Emitter.emit("task.classified", ...)` (seq 0) and then writes runner events
(seq 0,1,2…), the JSONL has **two seq=0 rows** — a corruption.

**Resolution:** `task.classified` is the FIRST event of the run and must own
seq 0 with the runner's stream continuing from there. Since the CLI `Emitter` and
the runner's `QueueEmitter` are separate objects, the clean fix is: the CLI emits
`task.classified` via `Emitter.emit(...)` (seq 0), and for the agent path the
runner's events are renumbered as the CLI writes them so the combined JSONL is
contiguous. Concretely, the CLI keeps a single monotonic counter by writing ALL
events (including runner events) through one `Emitter` whose `emit` is the sole
seq source — but runner events arrive pre-built. So: **the CLI rewrites each
yielded runner event's `seq` to the Emitter's next value before `write_event`**,
i.e. a small `write_renumbered(event)` helper, OR (simpler) `task.classified` is
written with `write_event` using an Event the CLI builds with an explicit seq and
the runner stream is offset by 1. The plan picks ONE concrete mechanism; the
REQUIREMENT here is: **the final `events.jsonl` has strictly contiguous `seq`
from 0, with `task.classified` first.** A test asserts this.

### Key boundaries

- **Two model slots, never crossed:** router-model (fixed cheap, classify only)
  vs. worker-model (resolved from the user's `--model`/`VIBEPROXY_MODEL`).
- **The router never executes work** — `classify()` returns a `Classification`;
  the CLI chooses the handler.
- **Worker model resolution:** `--model` selects mock vs vibeproxy; the actual
  vibeproxy model id is `VIBEPROXY_MODEL`. The `suggested_model` advisory compares
  against this RESOLVED id, not the raw `--model` flag.
- `task.classified` reuses the existing `Event` type; **no event-model change**
  (but see the seq-contiguity requirement above).
- **Orchestration is extracted into a testable function** with explicit
  dependency injection so dispatch can be unit-tested without real models or I/O:
  `route_and_dispatch(prompt, *, router, emitter, make_chat_handler, run_agent,
  ask_user, echo, worker_model_id) -> int`, where:
  - `router` is a `Router` (with a stub `complete_fn` in tests),
  - `make_chat_handler()` returns a ChatHandler (stub in tests),
  - `run_agent(prompt)` runs the `MiniSweAgentRunner` path and writes events
    (stub/spy in tests — Test 4 asserts it is/ isn't called),
  - `ask_user(question) -> str` wraps `input()` (stub in tests; raises/returns
    "" to exercise the EOF/empty rules),
  - `echo(text)` wraps `print` (capture in tests),
  - `worker_model_id` is the resolved id (for the suggested-model comparison).
  `main()` stays a thin wrapper: arg-parse, dotenv, resolve worker model, build
  run-dir + `Emitter`, build the real `complete`/handler/agent callables, and call
  `route_and_dispatch`. This is a minor, justified refactor of the file we are
  already modifying.

---

## 3. Error handling

- **Unparseable router output.** `classify()` first strips markdown code fences
  (```` ```json ```` / ```` ``` ````) and leading/trailing prose, then parses. If
  it still cannot parse the expected JSON, it returns a `Classification` with
  `task_type="ambiguous"`, `confidence=0.0`, `needs_clarification=True`, and a
  generic `clarifying_question` — a parse failure degrades to "ask the user,"
  never to a wrong route.
- **Unknown `task_type`.** The model may return a `task_type` not in the enum.
  `classify()` normalizes any unrecognized value to `"ambiguous"` (which sets
  `needs_clarification=True`). The dispatch table therefore only ever sees known
  values; an unknown type can never fall through to the agent.
- **Hallucinated skills.** `classify()` filters `skills` to catalog members and
  drops the rest. A non-catalog skill name can never reach downstream.
- **Router model unreachable** (VibeProxy down / wrong model). The litellm error
  propagates out of `classify()` to the CLI, which prints an "Is VibeProxy running
  on :8317? (the router uses VibeProxy even when --model is mock)" hint and exits.
  This hint is **independent of `args.model`** — the existing worker-only hint
  (which checks `args.model == "vibeproxy"`) does NOT cover the router, since the
  router always uses VibeProxy. No silent fallback to the worker path — a broken
  router must not invisibly become "run the agent on everything."
- **`--model mock` + chat dispatch (must-fix).** The default worker model is a
  `DeterministicToolcallModel` that replays canned bash tool-calls — it CANNOT
  answer a free-form `chat_question`. Resolution: the `ChatHandler` does NOT use
  the worker model object directly; it takes a model that can do a plain
  completion. For `--model vibeproxy`, that is the user's VibeProxy model (works).
  For `--model mock`, a real chat answer is not possible, so the ChatHandler
  prints an explicit, honest message: `"[mock mode] classified as chat_question;
  chat answers require --model vibeproxy. Classification: <reasoning>"` rather
  than feeding the prompt to a tool-call model. So the mock path still
  demonstrates *routing* (the "1+1" no longer edits the calculator) without
  pretending to answer. Tests cover both: a mock-mode chat → the honest message
  (no runner, no tool-call model invoked); a vibeproxy-mode chat → a real answer
  (mocked in unit tests).
- **Bounded clarification + safe ambiguous fallback (must-fix).** The CLI asks AT
  MOST ONCE. After the single clarification round, dispatch is by the
  re-classified `task_type` — BUT if it is STILL `ambiguous` (or `chat_question`
  with mock), it does **NOT** blindly enter the agent (that would recreate the
  "1+1 edits the calculator" failure). Instead the CLI prints: `"Still
  unclear after clarification — not running the agent. Please rephrase as a
  concrete task."` and exits without dispatching to `MiniSweAgentRunner`.
  Defaulting-to-agent on persistent ambiguity is explicitly rejected.
- **`input()` failures.** During clarification, an EOF/`Ctrl-D` (`EOFError`),
  `KeyboardInterrupt`, or an empty/whitespace-only answer is treated as "no
  clarification given" → the CLI prints "no clarification provided — not running
  the agent" and exits without dispatching. Non-interactive stdin therefore fails
  safe (does not hang, does not auto-run the agent).
- **KeyboardInterrupt preservation.** The existing `except KeyboardInterrupt`
  branch around the worker iteration is preserved inside `route_and_dispatch`'s
  agent-path; a Ctrl-D/Ctrl-C during the *clarification* prompt is handled per
  the `input()` rule above.
- **ChatHandler failure** is an LLM error surfaced to the user; it does not touch
  the worker/agent path.

---

## 4. Testing

Routing logic is deterministic by **mocking the router model** (no network),
mirroring the runner-test pattern.

- **Test 1 — classify parses + validates skills + unknown-type normalization.**
  Stub `complete_fn` returns `{task_type: "code_fix", skills: ["poker-domain-rules",
  "not-a-real-skill"], confidence: 0.9, ...}`; assert `skills ==
  ["poker-domain-rules"]` and `needs_clarification is False`. Second case: a JSON
  with `task_type: "frobnicate"` (not in enum) → normalized to `"ambiguous"`,
  `needs_clarification is True`.
- **Test 2 — low-confidence + ambiguous set the gate.** (a) `confidence: 0.2`
  → `needs_clarification True` with a `clarifying_question`. (b)
  `task_type: "ambiguous"` at high confidence → still `needs_clarification True`.
- **Test 3 — unparseable + fenced JSON.** (a) `complete_fn` returns non-JSON
  garbage → `classify()` returns `ambiguous / 0.0 / needs_clarification True`,
  does NOT raise. (b) `complete_fn` returns the JSON wrapped in ```` ```json ````
  fences → parsed correctly (fence-stripping works).
- **Test 4 — dispatch routing (the "1+1" guard).** Stub router → `chat_question`;
  assert `route_and_dispatch` calls the chat handler and the `run_agent` spy is
  NEVER called. Stub → `code_fix`; assert `run_agent` IS called. Inject stubs so no
  real model/agent runs.
- **Test 5 — `suggested_model` never auto-applied.** Stub router suggests a model
  ≠ the resolved `worker_model_id`; assert the agent/chat path uses
  `worker_model_id` and the suggestion is only echoed (captured via the `echo`
  stub).
- **Test 6 — `--model mock` chat answers honestly, never tool-call model.** With
  the mock worker and a stubbed `chat_question` classification, assert the chat
  path prints the honest "[mock mode] … requires --model vibeproxy" message and
  the `run_agent` spy is NOT called (the tool-call mock model is never asked to
  chat).
- **Test 7 — ambiguous-after-clarification does NOT run the agent.** Stub the
  router to return `ambiguous` BOTH times (initial + after clarification);
  `ask_user` returns a non-empty answer; assert `run_agent` is NEVER called and the
  "still unclear — not running the agent" message is echoed. This is the guard
  against re-introducing the "1+1 edits the calculator" failure via the fallback.
- **Test 8 — `input()` EOF/empty fails safe.** `ask_user` raises `EOFError` (and
  separately returns `"   "`); assert in both cases `run_agent` is NEVER called and
  the "no clarification provided" message is echoed.
- **Test 9 — event seq is contiguous with `task.classified` first.** Run the
  agent path through `route_and_dispatch` with a stub `run_agent` that emits a few
  events via the same `Emitter`; assert the resulting `events.jsonl` has
  `task.classified` at `seq=0` and strictly contiguous `seq` after it (guards the
  two-emitter seq-collision must-fix).

Existing Phase 0/1 tests (15) MUST still pass — the router is additive; the agent
path is unchanged.

---

## 5. Out of scope (explicitly deferred)

- **Skills LOADING** — reading `SKILL.md` content into the agent's context, the
  skills/ directory format, progressive disclosure into the prompt. This phase
  only *selects* skill names. Loading = the next phase (knowledge layer).
- Router auto-selecting the worker model (rejected — user picks; router only
  suggests).
- Per-step / mid-run re-routing (speculative; not needed yet).
- HTTP/SSE protocol, TUI, GitHub PR worker, workspace isolation.
- A real/local router model (Ollama/Flash). gpt-5.4-mini is the fixed router
  model for now; swapping it is a one-line change later. The persona-lock lesson
  means the router model must be prompt-controllable — record this constraint but
  don't act on "own/local model" this phase.
- Enriching the event model. `task.classified` reuses the existing `Event`.
