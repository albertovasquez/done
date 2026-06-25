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

- **`Router`** — `__init__(self, model, *, catalog: list[tuple[str, str]],
  confidence_threshold: float = 0.6)`; `classify(self, prompt: str) ->
  Classification`. It calls its own fixed cheap model with the triage system
  prompt (the validated spike prompt), parses the JSON response, **validates
  `skills` against `catalog`** (drops any name not in the catalog), and sets
  `needs_clarification = (confidence < threshold or task_type == "ambiguous")`.
  The model is injected (built by `build_router_model()`), so tests pass a mock.

- **`build_router_model()`** — returns a `LitellmModel`-style model fixed to
  `openai/gpt-5.4-mini` via VibeProxy env (`VIBEPROXY_BASE_URL`/`_API_KEY`),
  `cost_tracking="ignore_errors"`. Independent of the worker model. (Note: the
  router calls the model directly for a single completion; it does NOT use the
  agent's tool-call BASH_TOOL path — it just needs text→JSON. Implementation may
  call `litellm.completion` directly rather than reuse `LitellmModel.query`,
  which is tool-call-shaped. Decided in the plan.)

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
parse args (--model = USER worker model) + load_dotenv
        │
        ▼
router = Router(build_router_model(), catalog=SKILL_CATALOG)
cls = router.classify(prompt)
emit "task.classified" (task_type, skills, confidence, suggested_model)
        │
   needs_clarification?  ──yes──► print clarifying_question; read user answer (input());
        │ no                       re-classify with prompt + "\n\n[clarification]: " + answer
        │                          (re-classify AT MOST ONCE; then proceed on that result)
        ▼
   dispatch on cls.task_type:
     chat_question                       ──► ChatHandler(user_model).answer(prompt) → print
     code_explain | code_* | ops_task    ──► MiniSweAgentRunner(user_model, env, agent_cfg).run(prompt)
        │                                     (the existing unchanged worker path; explain needs repo access)
  (if cls.suggested_model and it differs from user's model) print ONE advisory line; do NOT apply
```

### Key boundaries

- **Two model slots, never crossed:** router-model (fixed cheap, classify only)
  vs. worker-model (user's `--model`, does the work / answers chat).
- **The router never executes work** — `classify()` returns a `Classification`;
  the CLI chooses the handler.
- `task.classified` reuses the existing `Event` type (one-off emit); **no
  event-model change.**
- **Orchestration is extracted into a testable function** (e.g.
  `route_and_dispatch(...)`) so dispatch logic can be unit-tested without
  driving real models; `main()` stays a thin wrapper that does arg-parsing,
  dotenv, run-dir, and calls it. This is a minor, justified refactor of the
  file we are already modifying.

---

## 3. Error handling

- **Unparseable router output.** If `classify()` cannot parse the model's
  response as the expected JSON, it returns a `Classification` with
  `task_type="ambiguous"`, `confidence=0.0`, `needs_clarification=True`, and a
  generic `clarifying_question` — a parse failure degrades to "ask the user,"
  never to a wrong route.
- **Hallucinated skills.** `classify()` filters `skills` to catalog members and
  drops the rest. A non-catalog skill name can never reach downstream.
- **Router model unreachable** (VibeProxy down / wrong model). The litellm error
  propagates to the CLI, which prints the existing "Is VibeProxy running on
  :8317?"-style hint and exits. No silent fallback to the worker path — a broken
  router must not invisibly become "run the agent on everything."
- **Bounded clarification.** The CLI asks AT MOST ONCE: classify → clarify →
  re-classify → dispatch on the second result regardless of its confidence
  (proceed best-effort after one clarification; if still ambiguous, default to
  the agent path with a printed note). No infinite loop.
- **ChatHandler failure** is an LLM error surfaced to the user; it does not touch
  the worker/agent path.

---

## 4. Testing

Routing logic is deterministic by **mocking the router model** (no network),
mirroring the runner-test pattern.

- **Test 1 — classify parses + validates skills.** Mock router model returns
  `{task_type: "code_fix", skills: ["poker-domain-rules", "not-a-real-skill"],
  confidence: 0.9, ...}`; assert `Classification.skills == ["poker-domain-rules"]`
  (hallucinated dropped) and `needs_clarification is False`.
- **Test 2 — low-confidence + ambiguous set the gate.** (a) `confidence: 0.2`
  → `needs_clarification is True` with a `clarifying_question`. (b)
  `task_type: "ambiguous"` at high confidence → still `needs_clarification True`.
- **Test 3 — unparseable degrades safely.** Mock returns non-JSON; assert
  `classify()` returns `ambiguous / 0.0 / needs_clarification True` and does NOT
  raise.
- **Test 4 — dispatch routing (the "1+1" guard).** Stub the router to classify
  `chat_question`; assert `route_and_dispatch` calls the ChatHandler path and NOT
  `MiniSweAgentRunner`. Stub `code_fix`; assert it builds/calls the runner. Use
  monkeypatch/stubs so no real model or agent runs.
- **Test 5 — `suggested_model` never auto-applied.** Router suggests a different
  model than the user's `--model`; assert the worker model used is the user's and
  the suggestion is only printed.

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
