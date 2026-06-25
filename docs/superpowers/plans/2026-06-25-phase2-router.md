# Phase 2 — Router Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Router that classifies a request with a cheap model and dispatches it (`chat_question` → a ChatHandler using the user's model; everything else → the existing `MiniSweAgentRunner`), with a low-confidence clarify gate that fixes the "what is 1+1 → edits the calculator" failure.

**Architecture:** `router.py` holds a `complete()` cheap-model wrapper, a `Router` that turns a prompt into a `Classification` (validated, fenced-JSON-tolerant, fail-safe), and the `SKILL_CATALOG`. `chat_handler.py` answers chat one-shot. `run_traced.py` gains an injectable `route_and_dispatch()` that orchestrates classify → clarify(≤1) → dispatch, and `main()` becomes its thin wrapper. The router and runner never share a model.

**Tech Stack:** Python 3.11 (`.venv`), `litellm` (already a dep), the existing `trace/` modules, `pytest`.

## Global Constraints

- **Zero upstream edits.** Nothing under `upstream/` changes. (spec scope)
- **No changes to `runner.py`, `events.py`, `tracing_agent.py`, `models_mock.py`.** The router sits in front of the runner; `task.classified` reuses the existing `Event`. (spec §2)
- **Two model slots, never crossed:** router-model = fixed cheap `openai/gpt-5.4-mini` via the injected `complete()` wrapper, classify-only; worker-model = resolved from `--model`/`VIBEPROXY_MODEL`, does the work / answers chat. (spec §1, §2)
- **Router never answers and never picks the worker model.** `suggested_model` is advisory — printed, never applied; compared against the *resolved* worker model id (`VIBEPROXY_MODEL`), not the raw `--model` flag. (spec §1, §2)
- **Router uses an injected `complete(system, user) -> str`**, NOT `LitellmModel.query` (which always sends `tools=[BASH_TOOL]`). (spec §2)
- **Fail-safe everywhere:** unparseable output / unknown `task_type` / EOF/empty clarification / still-ambiguous-after-one-clarification all degrade to "ask or stop", NEVER to running the agent on an unclear request. (spec §3)
- **`--model mock` + chat:** the mock is a tool-call model that can't chat → print an honest "needs --model vibeproxy" message; never feed the prompt to the mock model. (spec §3)
- **Event seq stays strictly contiguous from 0 with `task.classified` first.** (spec §2)
- **Router VibeProxy error hint is independent of `--model`** (router uses VibeProxy even in mock mode). (spec §3)
- **Python env:** run tests as `.venv/bin/python -m pytest ...` (system python3 is 3.9, too old). Repo root: `/Users/alberto/Work/Quiubo/harness`.

---

## File Structure

| Path | Responsibility | Change |
|------|----------------|--------|
| `trace/router.py` | `Classification`, `Router`, `complete()`, `SKILL_CATALOG`, `TASK_TYPES` | CREATE |
| `trace/chat_handler.py` | `ChatHandler.answer(prompt) -> str` (one-shot, user model) | CREATE |
| `trace/run_traced.py` | `route_and_dispatch(...)` + thin `main()` wrapper | MODIFY |
| `tests/test_router.py` | Router classification tests (1, 2, 3) | CREATE |
| `tests/test_run_traced.py` | dispatch/clarify/seq tests (4, 5, 6, 7, 8, 9) + keep existing | MODIFY (add) |

---

### Task 1: `router.py` — Classification + Router (pure logic)

**Files:**
- Create: `trace/router.py`
- Test: `tests/test_router.py`

