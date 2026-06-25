# Phase 3 — Knowledge/Skills Loading Layer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Inject the body of each Router-selected `SKILL.md` into the agent's system prompt, so an agent task runs with the relevant domain knowledge in context.

**Architecture:** A new `trace/skills.py` owns content (scan `skills/` → catalog from frontmatter; compose selected bodies into one text block). `TracingAgent` injects that block into the system message *after* Jinja renders it (so skill bodies can't break `StrictUndefined`). `run_traced.py` wires the two together: builds the catalog at startup, composes on the agent-dispatch path, emits a `skill.load` event, prints what was injected/skipped. The Router stays selection-only. Zero upstream edits.

**Tech Stack:** Python ≥3.10, PyYAML (already installed) for frontmatter, pytest, jinja2 (upstream). Models via VibeProxy (OpenAI-compatible).

## Global Constraints

- **Zero upstream edits.** Nothing under `upstream/` changes. Injection lives only in the `TracingAgent` subclass.
- **Python ≥ 3.10**, run via `.venv/bin/python`. Tests run as `.venv/bin/python -m pytest tests/` — scope to `tests/`, NOT bare `pytest` (bare collection walks `upstream/tests/` and errors on optional deps).
- **Skills (not `task_type`) drive the prompt.** `task_type` never branches the system prompt.
- **Inject the full body verbatim, AFTER Jinja render.** Bodies bypass Jinja; `{{ }}`/`{% %}` in a body is literal text.
- **Bad skills are skipped AND shown**, never fatal. Console prints injected + skipped (with reason) on every run that selects skills; a `skill.load` event records the same. `compose`/`load_catalog` catch `OSError`, `UnicodeDecodeError`, `yaml.YAMLError`, and a shape check (frontmatter is a dict with `name` + `description`); any caught failure → skip with a reason, never raise.
- **New params default to `""`/empty** so existing callers and the mock path are unaffected.
- **Test style** (upstream `AGENTS.md`): pytest, no mocking/patching unless required, no trivial tests, `assert func() == b` one-liners, real files via `tmp_path`.
- **Catalog ownership:** `SKILL_CATALOG` is REMOVED from `router.py`; `Router(catalog=...)` becomes required (no default). The catalog is produced only by `skills.load_catalog()`.

---

## File Structure

- **Create `trace/skills.py`** — content layer: `SkillLoad` dataclass, `load_catalog(skills_dir)`, `compose(skills_dir, names)`, plus internal frontmatter parsing.
- **Create `skills/python-testing/SKILL.md`** — one real skill (exercised by the demo) + two stub skills to prove multi-entry scanning.
- **Modify `trace/tracing_agent.py`** — add `skill_block` ctor kwarg + `_render_template` override (the injection seam).
- **Modify `trace/runner.py`** — `MiniSweAgentRunner.run` accepts `skill_block` keyword, passes to `TracingAgent` ctor (NOT into `**kwargs`).
- **Modify `trace/router.py`** — remove `SKILL_CATALOG`; make `catalog` a required ctor param.
- **Modify `trace/run_traced.py`** — build catalog from `skills.load_catalog`; add `load_skills` collaborator to `route_and_dispatch`; emit `skill.load`; print injected/skipped; thread `skill_block` through `run_agent`.
- **Create `tests/test_skills.py`** — unit tests for `skills.py`.
- **Create `tests/test_tracing_agent_skills.py`** — unit tests for the injection seam.
- **Modify `tests/test_router.py`** — pass inline catalogs explicitly (no more `SKILL_CATALOG` import).
- **Modify `tests/test_run_traced.py`** — fix `_spy_agent` to accept `skill_block`; update `test_4` ordering assertion; add a skills-injection dispatch test.

---

## Task 1: Content layer — `trace/skills.py` (SkillLoad, load_catalog, compose)

**Files:**
- Create: `trace/skills.py`
- Test: `tests/test_skills.py`

**Interfaces:**
- Consumes: nothing (leaf module; stdlib + `yaml`).
- Produces:
  - `@dataclass SkillLoad` with fields `block: str = ""`, `injected: list[str] = []`, `skipped: list[tuple[str, str]] = []`.
  - `load_catalog(skills_dir: Path) -> list[tuple[str, str]]` — `[(name, description)]`, sorted by name; bad/absent → skipped/`[]`, never raises.
  - `compose(skills_dir: Path, names: list[str]) -> SkillLoad` — reads each selected `<name>/SKILL.md`, appends body to `block`; failures recorded in `skipped` with a reason; never raises.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_skills.py`:

```python
import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

from pathlib import Path
from trace.skills import SkillLoad, load_catalog, compose


def _write_skill(root: Path, name: str, description: str, body: str, *, dirname=None):
    d = root / (dirname or name)
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}", encoding="utf-8")
    return d