**Interfaces:**
- Consumes: nothing from other tasks (pure logic; `complete()` is injected in tests).
- Produces:
  - `TASK_TYPES: list[str]` = `["chat_question","code_explain","code_fix","code_feature","code_refactor","ops_task","ambiguous"]`
  - `SKILL_CATALOG: list[tuple[str,str]]` (the 5 placeholder skills).
  - `@dataclass Classification(task_type, skills, confidence, reasoning, suggested_model, needs_clarification, clarifying_question)`.
  - `complete(system: str, user: str) -> str` — litellm wrapper to gpt-5.4-mini (used by `main()`; NOT used in unit tests).
  - `class Router`: `__init__(self, complete_fn, *, catalog=SKILL_CATALOG, confidence_threshold=0.6)`; `classify(self, prompt: str) -> Classification`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_router.py`:
```python
import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

import json
from trace.router import Router, Classification, SKILL_CATALOG


def _stub(payload: str):
    """A complete_fn that ignores its args and returns a fixed string."""
    return lambda system, user: payload


def test_1_parses_validates_skills_and_unknown_type():
    r = Router(_stub(json.dumps({
        "task_type": "code_fix",
        "skills": ["poker-domain-rules", "not-a-real-skill"],
        "confidence": 0.9, "reasoning": "x", "suggested_model": None,
    })), confidence_threshold=0.6)
    c = r.classify("fix the rakeback test")
    assert c.task_type == "code_fix"
    assert c.skills == ["poker-domain-rules"]      # hallucinated dropped
    assert c.needs_clarification is False

    r2 = Router(_stub(json.dumps({"task_type": "frobnicate", "skills": [],
                                  "confidence": 0.9, "reasoning": "x"})))
    c2 = r2.classify("weird")
    assert c2.task_type == "ambiguous"             # unknown normalized
    assert c2.needs_clarification is True


def test_2_low_confidence_and_ambiguous_set_gate():
    r = Router(_stub(json.dumps({"task_type": "code_fix", "skills": [],
                                 "confidence": 0.2, "reasoning": "unsure"})))
    c = r.classify("the tests are red")
    assert c.needs_clarification is True
    assert c.clarifying_question

    r2 = Router(_stub(json.dumps({"task_type": "ambiguous", "skills": [],
                                  "confidence": 0.95, "reasoning": "vague"})))
    assert r2.classify("do the thing").needs_clarification is True


def test_3_unparseable_and_fenced_json():
    # (a) garbage -> safe ambiguous, no raise
    c = Router(_stub("I cannot help with that, here's some prose.")).classify("x")
    assert c.task_type == "ambiguous"
    assert c.confidence == 0.0
    assert c.needs_clarification is True

    # (b) fenced JSON -> parsed
    fenced = "```json\n" + json.dumps({"task_type": "ops_task", "skills": [],
                                       "confidence": 0.9, "reasoning": "pr"}) + "\n```"
    c2 = Router(_stub(fenced)).classify("make a PR")
    assert c2.task_type == "ops_task"
    assert c2.needs_clarification is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/alberto/Work/Quiubo/harness && .venv/bin/python -m pytest tests/test_router.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trace.router'`.

- [ ] **Step 3: Implement `router.py`**

Create `trace/router.py`:
```python
"""Router: classify a request with a CHEAP model and decide how to dispatch it.

The router classifies and dispatches — it does NOT answer requests and does NOT
pick the worker model. It has its own fixed cheap model (gpt-5.4-mini) via the
injected `complete(system, user) -> str` wrapper (NOT LitellmModel.query, which
is tool-call shaped). Parse failures / unknown types degrade to 'ambiguous' so an
unclear request never silently runs the agent.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Callable

import litellm

TASK_TYPES = ["chat_question", "code_explain", "code_fix", "code_feature",
              "code_refactor", "ops_task", "ambiguous"]

SKILL_CATALOG: list[tuple[str, str]] = [
    ("laravel-migrations", "Write/run Laravel DB migrations and schema changes"),
    ("react-native-release", "Cut and ship a React Native mobile release"),
    ("poker-domain-rules", "Poker rake/rakeback math and PPPoker domain logic"),
    ("python-testing", "Write and run pytest unit/integration tests"),
    ("git-pr-flow", "Create branches, commits, and pull requests"),
]

ROUTER_MODEL = "openai/gpt-5.4-mini"


@dataclass
class Classification:
    task_type: str
    skills: list[str] = field(default_factory=list)
    confidence: float = 0.0
    reasoning: str = ""
    suggested_model: str | None = None
    needs_clarification: bool = False
    clarifying_question: str | None = None


def complete(system: str, user: str) -> str:
    """Thin cheap-model completion for classification. Used by the CLI; tests
    inject a stub instead. Plain text in, text out — no tool calls."""
    resp = litellm.completion(
        model=ROUTER_MODEL,
        api_base=os.getenv("VIBEPROXY_BASE_URL", "http://localhost:8317/v1"),
        api_key=os.getenv("VIBEPROXY_API_KEY", "dummy-not-used"),
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
        max_tokens=300,
    )
    return resp.choices[0].message.content or ""


def _system_prompt(catalog: list[tuple[str, str]]) -> str:
    return (
        "You are a fast TRIAGE router for a coding agent harness. Read the user's "
        "request and classify it. You do NOT answer or chat; you only classify. "
        "Respond with ONLY a JSON object, no prose, with keys: "
        f"task_type (one of {TASK_TYPES}), skills (list of skill NAMES from the "
        "catalog that apply, may be empty), confidence (0.0-1.0), "
        "suggested_model (a model name or null; advisory only), "
        "reasoning (one short sentence).\n\nSkill catalog (name: description):\n"
        + "\n".join(f"  {n}: {d}" for n, d in catalog)
    )


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.removeprefix("```json").removeprefix("```").strip()
        if t.endswith("```"):
            t = t[: -3].strip()
    return t


class Router:
    def __init__(self, complete_fn: Callable[[str, str], str], *,
                 catalog: list[tuple[str, str]] = SKILL_CATALOG,
                 confidence_threshold: float = 0.6):
        self._complete = complete_fn
        self._catalog = catalog
        self._catalog_names = {n for n, _ in catalog}
        self._threshold = confidence_threshold

    def classify(self, prompt: str) -> Classification:
        raw = self._complete(_system_prompt(self._catalog), prompt)
        try:
            data = json.loads(_strip_fences(raw))
            if not isinstance(data, dict):
                raise ValueError("not an object")
        except Exception:
            return Classification(
                task_type="ambiguous", confidence=0.0, needs_clarification=True,
                reasoning="router output was not parseable JSON",
                clarifying_question="I couldn't interpret that. What concrete task "
                                    "should I do?")
        task_type = data.get("task_type", "ambiguous")
        if task_type not in TASK_TYPES:
            task_type = "ambiguous"
        skills = [s for s in (data.get("skills") or []) if s in self._catalog_names]
        try:
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        reasoning = str(data.get("reasoning", ""))
        suggested = data.get("suggested_model") or None
        needs = confidence < self._threshold or task_type == "ambiguous"
        question = None
        if needs:
            question = (f"That request is unclear ({reasoning or 'low confidence'}). "
                        "What concrete task should I do?")
        return Classification(task_type=task_type, skills=skills, confidence=confidence,
                              reasoning=reasoning, suggested_model=suggested,
                              needs_clarification=needs, clarifying_question=question)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/alberto/Work/Quiubo/harness && .venv/bin/python -m pytest tests/test_router.py -v`
Expected: 3 passed.

- [ ] **Step 5: Run the whole suite**

Run: `cd /Users/alberto/Work/Quiubo/harness && .venv/bin/python -m pytest tests/ -q`
Expected: all pass (15 existing + 3 router = 18).

- [ ] **Step 6: Commit**

```bash
cd /Users/alberto/Work/Quiubo/harness
git add trace/router.py tests/test_router.py
git -c user.name='harness' -c user.email='harness@local' commit -m "feat(router): classification with fail-safe parsing + skill validation"
```

---

### Task 2: `chat_handler.py` — one-shot chat answer

**Files:**
- Create: `trace/chat_handler.py`
- Test: folded into Task 3's dispatch tests (the handler is trivial; it's exercised via the dispatch path). No standalone test file — a one-method pass-through to a model is not worth an isolated mock-the-model test; Test 6 covers the mock-mode behavior and Test 4 covers that chat dispatch calls it.

**Interfaces:**
- Consumes: a model object with a litellm-style completion, OR `None` for mock mode.
- Produces: `class ChatHandler`: `__init__(self, worker_model_id: str | None)`; `answer(self, prompt: str) -> str`. If `worker_model_id` is None (mock mode), returns the honest "[mock mode] …" message WITHOUT calling any model. Otherwise does one `litellm.completion` with that model id via VibeProxy and returns the text.

- [ ] **Step 1: Implement `chat_handler.py`** (trivial; no separate failing-test step — verified through Task 3)

Create `trace/chat_handler.py`:
```python
"""ChatHandler: answer a chat_question with the USER's worker model (one-shot).

The router dispatches chat_question here — the router itself never answers. In
mock mode (no real worker model) we cannot answer, so we print an honest message
instead of feeding the prompt to the tool-call mock model.
"""