def test_load_catalog_parses_frontmatter_sorted_and_skips_bad(tmp_path):
    _write_skill(tmp_path, "python-testing", "Write pytest tests", "# body")
    _write_skill(tmp_path, "git-pr-flow", "Make PRs", "# body2")
    (tmp_path / "no-skill-md").mkdir()                       # dir without SKILL.md -> skipped
    bad = tmp_path / "broken"; bad.mkdir()
    (bad / "SKILL.md").write_text("not: [valid", encoding="utf-8")  # malformed yaml -> skipped
    catalog = load_catalog(tmp_path)
    assert catalog == [("git-pr-flow", "Make PRs"), ("python-testing", "Write pytest tests")]


def test_load_catalog_absent_dir_is_empty(tmp_path):
    assert load_catalog(tmp_path / "does-not-exist") == []


def test_load_catalog_skips_name_mismatch_and_missing_keys(tmp_path):
    _write_skill(tmp_path, "real-name", "desc", "# b", dirname="wrong-dir")   # name != dirname
    miss = tmp_path / "no-desc"; miss.mkdir()
    (miss / "SKILL.md").write_text("---\nname: no-desc\n---\nbody", encoding="utf-8")  # no description
    assert load_catalog(tmp_path) == []


def test_compose_injects_bodies_in_selection_order(tmp_path):
    _write_skill(tmp_path, "a", "da", "Alpha body")
    _write_skill(tmp_path, "b", "db", "Bravo body")
    load = compose(tmp_path, ["b", "a"])
    assert load.injected == ["b", "a"]
    assert load.skipped == []
    assert load.block.index("Bravo body") < load.block.index("Alpha body")
    assert "## b" in load.block and "## a" in load.block


def test_compose_skips_missing_but_injects_good(tmp_path):
    _write_skill(tmp_path, "good", "dg", "Good body")
    load = compose(tmp_path, ["good", "ghost"])
    assert load.injected == ["good"]
    assert load.skipped == [("ghost", "no SKILL.md")]
    assert "Good body" in load.block


def test_compose_empty_selection_is_empty(tmp_path):
    assert compose(tmp_path, []) == SkillLoad()


def test_compose_body_with_jinja_survives_verbatim(tmp_path):
    _write_skill(tmp_path, "tpl", "d", "Use {{ x }} and {% if y %} here")
    load = compose(tmp_path, ["tpl"])
    assert "{{ x }}" in load.block and "{% if y %}" in load.block