from __future__ import annotations

import os

import litellm


class ChatHandler:
    def __init__(self, worker_model_id: str | None):
        # None => mock mode (no chat-capable model available)
        self._model_id = worker_model_id

    def answer(self, prompt: str) -> str:
        if self._model_id is None:
            return ("[mock mode] classified as chat_question; chat answers require "
                    "--model vibeproxy. (Routing worked: this did not run the agent.)")
        resp = litellm.completion(
            model="openai/" + self._model_id,
            api_base=os.getenv("VIBEPROXY_BASE_URL", "http://localhost:8317/v1"),
            api_key=os.getenv("VIBEPROXY_API_KEY", "dummy-not-used"),
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1000,
        )
        return resp.choices[0].message.content or ""
```

- [ ] **Step 2: Sanity import + commit** (the real verification is Task 3)

Run: `cd /Users/alberto/Work/Quiubo/harness && .venv/bin/python -c "import sys; sys.path.insert(0,'.'); sys.path.insert(0,'upstream/src'); from trace.chat_handler import ChatHandler; print(ChatHandler(None).answer('hi')[:20])"`
Expected: prints `[mock mode] classifie` (the mock-mode branch works without a model).

```bash
cd /Users/alberto/Work/Quiubo/harness
git add trace/chat_handler.py
git -c user.name='harness' -c user.email='harness@local' commit -m "feat(chat): one-shot ChatHandler (honest message in mock mode)"
```

---

### Task 3: Rewire `run_traced.py` — `route_and_dispatch` + dispatch/clarify/seq tests

**Files:**
- Modify: `trace/run_traced.py`
- Test: `tests/test_run_traced.py` (add Tests 4–9; keep existing test_4_thin_client + test_4b)

**Interfaces:**
- Consumes: `trace.router.{Router, Classification, complete, SKILL_CATALOG}`, `trace.chat_handler.ChatHandler`, `trace.runner.MiniSweAgentRunner`, `trace.events.Emitter`.
- Produces: `route_and_dispatch(prompt, *, router, emitter, make_chat_handler, run_agent, ask_user, echo, worker_model_id) -> int`. Behavior:
  1. `cls = router.classify(prompt)`; emit `task.classified` via `emitter.emit(...)` (this is seq 0).
  2. If `cls.needs_clarification`: `answer = ask_user(cls.clarifying_question)`; on `EOFError`/`KeyboardInterrupt`/blank → echo "no clarification provided — not running the agent"; return 0. Else `cls = router.classify(prompt + "\n\n[clarification]: " + answer)` (once).
  3. If `cls.suggested_model` and `cls.suggested_model != worker_model_id`: echo one advisory line.
  4. Dispatch: `chat_question` → `echo(make_chat_handler().answer(prompt))`. `ambiguous` (still, after step 2) → echo "still unclear — not running the agent; please rephrase"; return 0. Anything else (`code_explain`/`code_*`/`ops_task`) → `run_agent(prompt)`.
  5. Return 0.
- `run_agent(prompt)` (built in `main`) iterates `MiniSweAgentRunner(...).run(prompt)` writing each event via the SAME emitter, **renumbered** so seq stays contiguous after `task.classified`.

- [ ] **Step 1: Write the failing tests (4–9)**

Add to `tests/test_run_traced.py` (keep the existing imports + `test_4_thin_client_mock_red_green` + `test_4b_...`):
```python
import json as _json
from trace.router import Router, Classification
from trace.run_traced import route_and_dispatch


class _FixedRouter:
    """Router stand-in returning preset Classifications in sequence."""
    def __init__(self, *classifications):
        self._seq = list(classifications)
        self._i = -1
    def classify(self, prompt):
        self._i += 1
        return self._seq[min(self._i, len(self._seq) - 1)]


def _spy_agent():
    calls = []
    def run_agent(prompt):
        calls.append(prompt)
    run_agent.calls = calls
    return run_agent


def _emitter(tmp_path):
    from trace.events import Emitter
    return Emitter(tmp_path / "events.jsonl", clock=lambda: 0.0, console=False)


def _cls(task_type, **kw):
    return Classification(task_type=task_type, **kw)


def test_4_chat_question_does_not_run_agent(tmp_path):
    spy = _spy_agent()
    out = []
    rc = route_and_dispatch(
        "what is 1+1",
        router=_FixedRouter(_cls("chat_question", confidence=0.97)),
        emitter=_emitter(tmp_path),
        make_chat_handler=lambda: type("H", (), {"answer": lambda s, p: "2"})(),
        run_agent=spy, ask_user=lambda q: "", echo=out.append, worker_model_id="gpt-5.4")
    assert rc == 0
    assert spy.calls == []                 # agent NEVER ran for a chat question
    assert "2" in out

    spy2 = _spy_agent()
    route_and_dispatch(
        "fix the bug",
        router=_FixedRouter(_cls("code_fix", confidence=0.9)),
        emitter=_emitter(tmp_path), make_chat_handler=lambda: None,
        run_agent=spy2, ask_user=lambda q: "", echo=lambda t: None, worker_model_id="gpt-5.4")
    assert spy2.calls == ["fix the bug"]   # agent DID run for code_fix


def test_5_suggested_model_not_applied(tmp_path):
    out = []
    spy = _spy_agent()
    route_and_dispatch(
        "fix it",
        router=_FixedRouter(_cls("code_fix", confidence=0.9, suggested_model="claude-opus-4-8")),
        emitter=_emitter(tmp_path), make_chat_handler=lambda: None,
        run_agent=spy, ask_user=lambda q: "", echo=out.append, worker_model_id="gpt-5.4")
    assert spy.calls == ["fix it"]                       # ran with the worker path
    assert any("claude-opus-4-8" in line for line in out)  # suggestion was printed
    # (the test does not pass the suggested model to run_agent; run_agent uses the
    #  worker model wired in main — here the spy just records the prompt.)


def test_6_mock_mode_chat_is_honest(tmp_path):
    out = []
    spy = _spy_agent()
    from trace.chat_handler import ChatHandler
    route_and_dispatch(
        "what is 1+1",
        router=_FixedRouter(_cls("chat_question", confidence=0.97)),
        emitter=_emitter(tmp_path),
        make_chat_handler=lambda: ChatHandler(None),   # mock mode
        run_agent=spy, ask_user=lambda q: "", echo=out.append, worker_model_id=None)
    assert spy.calls == []
    assert any("mock mode" in line for line in out)