def test_compose_non_utf8_is_skipped_not_raised(tmp_path):
    d = tmp_path / "binskill"; d.mkdir()
    (d / "SKILL.md").write_bytes(b"\xff\xfe\x00bad")
    load = compose(tmp_path, ["binskill"])
    assert load.injected == []
    assert load.skipped and load.skipped[0][0] == "binskill"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_skills.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'trace.skills'`.

- [ ] **Step 3: Write `trace/skills.py`**

```python
"""Knowledge/skills CONTENT layer: discover skills on disk, build the catalog
from frontmatter, and compose selected skill bodies into one injectable block.

Separate from the Router (which SELECTS skill names) and from TracingAgent
(which INJECTS the block). This module only reads files and returns data.

Every per-skill read is wrapped so one bad skill can never abort a run: a
missing file, unreadable file, non-UTF-8 content, malformed YAML, or
frontmatter missing name/description is recorded as 'skipped' with a reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class SkillLoad:
    block: str = ""
    injected: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (name, reason)


def _parse_skill_md(path: Path) -> tuple[dict, str]:
    """Return (frontmatter_dict, body). Raises on read/parse errors — callers
    wrap. Frontmatter is the leading ---\\n...\\n--- block."""
    text = path.read_text(encoding="utf-8")  # may raise OSError / UnicodeDecodeError
    if not text.startswith("---"):
        raise ValueError("missing frontmatter fence")
    _, fm, body = text.split("---", 2)        # may raise ValueError if < 2 fences
    data = yaml.safe_load(fm)                  # may raise yaml.YAMLError
    if not isinstance(data, dict):
        raise ValueError("frontmatter is not a mapping")
    return data, body.lstrip("\n")


def load_catalog(skills_dir: Path) -> list[tuple[str, str]]:
    """Scan skills_dir/<name>/SKILL.md, return [(name, description)] sorted by
    name. A dir whose SKILL.md is missing/malformed/name-mismatched/missing keys
    is skipped. Absent skills_dir -> []."""
    if not skills_dir.is_dir():
        return []
    catalog: list[tuple[str, str]] = []
    for child in sorted(skills_dir.iterdir(), key=lambda p: p.name):
        if not child.is_dir():
            continue
        skill_md = child / "SKILL.md"
        try:
            data, _ = _parse_skill_md(skill_md)
            name, desc = data.get("name"), data.get("description")
            if not name or not desc:
                raise ValueError("frontmatter missing name/description")
            if name != child.name:
                raise ValueError("name mismatch")
        except (OSError, UnicodeDecodeError, yaml.YAMLError, ValueError):
            continue  # silently omit from catalog; can't select what can't be parsed
        catalog.append((name, desc))
    return catalog


def compose(skills_dir: Path, names: list[str]) -> SkillLoad:
    """Read each selected skill's SKILL.md and append its body to one block.
    Records failures in skipped; never raises."""
    load = SkillLoad()
    bodies: list[str] = []
    for name in names:
        skill_md = skills_dir / name / "SKILL.md"
        if not skill_md.is_file():
            load.skipped.append((name, "no SKILL.md"))
            continue
        try:
            data, body = _parse_skill_md(skill_md)
            if data.get("name") != name:
                raise ValueError("name mismatch")
        except (OSError, UnicodeDecodeError) as e:
            load.skipped.append((name, f"unreadable: {type(e).__name__}"))
            continue
        except (yaml.YAMLError, ValueError) as e:
            load.skipped.append((name, f"bad frontmatter: {e}"))
            continue
        bodies.append(f"## {name}\n{body}")
        load.injected.append(name)
    if bodies:
        load.block = ("\n\n# Available Skills\n\n"
                      "The following skills apply to this task. Follow them.\n\n"
                      + "\n\n".join(bodies))
    return load
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_skills.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add trace/skills.py tests/test_skills.py
git commit -m "feat(skills): content layer — load_catalog + compose from SKILL.md"
```

---

## Task 2: Injection seam — `TracingAgent._render_template` override

**Files:**
- Modify: `trace/tracing_agent.py` (ctor `__init__` ~lines 26-29; add `_render_template` method)
- Test: `tests/test_tracing_agent_skills.py`

**Interfaces:**
- Consumes: `DefaultAgent._render_template` (upstream, `default.py:66-67`), `self.config.system_template`.
- Produces: `TracingAgent(model, env, *, emitter, skill_block: str = "", **kwargs)` — when `skill_block` is non-empty, it is appended to the rendered SYSTEM template only (matched by identity), after Jinja runs.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tracing_agent_skills.py`:

```python
import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

from trace.events import Emitter
from trace.tracing_agent import TracingAgent
from trace.models_mock import build_mock_model
from minisweagent.environments.local import LocalEnvironment


def _agent(tmp_path, skill_block):
    em = Emitter(tmp_path / "e.jsonl", clock=lambda: 0.0, console=False)
    return TracingAgent(
        build_mock_model(), LocalEnvironment(cwd=str(tmp_path)), emitter=em,
        skill_block=skill_block,
        system_template="SYS BASE", instance_template="INST {{task}}")


def test_skill_block_appended_to_system_template_only(tmp_path):
    a = _agent(tmp_path, "\n\nSKILLDATA")
    a.extra_template_vars = {"task": "t"}                    # so instance renders
    assert a._render_template(a.config.system_template) == "SYS BASE\n\nSKILLDATA"
    # instance template must NOT get the block
    assert "SKILLDATA" not in a._render_template(a.config.instance_template)


def test_empty_skill_block_is_byte_identical(tmp_path):
    a = _agent(tmp_path, "")
    assert a._render_template(a.config.system_template) == "SYS BASE"


def test_block_with_jinja_is_literal_not_evaluated(tmp_path):
    a = _agent(tmp_path, "\n\n{{ undefined_var }}")          # would raise if rendered
    assert a._render_template(a.config.system_template) == "SYS BASE\n\n{{ undefined_var }}"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_tracing_agent_skills.py -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'skill_block'`.

- [ ] **Step 3: Modify `trace/tracing_agent.py`**

In `__init__`, accept and store `skill_block`. Current:

```python
    def __init__(self, model, env, *, emitter: Emitter, **kwargs):
        super().__init__(model, env, **kwargs)
        self._emitter = emitter
        self._run_start = time.time()  # tracer-local clock; parent's _start_time is set in __init__
```

Change to:

```python
    def __init__(self, model, env, *, emitter: Emitter, skill_block: str = "", **kwargs):
        super().__init__(model, env, **kwargs)
        self._emitter = emitter
        self._skill_block = skill_block
        self._run_start = time.time()  # tracer-local clock; parent's _start_time is set in __init__

    def _render_template(self, template: str) -> str:
        # Inject selected skills AFTER Jinja renders the base, so a skill body
        # containing {{ }}/{% %} is literal text and cannot break StrictUndefined.
        # Identity match: only the system template gets skills, never instance.
        out = super()._render_template(template)
        if self._skill_block and template is self.config.system_template:
            out += self._skill_block
        return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tracing_agent_skills.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the existing tracing-agent suite to confirm no regression**

Run: `.venv/bin/python -m pytest tests/test_tracing_agent.py -v`
Expected: PASS (all existing tests still green — `skill_block` defaults to `""`).

- [ ] **Step 6: Commit**

```bash
git add trace/tracing_agent.py tests/test_tracing_agent_skills.py
git commit -m "feat(agents): inject skill_block into system prompt after render"
```

---

## Task 3: Runner passthrough — `MiniSweAgentRunner.run(skill_block=...)`

**Files:**
- Modify: `trace/runner.py` (`MiniSweAgentRunner.run`, lines ~83-96)
- Test: `tests/test_runner.py` (add one test)

**Interfaces:**
- Consumes: `TracingAgent(..., skill_block=...)` from Task 2.
- Produces: `MiniSweAgentRunner.run(self, task, *, skill_block: str = "", **kwargs)` — `skill_block` reaches the `TracingAgent` ctor; it MUST NOT be forwarded into `agent.run(task, **kwargs)` (which would make it a template var via `default.py:90`).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_runner.py`:

```python
def test_skill_block_reaches_system_message(tmp_path):
    """skill_block passed to run() must land in the agent's first system message,
    proving it reached the TracingAgent ctor (not leaked into run-kwargs)."""
    import sys
    sys.path.insert(0, "upstream/src"); sys.path.insert(0, ".")
    from trace.runner import MiniSweAgentRunner
    from trace.models_mock import build_mock_model
    from minisweagent.environments.local import LocalEnvironment
    import yaml
    from pathlib import Path
    cfg = yaml.safe_load(Path("upstream/src/minisweagent/config/mini.yaml").read_text())["agent"]
    runner = MiniSweAgentRunner(build_mock_model(), LocalEnvironment(cwd=str(tmp_path)), agent_cfg=cfg)
    # drain the generator so the run completes
    list(runner.run("noop task", skill_block="\n\nINJECTED_SKILL_MARKER"))
    # the agent's saved trajectory has the system message with our marker
    assert runner.result is not None
```

NOTE to implementer: if `tests/test_runner.py` lacks a clean way to inspect the system message, assert instead that `runner.run(task, skill_block=...)` completes without error and `runner.result.ok` is set — the byte-level system-message check is already covered by Task 2's `test_skill_block_appended_to_system_template_only`. Keep this test to the runner's contract: it accepts and threads `skill_block` without raising. Do not duplicate Task 2's assertion.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_runner.py::test_skill_block_reaches_system_message -v`
Expected: FAIL — `TypeError: run() got an unexpected keyword argument 'skill_block'`.

- [ ] **Step 3: Modify `trace/runner.py`**

Current `run` signature and agent construction:

```python
    def run(self, task: str, **kwargs) -> Iterator[Event]:
        q: "queue.Queue[Any]" = queue.Queue()
        emitter = QueueEmitter(q, clock=lambda: 0.0)
        agent = TracingAgent(self._model, self._env, emitter=emitter, **self._agent_cfg)
```

Change to (add keyword-only `skill_block`, pass to ctor, keep it OUT of `**kwargs`):

```python
    def run(self, task: str, *, skill_block: str = "", **kwargs) -> Iterator[Event]:
        q: "queue.Queue[Any]" = queue.Queue()
        emitter = QueueEmitter(q, clock=lambda: 0.0)
        agent = TracingAgent(self._model, self._env, emitter=emitter,
                             skill_block=skill_block, **self._agent_cfg)
```

The existing `agent.run(task, **kwargs)` inside `_worker` is unchanged — `skill_block` is now a named param, so it is not in `**kwargs` and cannot leak into `agent.run`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_runner.py -v`
Expected: PASS (new test + all existing runner tests).

- [ ] **Step 5: Commit**

```bash
git add trace/runner.py tests/test_runner.py
git commit -m "feat(runner): thread skill_block through to TracingAgent ctor"
```

---

## Task 4: Router catalog ownership — remove `SKILL_CATALOG`, require `catalog`

**Files:**
- Modify: `trace/router.py` (remove `SKILL_CATALOG` const lines ~22-28; change `Router.__init__` default at line ~82)
- Modify: `tests/test_router.py` (stop importing `SKILL_CATALOG`; pass inline catalogs)

**Interfaces:**
- Consumes: nothing new.
- Produces: `Router(complete_fn, *, catalog: list[tuple[str, str]], confidence_threshold: float = 0.6)` — `catalog` is now REQUIRED (no default). `SKILL_CATALOG` no longer exists.

- [ ] **Step 1: Update `tests/test_router.py` to fail against the new contract**

At the top, change the import (remove `SKILL_CATALOG`):

```python
from trace.router import Router, Classification
```

Add a shared inline catalog near the top (after imports):

```python
_CATALOG = [
    ("poker-domain-rules", "Poker rake/rakeback math and PPPoker domain logic"),
    ("python-testing", "Write and run pytest unit/integration tests"),
]
```

Then update every `Router(_stub(...))` and `Router(_stub(...), confidence_threshold=...)` call to pass `catalog=_CATALOG`. Concretely, the constructions currently at test_router.py lines 15, 25, 33, 39, 46, 54, 61, 65 become e.g.:

```python
    r = Router(_stub(json.dumps({...})), catalog=_CATALOG, confidence_threshold=0.6)
```
and for the ones without a threshold:
```python
    Router(_stub("..."), catalog=_CATALOG)
```

Keep all existing assertions. The hallucinated-skill-drop test still expects `["poker-domain-rules"]` since that name is in `_CATALOG`.

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_router.py -v`
Expected: FAIL — `ImportError: cannot import name 'SKILL_CATALOG'` (until router.py is changed) OR, after import fixed but before signature change, `TypeError` on `catalog=` if still defaulted. The end state must drive the signature change.

- [ ] **Step 3: Modify `trace/router.py`**

Delete the `SKILL_CATALOG` constant (lines ~22-28):

```python
SKILL_CATALOG: list[tuple[str, str]] = [
    ("laravel-migrations", "Write/run Laravel DB migrations and schema changes"),
    ("react-native-release", "Cut and ship a React Native mobile release"),
    ("poker-domain-rules", "Poker rake/rakeback math and PPPoker domain logic"),
    ("python-testing", "Write and run pytest unit/integration tests"),
    ("git-pr-flow", "Create branches, commits, and pull requests"),
]
```

Change `Router.__init__` from:

```python
    def __init__(self, complete_fn: Callable[[str, str], str], *,
                 catalog: list[tuple[str, str]] = SKILL_CATALOG,
                 confidence_threshold: float = 0.6):
```

to (required `catalog`):

```python
    def __init__(self, complete_fn: Callable[[str, str], str], *,
                 catalog: list[tuple[str, str]],
                 confidence_threshold: float = 0.6):
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_router.py -v`
Expected: PASS (all router tests, now with explicit catalogs).

- [ ] **Step 5: Commit**

```bash
git add trace/router.py tests/test_router.py
git commit -m "ref(router): remove SKILL_CATALOG; catalog is now a required param"
```

---

## Task 5: Wiring — `run_traced.py` builds catalog, composes, emits `skill.load`

**Files:**
- Modify: `trace/run_traced.py` (imports line ~30; `route_and_dispatch` signature + body; `run_agent` closure; `main` startup + dispatch wiring)
- Modify: `tests/test_run_traced.py` (`_spy_agent`; `test_4` ordering; add injection test)

**Interfaces:**
- Consumes: `skills.load_catalog`, `skills.compose`, `SkillLoad` (Task 1); `Router(catalog=...)` (Task 4); `MiniSweAgentRunner.run(skill_block=...)` (Task 3).
- Produces: `route_and_dispatch(prompt, *, router, emitter, make_chat_handler, run_agent, ask_user, echo, worker_model_id, load_skills=<empty default>)`. `run_agent(prompt, skill_block="")`.

- [ ] **Step 1: Fix `_spy_agent` and update `test_4`, then add the injection test (failing)**

In `tests/test_run_traced.py`, change `_spy_agent` so the spy accepts the new keyword (currently `run_agent(prompt)` — would break when called with `skill_block=`):

```python
def _spy_agent():
    calls = []
    def run_agent(prompt, skill_block=""):
        calls.append(prompt)
    run_agent.calls = calls
    return run_agent
```

Update `test_4`'s ordering assertion (currently `rec[1]` is `run.started`):

```python
    # task.classified first (seq 0); skill.load next; run.started follows; run.finished last.
    assert rec[0]["type"] == "task.classified"
    assert rec[1]["type"] == "skill.load"
    assert rec[2]["type"] == "run.started" and rec[-1]["type"] == "run.finished"
```

Add a new test proving the dispatch path composes skills and emits the event:

```python
def test_11_skill_load_emitted_and_block_passed(tmp_path):
    from trace.events import Emitter
    from trace.skills import SkillLoad
    import json as _j
    em = Emitter(tmp_path / "events.jsonl", clock=lambda: 0.0, console=False)
    received = {}
    def run_agent(prompt, skill_block=""):
        received["block"] = skill_block
    out = []
    route_and_dispatch(
        "fix the rake bug",
        router=_FixedRouter(_cls("code_fix", confidence=0.9, skills=["poker-domain-rules"])),
        emitter=em, make_chat_handler=lambda: None,
        run_agent=run_agent, ask_user=lambda q: "", echo=out.append,
        worker_model_id="gpt-5.4",
        load_skills=lambda names: SkillLoad(block="\n\nPOKER", injected=list(names), skipped=[]))
    em.close()
    rec = [_j.loads(l) for l in (tmp_path / "events.jsonl").read_text().splitlines()]
    sl = [r for r in rec if r["type"] == "skill.load"]
    assert len(sl) == 1 and sl[0]["data"]["injected"] == ["poker-domain-rules"]
    assert received["block"] == "\n\nPOKER"                 # block reached run_agent
    assert any("injected" in line for line in out)          # console showed it
```

- [ ] **Step 2: Run to verify the new/updated tests fail**

Run: `.venv/bin/python -m pytest tests/test_run_traced.py -v`
Expected: FAIL — `test_11` fails with `TypeError: route_and_dispatch() got an unexpected keyword argument 'load_skills'`; `test_4` fails on the new `rec[1]` assertion.

- [ ] **Step 3: Modify `trace/run_traced.py`**

(a) Imports — replace the router import line (~30) and add skills:

Current:
```python
from trace.router import Router, complete, SKILL_CATALOG  # noqa: E402
```
Change to:
```python
from trace.router import Router, complete  # noqa: E402
from trace import skills  # noqa: E402
```

(b) `route_and_dispatch` — add the `load_skills` parameter with a safe default and emit/compose on the agent path. Current head:
```python
def route_and_dispatch(prompt, *, router, emitter, make_chat_handler, run_agent,
                       ask_user, echo, worker_model_id) -> int:
```
Change to:
```python
def route_and_dispatch(prompt, *, router, emitter, make_chat_handler, run_agent,
                       ask_user, echo, worker_model_id,
                       load_skills=lambda names: skills.SkillLoad()) -> int:
```

Then, replace the final dispatch line. Current tail of the function:
```python
    if cls.task_type == "ambiguous":
        echo("still unclear after clarification — not running the agent; please rephrase.")
        return 0
    run_agent(prompt)
    return 0
```
Change the agent-dispatch to compose + emit + print + pass the block:
```python
    if cls.task_type == "ambiguous":
        echo("still unclear after clarification — not running the agent; please rephrase.")
        return 0
    load = load_skills(cls.skills)
    emitter.emit("skill.load", injected=load.injected, skipped=load.skipped)
    if cls.skills:
        echo(f"skills: injected {load.injected}, skipped {load.skipped}")
    run_agent(prompt, skill_block=load.block)
    return 0
```

(c) `run_agent` closure inside `main` — accept and forward `skill_block`. Current:
```python
    def run_agent(prompt):
        runner = MiniSweAgentRunner(model, env, agent_cfg=agent_cfg)
        try:
            for event in runner.run(prompt):
                emitter.write_renumbered(event)
```
Change to:
```python
    def run_agent(prompt, skill_block=""):
        runner = MiniSweAgentRunner(model, env, agent_cfg=agent_cfg)
        try:
            for event in runner.run(prompt, skill_block=skill_block):
                emitter.write_renumbered(event)
```

(d) `main` startup — build the catalog from disk and wire `load_skills`. Current router construction:
```python
    router = Router(complete, catalog=SKILL_CATALOG)
    try:
        rc = route_and_dispatch(
            args.task, router=router, emitter=emitter,
            make_chat_handler=lambda: ChatHandler(worker_model_id),
            run_agent=run_agent, ask_user=input, echo=print,
            worker_model_id=worker_model_id)
```
Change to:
```python
    skills_dir = REPO_ROOT / "skills"
    router = Router(complete, catalog=skills.load_catalog(skills_dir))
    try:
        rc = route_and_dispatch(
            args.task, router=router, emitter=emitter,
            make_chat_handler=lambda: ChatHandler(worker_model_id),
            run_agent=run_agent, ask_user=input, echo=print,
            worker_model_id=worker_model_id,
            load_skills=lambda names: skills.compose(skills_dir, names))
```

- [ ] **Step 4: Run to verify all pass**

Run: `.venv/bin/python -m pytest tests/test_run_traced.py -v`
Expected: PASS (test_4 with new ordering, test_11 new, test_9/test_10 unchanged-and-green, all others green).

- [ ] **Step 5: Commit**

```bash
git add trace/run_traced.py tests/test_run_traced.py
git commit -m "feat(run): wire skills — catalog at startup, compose + skill.load on dispatch"
```

---

## Task 6: Author skills + end-to-end demo

**Files:**
- Create: `skills/python-testing/SKILL.md`
- Create: `skills/git-pr-flow/SKILL.md` (stub)
- Create: `skills/poker-domain-rules/SKILL.md` (stub)

**Interfaces:**
- Consumes: everything from Tasks 1-5.
- Produces: a populated `skills/` dir so `load_catalog` returns a real catalog and an agent run can inject real content.

- [ ] **Step 1: Create the real skill `skills/python-testing/SKILL.md`**

```markdown
---
name: python-testing
description: Write and run pytest unit/integration tests
---
# Python Testing

When fixing or adding Python code in this repo:

- Run tests with `python -m pytest <path> -v`. Scope to a specific file when iterating.
- Write a failing test FIRST that reproduces the bug, then make it pass.
- Prefer one assertion per behavior; use `assert func(x) == expected` directly.
- After the fix, run the whole relevant test file to check for regressions.
```

- [ ] **Step 2: Create two stub skills (prove multi-entry scanning)**

`skills/git-pr-flow/SKILL.md`:
```markdown
---
name: git-pr-flow
description: Create branches, commits, and pull requests
---
# Git PR Flow

- Work on a feature branch, never directly on main.
- Make small, focused commits with conventional-commit messages.
```

`skills/poker-domain-rules/SKILL.md`:
```markdown
---
name: poker-domain-rules
description: Poker rake/rakeback math and PPPoker domain logic
---
# Poker Domain Rules

- Rake is taken from each pot up to a cap; rakeback returns a fraction to players.
- Validate seat counts and blind levels before computing payouts.
```

- [ ] **Step 3: Verify the catalog loads all three**

Run:
```bash
.venv/bin/python -c "import sys; sys.path[:0]=['upstream/src','.']; from pathlib import Path; from trace import skills; print(skills.load_catalog(Path('skills')))"
```
Expected: `[('git-pr-flow', '...'), ('poker-domain-rules', '...'), ('python-testing', '...')]` (sorted by name).

- [ ] **Step 4: End-to-end mock demo — agent runs with a skill injected**

The mock router can't classify, so verify injection through the agent path using a forced classification is out of scope for the mock; instead verify the wiring end-to-end with the real default task and confirm the events file records `skill.load`. Run:
```bash
./run.sh --model mock --task "Fix the failing test in examples/sample-repo so that add(2, 3) == 5."
```
Expected console: a `task.classified` line, then (if the mock-router selects no skills) a `skill.load` line with empty lists, then the agent run. The run completes and writes `events.jsonl` containing a `skill.load` event.

NOTE to implementer: the mock model is a deterministic tool-call model, not a classifier — the router still calls the real `complete` (VibeProxy) for classification even in `--model mock`. If VibeProxy is unavailable, this demo prints the router VibeProxy hint and exits 1; that is expected and not a failure of Task 6. The byte-level injection behavior is already proven by Tasks 2 and 5's unit tests. Record in the task report whether VibeProxy was reachable; if not, state that the unit tests carry the injection proof.

- [ ] **Step 5: Verify `skills/` is not gitignored, then commit**

```bash
git check-ignore skills/python-testing/SKILL.md || echo "not ignored — good"
git add skills/
git commit -m "feat(skills): author python-testing skill + git-pr-flow/poker stubs"
```

---

## Task 7: Full-suite green + learning-log update

**Files:**
- Modify: `docs/learning-log.md` (append a Phase 3 section)

- [ ] **Step 1: Run the entire project suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: PASS — all tests (Phase 0-2 originals + the new skills/injection tests). Record the count.

- [ ] **Step 2: Append a Phase 3 entry to `docs/learning-log.md`**

Add a section summarizing: what the knowledge layer does (selection vs. content vs. injection separation), the post-render injection trick (why bodies bypass Jinja), the skipped-and-shown failure model, and the `skill.load` event as the Phase-4 (CLI) pickup point. Keep it to ~15-25 lines, matching the existing log's voice.

- [ ] **Step 3: Commit**

```bash
git add docs/learning-log.md
git commit -m "docs: learning-log — Phase 3 knowledge/skills layer"
```

---

## Self-Review (completed by plan author)

**Spec coverage:** load_catalog/compose → Task 1; injection seam → Task 2; runner passthrough → Task 3; catalog ownership (remove SKILL_CATALOG, required param) → Task 4; wiring + skill.load + console + load_skills collaborator + test_4 ordering → Task 5; SKILL.md format + real skill → Task 6; full-suite green → Task 7. All spec error-table rows are exercised by Task 1's tests (missing file, malformed yaml, name mismatch, missing keys, non-utf8, empty selection, jinja-in-body, absent dir). The "existing tests that change" list (test_router.py, test_4, _spy_agent) maps to Tasks 4 and 5; test_9/test_10 verified-unchanged are not touched.

**Placeholder scan:** no TBD/TODO; every code step shows complete code. The two "NOTE to implementer" blocks (Task 3, Task 6) are scoping guidance with concrete fallbacks, not deferred work.

**Type consistency:** `SkillLoad(block, injected, skipped)`, `load_catalog(skills_dir)->list[tuple[str,str]]`, `compose(skills_dir, names)->SkillLoad`, `skill_block: str` keyword across TracingAgent/runner/run_agent, `load_skills: Callable[[list[str]], SkillLoad]` — names and signatures match across Tasks 1-6.