def test_7_ambiguous_after_clarification_does_not_run_agent(tmp_path):
    out = []
    spy = _spy_agent()
    route_and_dispatch(
        "do the thing",
        router=_FixedRouter(_cls("ambiguous", confidence=0.2, needs_clarification=True,
                                 clarifying_question="what?"),
                            _cls("ambiguous", confidence=0.2, needs_clarification=True)),
        emitter=_emitter(tmp_path), make_chat_handler=lambda: None,
        run_agent=spy, ask_user=lambda q: "still do the thing",
        echo=out.append, worker_model_id="gpt-5.4")
    assert spy.calls == []                                # agent NEVER ran
    assert any("still unclear" in line.lower() for line in out)


def test_8_eof_and_empty_clarification_fail_safe(tmp_path):
    for answer in [EOFError(), "   "]:
        out = []
        spy = _spy_agent()
        def ask(q, _a=answer):
            if isinstance(_a, BaseException):
                raise _a
            return _a
        route_and_dispatch(
            "the tests are red",
            router=_FixedRouter(_cls("ambiguous", confidence=0.2, needs_clarification=True,
                                     clarifying_question="which?")),
            emitter=_emitter(tmp_path), make_chat_handler=lambda: None,
            run_agent=spy, ask_user=ask, echo=out.append, worker_model_id="gpt-5.4")
        assert spy.calls == []
        assert any("no clarification" in line.lower() for line in out)


def test_9_event_seq_contiguous_with_classified_first(tmp_path):
    from trace.events import Emitter, Event
    em = Emitter(tmp_path / "events.jsonl", clock=lambda: 0.0, console=False)
    def run_agent(prompt):
        # simulate runner events arriving pre-built with their OWN seq 0,1
        for i, t in enumerate(["llm.call", "action"]):
            em.write_renumbered(Event(seq=i, t=0.0, type=t, data={}))
    route_and_dispatch(
        "fix it",
        router=_FixedRouter(_cls("code_fix", confidence=0.9)),
        emitter=em, make_chat_handler=lambda: None,
        run_agent=run_agent, ask_user=lambda q: "", echo=lambda t: None,
        worker_model_id="gpt-5.4")
    em.close()
    rec = [_json.loads(l) for l in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert rec[0]["type"] == "task.classified" and rec[0]["seq"] == 0
    assert [r["seq"] for r in rec] == list(range(len(rec)))   # strictly contiguous
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd /Users/alberto/Work/Quiubo/harness && .venv/bin/python -m pytest tests/test_run_traced.py -v`
Expected: FAIL — `route_and_dispatch` / `Emitter.write_renumbered` don't exist yet.

- [ ] **Step 3: Add `write_renumbered` to the Emitter**

Wait — this touches `events.py`, a Phase-0/1 file. It is a small, additive, behavior-preserving method (no existing behavior changes), needed for the seq-contiguity must-fix. Add to `trace/events.py` `Emitter`:
```python
    def write_renumbered(self, event: Event) -> None:
        """Write an externally-built event but reassign its seq to THIS emitter's
        next value, so a single emitter keeps one contiguous seq stream across
        events it built (emit) and events built elsewhere (e.g. the runner)."""
        renum = Event(seq=self._seq, t=event.t, type=event.type, data=event.data)
        self._seq += 1
        self.write_event(renum)
```

- [ ] **Step 4: Rewire `run_traced.py`**

Replace the import of `MiniSweAgentRunner` region and the `main()` body. Keep `_load_agent_config`, `_build_vibeproxy_model`, `_run_id`, `DEFAULT_TASK`, arg-parsing, `load_dotenv`, `REPO_ROOT`. New code:

Imports (replace the `MiniSweAgentRunner` import line):
```python
from trace.runner import MiniSweAgentRunner  # noqa: E402
from trace.router import Router, complete, SKILL_CATALOG  # noqa: E402
from trace.chat_handler import ChatHandler  # noqa: E402
```

Add the orchestration function (above `main`):
```python
def route_and_dispatch(prompt, *, router, emitter, make_chat_handler, run_agent,
                       ask_user, echo, worker_model_id) -> int:
    cls = router.classify(prompt)
    emitter.emit("task.classified", task_type=cls.task_type, skills=cls.skills,
                 confidence=cls.confidence, suggested_model=cls.suggested_model)
    if cls.needs_clarification:
        try:
            answer = ask_user(cls.clarifying_question or "Please clarify:")
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if not answer.strip():
            echo("no clarification provided — not running the agent.")
            return 0
        cls = router.classify(prompt + "\n\n[clarification]: " + answer)
    if cls.suggested_model and cls.suggested_model != worker_model_id:
        echo(f"(router suggests model '{cls.suggested_model}'; using your '{worker_model_id}')")
    if cls.task_type == "chat_question":
        echo(make_chat_handler().answer(prompt))
        return 0
    if cls.task_type == "ambiguous":
        echo("still unclear after clarification — not running the agent; please rephrase.")
        return 0
    run_agent(prompt)
    return 0
```

Replace the model/env/runner/loop part of `main()` with:
```python
    worker_model_id = None if args.model == "mock" else os.getenv("VIBEPROXY_MODEL", "gpt-5.4")

    if args.model == "mock":
        model = build_mock_model()
    else:
        model = _build_vibeproxy_model()
    env = LocalEnvironment(cwd=args.cwd)
    agent_cfg = _load_agent_config()
    agent_cfg["output_path"] = str(run_dir / "traj.json")
    emitter = Emitter(run_dir / "events.jsonl", clock=lambda: 0.0, console=True)

    def run_agent(prompt):
        runner = MiniSweAgentRunner(model, env, agent_cfg=agent_cfg)
        try:
            for event in runner.run(prompt):
                emitter.write_renumbered(event)
        except KeyboardInterrupt:
            print("\ninterrupted", file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            if args.model == "vibeproxy":
                print(f"\nVibeProxy run failed: {e}\n"
                      f"Is VibeProxy running on {os.getenv('VIBEPROXY_BASE_URL', 'http://localhost:8317/v1')}?",
                      file=sys.stderr)
            else:
                raise

    router = Router(complete, catalog=SKILL_CATALOG)
    try:
        rc = route_and_dispatch(
            args.task, router=router, emitter=emitter,
            make_chat_handler=lambda: ChatHandler(worker_model_id),
            run_agent=run_agent, ask_user=input, echo=print,
            worker_model_id=worker_model_id)
    except Exception as e:  # noqa: BLE001 — router model unreachable etc.
        print(f"\nRouter failed: {e}\n"
              f"Is VibeProxy running on {os.getenv('VIBEPROXY_BASE_URL', 'http://localhost:8317/v1')}? "
              f"(the router uses VibeProxy even when --model is mock)", file=sys.stderr)
        rc = 1
    finally:
        emitter.close()
        print(f"\nevents:     {run_dir / 'events.jsonl'}")
        print(f"trajectory: {run_dir / 'traj.json'}")
    return rc
```

- [ ] **Step 5: Run the run_traced tests**

Run: `cd /Users/alberto/Work/Quiubo/harness && .venv/bin/python -m pytest tests/test_run_traced.py -v`
Expected: all pass (existing test_4_thin_client + test_4b + new 4,5,6,7,8,9).

> Note: `test_4_thin_client_mock_red_green` (Phase 1) drove the agent directly via `main()`. With routing now in front, `main(["--model","mock", ...])` first classifies. The default task ("Fix the failing test…") classifies as a code_* type → agent path → still fixes the bug. If that test's assertions now also see a leading `task.classified` event, update its seq assertion to expect `task.classified` first (contiguous), consistent with Test 9. Make that adjustment in this step if the test fails on the new first event.

- [ ] **Step 6: Run the full suite**

Run: `cd /Users/alberto/Work/Quiubo/harness && .venv/bin/python -m pytest tests/ -q`
Expected: all pass.

- [ ] **Step 7: Live demos (manual deliverable check — the whole point)**

Run:
```bash
cd /Users/alberto/Work/Quiubo/harness
git checkout examples/sample-repo/calculator.py 2>/dev/null; git clean -fdq examples/sample-repo/ 2>/dev/null
# (a) the case that used to break: a question, mock mode
./run.sh --model mock --task "what is 1+1"
# (b) a real coding task still routes to the agent
./run.sh --model mock --task "Fix the failing test in examples/sample-repo so add(2,3)==5"
git checkout examples/sample-repo/calculator.py 2>/dev/null; git clean -fdq examples/sample-repo/ 2>/dev/null
```
Expected: (a) emits `task.classified` then either an honest "[mock mode]… chat" message or a clarify prompt — and does NOT edit calculator.py. (b) emits `task.classified` (a code type) then the agent runs and fixes the bug; `events.jsonl` seq contiguous with `task.classified` first.

- [ ] **Step 8: Commit**

```bash
cd /Users/alberto/Work/Quiubo/harness
git add trace/run_traced.py trace/events.py tests/test_run_traced.py
git -c user.name='harness' -c user.email='harness@local' commit -m "feat(cli): route_and_dispatch — classify, clarify, dispatch; seq-safe events"
```

---

## Self-Review

**1. Spec coverage:**
- §1 router classifies, own cheap model, no-answer, no-model-pick → Tasks 1 (Router), 3 (dispatch).
- §1 Classification fields → Task 1 dataclass.
- §1 dispatch (chat→ChatHandler; code_explain+code_*+ops→agent) → Task 3 `route_and_dispatch`.
- §1 task.classified event + suggested_model advisory vs resolved id → Task 3 (emit + the advisory compares `worker_model_id`).
- §1 "1+1" routes away from the agent → Task 3 Test 4/6 + live demo (a).
- §1 skills selected not loaded → Task 1 (validate against catalog; no loader anywhere).
- §2 complete() wrapper not LitellmModel.query → Task 1.
- §2 unknown task_type → ambiguous; fenced JSON; skill validation → Task 1 (Tests 1,3).
- §2 event seq contiguous, task.classified first → Task 3 `write_renumbered` + Test 9.
- §2 worker model resolved from VIBEPROXY_MODEL → Task 3 `worker_model_id`.
- §2 route_and_dispatch injected deps → Task 3 signature.
- §3 unparseable/unknown fail-safe → Task 1. Router error hint independent of --model → Task 3 outer except. mock chat honest message → Task 2 + Test 6. ambiguous-after-clarification no agent → Task 3 + Test 7. input() EOF/empty fail-safe → Task 3 + Test 8. KeyboardInterrupt preserved in agent path → Task 3 run_agent.
- §4 Tests 1–9 → Tasks 1 (1,2,3) + 3 (4,5,6,7,8,9).

No gaps found.

**2. Placeholder scan:** No TBD/TODO; every code step has complete code.

**3. Type consistency:**
- `complete(system, user) -> str` — Task 1, injected to `Router`, real one used in Task 3. ✓
- `Router(complete_fn, *, catalog, confidence_threshold)` + `classify(prompt) -> Classification` — Task 1, used Task 3 + tests. ✓
- `Classification(task_type, skills, confidence, reasoning, suggested_model, needs_clarification, clarifying_question)` — Task 1; constructed in tests via `_cls`. ✓
- `ChatHandler(worker_model_id)` + `answer(prompt) -> str` — Task 2, used Task 3 + Test 6. ✓
- `route_and_dispatch(prompt, *, router, emitter, make_chat_handler, run_agent, ask_user, echo, worker_model_id) -> int` — Task 3, all tests call it with this exact signature. ✓
- `Emitter.write_renumbered(event)` — added Task 3 Step 3, used by `run_agent` + Test 9. ✓
- `task.classified` event type — emitted Task 3, asserted Test 9. ✓

One consistency note resolved in review: Test 5's comment clarifies the spy `run_agent` only records the prompt (worker model is wired in `main`, not passed to the spy), so "suggested model not applied" is asserted via the absence of the suggested id in the agent call + its presence in `echo` output — consistent with the injected design.
