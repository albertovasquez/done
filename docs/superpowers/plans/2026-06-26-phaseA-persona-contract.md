# Phase A — Persona / Workspace Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Read a persona workspace's identity-trio files (`SOUL.md`, `IDENTITY.md`, `USER.md`) and inject them into both the agent and chat dispatch paths, via a single resolution chokepoint, with an empty `default` persona that is a byte-identical no-op.

**Architecture:** A new `harness/persona.py` content module (mirrors `harness/skills.py`) reads the trio and composes one injectable block. A `compose_context()` resolver bundles persona+skill blocks into a `TurnContext` that every dispatch path consumes — so future engine consumers inherit persona without re-wiring. The persona string is composed once per session (cached on `SessionState`), injected fresh every turn at two sites: appended to `TracingAgent`'s system template (agent path) and prepended as a system message in `ChatHandler` (chat path).

**Tech Stack:** Python 3.11, pytest, dataclasses, pathlib. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-06-26-phaseA-persona-contract-design.md`

## Global Constraints

- **Tests live in `tests/` only.** Run: `.venv/bin/python -m pytest tests/ -q`.
- **Every test file starts with** these three lines (matches every existing test):
  ```python
  import sys
  sys.path.insert(0, "upstream/src")
  sys.path.insert(0, ".")
  ```
- **Zero upstream edits** — never modify anything under `upstream/`.
- **`SessionState.persona_block` default is `None`** (sentinel: `None`=not-composed, `""`=composed-empty). A `""` default silently disables persona loading. Constructor *param* defaults are `""` (empty-block = no-op) — a different thing; do not conflate.
- **`workspace_dir` defaults to `None`** in `HarnessAgent.__init__` and `build_harness_agent` → "no persona" → existing callers/tests pass unmodified.
- **The `persona_load` `_meta` event is GATED** on non-empty `injected`. The empty default emits nothing.
- **`MAX_FILE_CHARS = 8000`** per-file trim ceiling. **`PERSONA_FILES = ["SOUL.md", "IDENTITY.md", "USER.md"]`** (this order = injection order).
- **"Blank" means empty after `.strip()`** — whitespace-only files are skipped.
- Commit after each task with a `feat:`/`test:` conventional-commit message.

---

### Task 1: `harness/persona.py` — `PersonaLoad` + `compose_persona`

The content module that reads the trio and composes the block. Mirrors `harness/skills.py` discipline: per-file reads individually wrapped, never raises, returns `injected`/`skipped` telemetry.

**Files:**
- Create: `harness/persona.py`
- Test: `tests/test_persona.py`

**Interfaces:**
- Consumes: nothing (leaf module).
- Produces:
  - `PersonaLoad` dataclass: `block: str = ""`, `injected: list[str]`, `skipped: list[tuple[str, str]]`
  - `PERSONA_FILES: list[str]` = `["SOUL.md", "IDENTITY.md", "USER.md"]`
  - `MAX_FILE_CHARS: int` = `8000`
  - `compose_persona(workspace_dir: Path) -> PersonaLoad`
  - `_trim(text: str, limit: int) -> tuple[str, bool]` (text, was_trimmed)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_persona.py`:

```python
import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

from pathlib import Path
from harness.persona import PersonaLoad, compose_persona, MAX_FILE_CHARS


def _write(ws: Path, name: str, body: str):
    ws.mkdir(parents=True, exist_ok=True)
    (ws / name).write_text(body, encoding="utf-8")


def test_all_three_present_in_order(tmp_path):
    _write(tmp_path, "SOUL.md", "I am terse.")
    _write(tmp_path, "IDENTITY.md", "Name: Ada")
    _write(tmp_path, "USER.md", "User is Alberto.")
    load = compose_persona(tmp_path)
    assert load.injected == ["SOUL.md", "IDENTITY.md", "USER.md"]
    assert load.skipped == []
    # ordering: SOUL before IDENTITY before USER
    assert load.block.index("I am terse.") < load.block.index("Name: Ada") < load.block.index("User is Alberto.")
    assert "# Persona" in load.block


def test_partial_injects_present_only(tmp_path):
    _write(tmp_path, "SOUL.md", "Only soul.")
    load = compose_persona(tmp_path)
    assert load.injected == ["SOUL.md"]
    assert "Only soul." in load.block
    assert "IDENTITY.md" not in load.injected and "USER.md" not in load.injected


def test_absent_dir_is_empty_noraise(tmp_path):
    load = compose_persona(tmp_path / "does-not-exist")
    assert load == PersonaLoad()
    assert load.block == "" and load.injected == []


def test_empty_workspace_is_empty(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    load = compose_persona(tmp_path)
    assert load.block == "" and load.injected == []


def test_blank_file_skipped(tmp_path):
    _write(tmp_path, "SOUL.md", "")
    load = compose_persona(tmp_path)
    assert ("SOUL.md", "blank") in load.skipped
    assert load.injected == []
    assert load.block == ""


def test_whitespace_only_file_is_blank(tmp_path):
    _write(tmp_path, "SOUL.md", "   \n\t\n  ")
    load = compose_persona(tmp_path)
    assert ("SOUL.md", "blank") in load.skipped
    assert load.block == ""          # guards the byte-identical no-op


def test_oversized_file_trimmed(tmp_path):
    big = "x" * (MAX_FILE_CHARS + 500)
    _write(tmp_path, "SOUL.md", big)
    load = compose_persona(tmp_path)
    assert load.injected == ["SOUL.md"]
    assert "[truncated]" in load.block
    # body content capped at MAX_FILE_CHARS (marker excluded)
    assert load.block.count("x") <= MAX_FILE_CHARS


def test_non_utf8_file_skipped_not_raised(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "SOUL.md").write_bytes(b"\xff\xfe\x00bad")
    load = compose_persona(tmp_path)
    assert load.injected == []
    assert load.skipped and load.skipped[0][0] == "SOUL.md"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_persona.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'harness.persona'`

- [ ] **Step 3: Write `harness/persona.py`**

```python
"""Persona/workspace CONTENT layer: read a workspace's identity-trio files
(SOUL.md, IDENTITY.md, USER.md) and compose them into one injectable block.

Parallel to skills.py: this module only reads files and returns data. It never
injects (consumers do) and never selects which workspace (Phase C does). Every
per-file read is wrapped so one bad/missing file can never abort a turn.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

PERSONA_FILES = ["SOUL.md", "IDENTITY.md", "USER.md"]   # order = injection order
MAX_FILE_CHARS = 8000                                   # per-file trim ceiling


@dataclass
class PersonaLoad:
    block: str = ""
    injected: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (filename, reason)


def _trim(text: str, limit: int) -> tuple[str, bool]:
    """Cap text at `limit` chars. Returns (text, was_trimmed)."""
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def compose_persona(workspace_dir: Path) -> PersonaLoad:
    """Read the identity trio from `workspace_dir` and compose one block. Absent
    dir, missing files, and blank (whitespace-only) files yield an empty/partial
    block, never a raise. Oversized files are trimmed with a marker."""
    load = PersonaLoad()
    workspace_dir = Path(workspace_dir)
    if not workspace_dir.is_dir():           # absent workspace -> empty no-op
        return load
    sections: list[str] = []
    for name in PERSONA_FILES:
        path = workspace_dir / name
        try:
            raw = path.read_text(encoding="utf-8")   # OSError if missing, UnicodeDecodeError if binary
        except FileNotFoundError:
            continue                                  # missing file is silent (like skills)
        except (OSError, UnicodeDecodeError) as e:
            load.skipped.append((name, type(e).__name__))
            continue
        if not raw.strip():                           # blank == empty after strip
            load.skipped.append((name, "blank"))
            continue
        body, trimmed = _trim(raw, MAX_FILE_CHARS)
        if trimmed:
            body = body + "\n\n…[truncated]…"
        label = name[:-3].upper() if name.endswith(".md") else name   # "SOUL.md" -> "SOUL"
        sections.append(f"## {label}\n{body}")
        load.injected.append(name)
    if sections:
        load.block = ("\n\n# Persona\n\n"
                      "You are operating as the following persona. Honor it.\n\n"
                      + "\n\n".join(sections))
    return load
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_persona.py -q`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add harness/persona.py tests/test_persona.py
git commit -m "feat(persona): compose_persona reads the identity trio into one block"
```

---

### Task 2: `TurnContext` + `compose_context` resolver (the chokepoint)

The single resolver every dispatch path consumes. Bundles persona+skill blocks so a future consumer cannot ship persona-blind.

**Files:**
- Modify: `harness/persona.py` (add `TurnContext` + `compose_context`)
- Test: `tests/test_persona.py` (append)

**Interfaces:**
- Consumes: `compose_persona` (Task 1); `skills.compose(roots, names) -> SkillLoad` (existing, `harness/skills.py:63`).
- Produces:
  - `TurnContext` dataclass: `persona_block: str = ""`, `skill_block: str = ""`, `persona: PersonaLoad`, `skills: "skills.SkillLoad"`
  - `compose_context(workspace_dir: Path | None, skill_roots: list[Path], skill_names: list[str]) -> TurnContext`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_persona.py`:

```python
from harness.persona import TurnContext, compose_context
from harness import skills as _skills


def _write_skill(root: Path, name: str, body: str):
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\n{body}", encoding="utf-8")


def test_compose_context_bundles_persona_and_skills(tmp_path):
    ws = tmp_path / "ws"
    (ws).mkdir()
    (ws / "SOUL.md").write_text("Be terse.", encoding="utf-8")
    skroot = tmp_path / "sk"
    _write_skill(skroot, "tdd", "TDD body")
    ctx = compose_context(ws, [skroot], ["tdd"])
    assert "Be terse." in ctx.persona_block
    assert "TDD body" in ctx.skill_block
    assert ctx.persona.injected == ["SOUL.md"]
    assert ctx.skills.injected == ["tdd"]


def test_compose_context_none_workspace_is_persona_blank(tmp_path):
    skroot = tmp_path / "sk"
    _write_skill(skroot, "tdd", "TDD body")
    ctx = compose_context(None, [skroot], ["tdd"])
    assert ctx.persona_block == ""
    assert "TDD body" in ctx.skill_block      # skills still resolve with no persona


def test_compose_context_empty_everything(tmp_path):
    ctx = compose_context(None, [tmp_path], [])
    assert ctx == TurnContext()               # all-empty default
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_persona.py -k compose_context -q`
Expected: FAIL — `ImportError: cannot import name 'TurnContext'`

- [ ] **Step 3: Add to `harness/persona.py`**

At the top, add the skills import:
```python
from harness import skills
```

After `PersonaLoad`, add:
```python
@dataclass
class TurnContext:
    """The injectable context for one turn: persona (identity) + skills (task).
    The single object every dispatch path consumes so persona reaches all of
    them without per-site re-wiring."""
    persona_block: str = ""
    skill_block: str = ""
    persona: PersonaLoad = field(default_factory=PersonaLoad)
    skills: "skills.SkillLoad" = field(default_factory=lambda: skills.SkillLoad())


def compose_context(workspace_dir, skill_roots, skill_names) -> TurnContext:
    """Resolve persona + skills for one turn. `workspace_dir=None` => no persona
    (persona_block stays ""). Skills always resolve from skill_roots/skill_names."""
    persona = compose_persona(workspace_dir) if workspace_dir is not None else PersonaLoad()
    skill_load = skills.compose(skill_roots, skill_names)
    return TurnContext(persona_block=persona.block, skill_block=skill_load.block,
                       persona=persona, skills=skill_load)
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_persona.py -q`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add harness/persona.py tests/test_persona.py
git commit -m "feat(persona): compose_context resolver bundles persona+skills (the chokepoint)"
```

---

### Task 3: `paths.default_workspace_dir()`

Resolve the default persona workspace through the asset-resolution single source of truth.

**Files:**
- Modify: `harness/paths.py` (add one function after `skills_dirs`, ~line 49)
- Test: `tests/test_paths.py` (append)

**Interfaces:**
- Consumes: `paths.config_dir()` (existing).
- Produces: `paths.default_workspace_dir() -> Path` = `config_dir() / "agents" / "default"`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_paths.py`:

```python
def test_default_workspace_dir_under_config(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert paths.default_workspace_dir() == tmp_path / "harness" / "agents" / "default"


def test_default_workspace_dir_does_not_create(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    d = paths.default_workspace_dir()
    assert not d.exists()
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_paths.py -k default_workspace -q`
Expected: FAIL — `AttributeError: module 'harness.paths' has no attribute 'default_workspace_dir'`

- [ ] **Step 3: Add to `harness/paths.py`** (after `skills_dirs`):

```python
def default_workspace_dir() -> Path:
    """The built-in 'default' persona workspace at config_dir()/agents/default/.
    Does NOT create the directory; an absent dir is a valid empty persona."""
    return config_dir() / "agents" / "default"
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_paths.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add harness/paths.py tests/test_paths.py
git commit -m "feat(paths): default_workspace_dir() under the XDG config dir"
```

---

### Task 4: `TracingAgent` accepts and injects `persona_block`

Append persona to the system template, after the base and before skills.

**Files:**
- Modify: `harness/tracing_agent.py` (`__init__` ~line 35, `_render_template` ~line 41)
- Test: `tests/test_tracing_agent_skills.py` (append; reuse its `_agent` helper pattern)

**Interfaces:**
- Consumes: nothing new.
- Produces: `TracingAgent(model, env, *, emitter, skill_block="", persona_block="", **kwargs)` — new keyword `persona_block`, default `""`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tracing_agent_skills.py`:

```python
def _agent_p(tmp_path, *, persona_block="", skill_block=""):
    em = Emitter(tmp_path / "e2.jsonl", clock=lambda: 0.0, console=False)
    return TracingAgent(
        build_mock_model(), LocalEnvironment(cwd=str(tmp_path)), emitter=em,
        persona_block=persona_block, skill_block=skill_block,
        system_template="SYS BASE", instance_template="INST {{task}}")


def test_persona_block_appended_after_base_before_skills(tmp_path):
    a = _agent_p(tmp_path, persona_block="\n\nPERSONA", skill_block="\n\nSKILLS")
    rendered = a._render_template(a.config.system_template)
    assert rendered == "SYS BASE\n\nPERSONA\n\nSKILLS"   # base -> persona -> skills
    # instance template gets neither
    a.extra_template_vars = {"task": "t"}
    inst = a._render_template(a.config.instance_template)
    assert "PERSONA" not in inst and "SKILLS" not in inst


def test_empty_persona_block_is_byte_identical(tmp_path):
    a = _agent_p(tmp_path, persona_block="", skill_block="")
    assert a._render_template(a.config.system_template) == "SYS BASE"


def test_persona_block_jinja_is_literal(tmp_path):
    a = _agent_p(tmp_path, persona_block="\n\n{{ undefined }}")
    assert a._render_template(a.config.system_template) == "SYS BASE\n\n{{ undefined }}"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_tracing_agent_skills.py -k persona -q`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'persona_block'`

- [ ] **Step 3: Modify `harness/tracing_agent.py`**

Change `__init__` (line 35) from:
```python
    def __init__(self, model, env, *, emitter: Emitter, skill_block: str = "", **kwargs):
        super().__init__(model, env, **kwargs)
        self._emitter = emitter
        self._skill_block = skill_block
```
to:
```python
    def __init__(self, model, env, *, emitter: Emitter, skill_block: str = "",
                 persona_block: str = "", **kwargs):
        super().__init__(model, env, **kwargs)
        self._emitter = emitter
        self._skill_block = skill_block
        self._persona_block = persona_block
```

Change `_render_template` (lines 45-48) from:
```python
        out = super()._render_template(template)
        if self._skill_block and template is self.config.system_template:
            out += self._skill_block
        return out
```
to:
```python
        out = super()._render_template(template)
        if template is self.config.system_template:
            if self._persona_block:
                out += self._persona_block      # identity, before task skills
            if self._skill_block:
                out += self._skill_block
        return out
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_tracing_agent_skills.py -q`
Expected: PASS (existing 3 + new 3 = 6)

- [ ] **Step 5: Commit**

```bash
git add harness/tracing_agent.py tests/test_tracing_agent_skills.py
git commit -m "feat(tracing): inject persona_block into system template before skills"
```

---

### Task 5: `ChatHandler` accepts and prepends `persona_block`

Add the system message the chat path lacks today — only when non-empty, every turn.

**Files:**
- Modify: `harness/chat_handler.py` (`__init__` ~line 52, `answer_stream` ~line 78)
- Test: `tests/test_chat_handler.py` (append + update one existing assertion)

**Interfaces:**
- Consumes: nothing new.
- Produces: `ChatHandler(worker_model_id, catalog=None, persona_block="")` — new keyword `persona_block`, default `""`.

- [ ] **Step 1: Write the failing tests + update the existing baseline assertion**

In `tests/test_chat_handler.py`, the existing `test_real_mode_streams_pieces_in_order_with_stream_true` asserts (line 55):
```python
    assert captured["messages"] == [{"role": "user", "content": "hi"}]
```
This still holds for a `ChatHandler` with no persona — leave it unchanged (it constructs `ChatHandler("gpt-5.4")`, persona defaults to `""`).

Append new tests:

```python
def test_persona_block_prepended_as_system_message(monkeypatch):
    captured = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return iter([_Chunk("ok")])

    import litellm
    monkeypatch.setattr(litellm, "completion", fake_completion)

    list(ChatHandler("gpt-5.4", persona_block="BE TERSE").answer_stream("hi"))
    assert captured["messages"] == [
        {"role": "system", "content": "BE TERSE"},
        {"role": "user", "content": "hi"},
    ]


def test_persona_block_prepended_before_history(monkeypatch):
    captured = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return iter([_Chunk("ok")])

    import litellm
    monkeypatch.setattr(litellm, "completion", fake_completion)

    history = [{"role": "user", "content": "earlier"}]
    list(ChatHandler("gpt-5.4", persona_block="BE TERSE").answer_stream("hi", history=history))
    assert captured["messages"] == [
        {"role": "system", "content": "BE TERSE"},
        {"role": "user", "content": "earlier"},
        {"role": "user", "content": "hi"},
    ]


def test_empty_persona_block_adds_no_system_message(monkeypatch):
    captured = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return iter([_Chunk("ok")])

    import litellm
    monkeypatch.setattr(litellm, "completion", fake_completion)

    list(ChatHandler("gpt-5.4", persona_block="").answer_stream("hi"))
    assert captured["messages"] == [{"role": "user", "content": "hi"}]   # byte-identical
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_chat_handler.py -k persona -q`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'persona_block'`

- [ ] **Step 3: Modify `harness/chat_handler.py`**

Change `__init__` (lines 52-58) from:
```python
    def __init__(self, worker_model_id: str | None,
                 catalog: list[tuple[str, str]] | None = None):
        # None => mock mode (no chat-capable model available)
        self._model_id = worker_model_id
        # The skill catalog (name, description) — used to answer capability
        # questions from data instead of the model. Empty/None => not available.
        self._catalog = catalog or []
```
to:
```python
    def __init__(self, worker_model_id: str | None,
                 catalog: list[tuple[str, str]] | None = None,
                 persona_block: str = ""):
        # None => mock mode (no chat-capable model available)
        self._model_id = worker_model_id
        # The skill catalog (name, description) — used to answer capability
        # questions from data instead of the model. Empty/None => not available.
        self._catalog = catalog or []
        # Persona context (identity trio). Prepended as a system message on every
        # turn when non-empty; "" => no system message (byte-identical to before).
        self._persona_block = persona_block
```

Change the `messages=` line in `answer_stream` (line 78) from:
```python
            messages=(history or []) + [{"role": "user", "content": prompt}],
```
to:
```python
            messages=(([{"role": "system", "content": self._persona_block}]
                       if self._persona_block else [])
                      + (history or []) + [{"role": "user", "content": prompt}]),
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_chat_handler.py -q`
Expected: PASS (existing + 3 new)

- [ ] **Step 5: Commit**

```bash
git add harness/chat_handler.py tests/test_chat_handler.py
git commit -m "feat(chat): prepend persona_block as a system message when non-empty"
```

---

### Task 6: `SessionState.persona_block` field

The per-session cache. Default `None` (sentinel).

**Files:**
- Modify: `harness/acp_session.py` (`SessionState` dataclass ~line 11)
- Test: `tests/test_acp_session.py` (append)

**Interfaces:**
- Consumes: nothing.
- Produces: `SessionState.persona_block: str | None = None`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_acp_session.py`:

```python
def test_session_state_persona_block_defaults_none():
    from harness.acp_session import SessionState
    s = SessionState(cwd="/tmp")
    assert s.persona_block is None      # sentinel: not-yet-composed (NOT "")
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_acp_session.py -k persona -q`
Expected: FAIL — `AssertionError` (attribute missing → AttributeError, or default wrong)

- [ ] **Step 3: Modify `harness/acp_session.py`**

In the `SessionState` dataclass (after the `transcript` field, line 16), add:
```python
    persona_block: str | None = None  # None = not-yet-composed; "" = composed-empty
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_acp_session.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add harness/acp_session.py tests/test_acp_session.py
git commit -m "feat(session): add persona_block cache field (None sentinel)"
```

---

### Task 7: Wire persona into `HarnessAgent` + the factory

`HarnessAgent` gains `workspace_dir` (default `None` = no persona); composes once per session, emits the gated event, threads `persona_block` into both paths.

**Files:**
- Modify: `harness/acp_agent.py` (`__init__` ~line 30, `prompt()` ~line 98, `_run_agent_turn` call ~line 166, `build_harness_agent` ~line 288)
- Test: `tests/test_acp_session_context.py` (append integration tests)

**Interfaces:**
- Consumes: `persona.compose_persona` (Task 1), `SessionState.persona_block` (Task 6), `TracingAgent(persona_block=…)` (Task 4), `ChatHandler(persona_block=…)` (Task 5).
- Produces: `HarnessAgent(..., workspace_dir: Path | None = None)`; `build_harness_agent(..., workspace_dir=None)`.

- [ ] **Step 1: Write the failing integration tests**

Append to `tests/test_acp_session_context.py`. This reuses the file's REAL helpers (verified): `_ScriptedRouter([...])`, `_chat()`, `_build(router, worker_model_id=...)`, and `_prompt(agent, sid, text)` (which calls `asyncio.run` internally — do NOT wrap it again). The chat path uses litellm when `worker_model_id` is a real id, so monkeypatch `litellm.completion` to capture the messages. Sessions are created via `asyncio.run(agent.new_session(cwd="."))`.

```python
def test_persona_reaches_chat_path(tmp_path, monkeypatch):
    # workspace with a SOUL.md -> chat path must carry it as a system message
    ws = tmp_path / "ws"; ws.mkdir()
    (ws / "SOUL.md").write_text("BE TERSE", encoding="utf-8")

    captured = {}
    def fake_completion(**kwargs):
        captured.update(kwargs)
        return iter([])                       # empty stream -> empty answer, fine
    import litellm
    monkeypatch.setattr(litellm, "completion", fake_completion)

    agent = _build(_ScriptedRouter([_chat()]), worker_model_id="gpt-5.4")
    agent._workspace_dir = ws                 # inject the test workspace
    sid = asyncio.run(agent.new_session(cwd="."))
    _prompt(agent, sid, "hi")                 # _prompt already runs asyncio.run
    sysmsg = captured["messages"][0]
    assert sysmsg["role"] == "system"
    assert "BE TERSE" in sysmsg["content"]
    assert sysmsg["content"] == agent._store.get(sid).persona_block


def test_empty_workspace_is_byte_identical_chat(tmp_path, monkeypatch):
    captured = {}
    def fake_completion(**kwargs):
        captured.update(kwargs)
        return iter([])
    import litellm
    monkeypatch.setattr(litellm, "completion", fake_completion)

    agent = _build(_ScriptedRouter([_chat()]), worker_model_id="gpt-5.4")
    agent._workspace_dir = tmp_path / "absent"    # no persona (absent dir)
    sid = asyncio.run(agent.new_session(cwd="."))
    _prompt(agent, sid, "hi")
    assert captured["messages"] == [{"role": "user", "content": "hi"}]   # no system msg


def test_persona_load_event_gated_off_for_empty_workspace(tmp_path):
    # empty/absent workspace -> NO persona_load _meta event on the conn
    agent = _build(_ScriptedRouter([_chat()]), worker_model_id=None)  # mock chat line
    agent._workspace_dir = tmp_path / "absent"
    sid = asyncio.run(agent.new_session(cwd="."))
    _prompt(agent, sid, "hi")
    metas = [u for u in agent._conn.updates
             if isinstance(getattr(u, "meta", None) or getattr(u, "field_meta", None), dict)]
    assert not any("persona_load" in str(getattr(u, "meta", "") or getattr(u, "field_meta", ""))
                   for u in agent._conn.updates)
```

> **NOTE for the implementer:** the gated-event assertion inspects `_FakeConn.updates` for a `persona_load` key in the update's `_meta`. The exact attribute name on the update object is whatever `with_meta()` sets (check `harness/acp_emit.py`); adjust the attribute access to match — the intent is "no persona_load meta was emitted." A positive counterpart (non-empty workspace emits exactly one) is optional but recommended.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_acp_session_context.py -k persona -q`
Expected: FAIL — `AttributeError: 'HarnessAgent' object has no attribute '_workspace_dir'`

- [ ] **Step 3: Modify `harness/acp_agent.py`**

Add the import near the top (after `from harness import skills`):
```python
from harness import persona
```

In `HarnessAgent.__init__` (line 30), add `workspace_dir` to the signature and store it:
```python
    def __init__(self, *, model_factory, agent_cfg, skills_dir: list[Path], router: Router,
                 worker_model_id, yolo: bool = False, backend: str = "vibeproxy",
                 workspace_dir: Path | None = None):
```
and in the body (after `self._skills_dir = skills_dir`):
```python
        self._workspace_dir = workspace_dir     # None => no persona (byte-identical)
```

In `prompt()`, after `transcript = state.transcript` (line 107), add the compose-once block:
```python
        # Persona: compose once per session (cached). None => not-yet-read.
        if state.persona_block is None:
            pload = await loop.run_in_executor(
                None, persona.compose_persona, self._workspace_dir) \
                if self._workspace_dir is not None else None
            state.persona_block = pload.block if pload else ""
            if pload and pload.injected:                 # GATED: empty default emits nothing
                await self._conn.session_update(session_id,
                    with_meta(message_chunk(""),
                              {"persona_load": {"injected": pload.injected,
                                                "skipped": pload.skipped}}))
```

In the `chat_question` branch, change the `ChatHandler(...)` construction (line 137) from:
```python
            handler = ChatHandler(self._worker_model_id, catalog=self._router.catalog)
```
to:
```python
            handler = ChatHandler(self._worker_model_id, catalog=self._router.catalog,
                                  persona_block=state.persona_block or "")
```

In the agent path, change the `_run_agent_turn` call (line 166) to pass the persona block through. Update the helper signature (line 176) from:
```python
    async def _run_agent_turn(self, loop, session_id, state, text, skill_block, prior) -> dict:
```
to:
```python
    async def _run_agent_turn(self, loop, session_id, state, text, skill_block, prior,
                              persona_block="") -> dict:
```
update the call site (line 166):
```python
        engine = await self._run_agent_turn(loop, session_id, state, text, load.block,
                                            transcript, state.persona_block or "")
```
and in `run_engine` (line 270), pass it into the `TracingAgent`:
```python
                agent = TracingAgent(self._model_factory(self._worker_model_id), env,
                                     emitter=emitter, skill_block=skill_block,
                                     persona_block=persona_block, **cfg)
```

Finally, update `build_harness_agent` (line 288) to accept and forward `workspace_dir`:
```python
def build_harness_agent(*, model_factory, agent_cfg, skills_dir: list[Path],
                        router: Router, worker_model_id=None,
                        workspace_dir: Path | None = None) -> HarnessAgent:
    """Factory: wire the agent from resolved dependencies."""
    return HarnessAgent(
        model_factory=model_factory,
        agent_cfg=agent_cfg,
        skills_dir=skills_dir,
        router=router,
        worker_model_id=worker_model_id,
        workspace_dir=workspace_dir,
    )
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_acp_session_context.py -q`
Expected: PASS (existing + 2 new). The existing `_build` call passes no `workspace_dir` → defaults to `None` → unchanged behavior.

- [ ] **Step 5: Commit**

```bash
git add harness/acp_agent.py tests/test_acp_session_context.py
git commit -m "feat(agent): compose persona once per session, inject into both paths, gated event"
```

---

### Task 8: Wire the default workspace at the ACP entrypoint

`acp_main.py` resolves the default workspace and passes it to `HarnessAgent`.

**Files:**
- Modify: `harness/acp_main.py` (`HarnessAgent(...)` construction ~line 93)
- Test: covered by the full suite + an entrypoint assertion in `tests/test_acp_session_context.py` is not needed; assert via `tests/test_paths.py` already. Add one wiring test in `tests/test_acp_agent.py`.

**Interfaces:**
- Consumes: `paths.default_workspace_dir()` (Task 3).
- Produces: a `HarnessAgent` whose `_workspace_dir` is the default workspace.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_acp_agent.py`:

```python
def test_acp_main_wires_default_workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("HARNESS_ROUTER_STUB", "1")
    import asyncio
    from harness import acp_agent, paths

    captured = {}
    real_init = acp_agent.HarnessAgent.__init__
    def spy_init(self, **kw):
        captured.update(kw)
        real_init(self, **kw)
    monkeypatch.setattr(acp_agent.HarnessAgent, "__init__", spy_init)

    # run _main far enough to construct the agent, then stop at run_agent
    monkeypatch.setattr("acp.run_agent", lambda agent: asyncio.sleep(0))
    from harness import acp_main
    asyncio.run(acp_main._main(["--model", "mock"]))
    assert captured["workspace_dir"] == paths.default_workspace_dir()
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_acp_agent.py -k default_workspace -q`
Expected: FAIL — `KeyError: 'workspace_dir'` (entrypoint doesn't pass it yet)

- [ ] **Step 3: Modify `harness/acp_main.py`**

In `_main`, change the `HarnessAgent(...)` construction (lines 93-101) to add the workspace:
```python
    agent = HarnessAgent(
        model_factory=_model_factory(args.model),
        agent_cfg=_load_agent_cfg(),
        skills_dir=roots,
        router=Router(complete_fn, catalog=skills.load_catalog(roots)),
        worker_model_id=worker_model_id,
        yolo=args.yolo,
        backend=args.model,
        workspace_dir=paths.default_workspace_dir(),
    )
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_acp_agent.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add harness/acp_main.py tests/test_acp_agent.py
git commit -m "feat(acp-main): wire the default persona workspace into the agent"
```

---

### Task 9: Route persona through the non-ACP dev path (`run_traced` / `runner`)

Prove the chokepoint generalizes: the Phase-0 dev entrypoint resolves persona through `compose_context` too, so it is not silently persona-blind. **Per spec §7: if this proves heavier than expected, stop and flag — it is the one task safe to split to a fast-follow.**

**Files:**
- Modify: `harness/runner.py` (`MiniSweAgentRunner.run` ~line 83)
- Modify: `harness/run_traced.py` (`run_agent` ~line 125)
- Test: `tests/test_runner.py` (append)

**Interfaces:**
- Consumes: `TracingAgent(persona_block=…)` (Task 4).
- Produces: `MiniSweAgentRunner.run(task, *, skill_block="", persona_block="", **kwargs)`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_runner.py`:

```python
def test_runner_passes_persona_block_to_agent(tmp_path, monkeypatch):
    from harness.runner import MiniSweAgentRunner
    from harness.models_mock import build_mock_model
    from minisweagent.environments.local import LocalEnvironment
    import yaml
    from pathlib import Path

    captured = {}
    import harness.runner as rmod
    real = rmod.TracingAgent
    def spy(*args, **kwargs):
        captured.update(kwargs)
        return real(*args, **kwargs)
    monkeypatch.setattr(rmod, "TracingAgent", spy)

    cfg = yaml.safe_load(Path("upstream/src/minisweagent/config/mini.yaml").read_text())["agent"]
    runner = MiniSweAgentRunner(build_mock_model(), LocalEnvironment(cwd=str(tmp_path)), agent_cfg=cfg)
    list(runner.run("do a thing", skill_block="\n\nSK", persona_block="\n\nPER"))
    assert captured.get("persona_block") == "\n\nPER"
    assert captured.get("skill_block") == "\n\nSK"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_runner.py -k persona -q`
Expected: FAIL — `run()` rejects `persona_block` (TypeError) or doesn't forward it.

- [ ] **Step 3: Modify `harness/runner.py`**

Change `MiniSweAgentRunner.run` (line 83) from:
```python
    def run(self, task: str, *, skill_block: str = "", **kwargs) -> Iterator[Event]:
        q: "queue.Queue[Any]" = queue.Queue()
        emitter = QueueEmitter(q, clock=lambda: 0.0)
        agent = TracingAgent(self._model, self._env, emitter=emitter,
                             skill_block=skill_block, **self._agent_cfg)
```
to:
```python
    def run(self, task: str, *, skill_block: str = "", persona_block: str = "",
            **kwargs) -> Iterator[Event]:
        q: "queue.Queue[Any]" = queue.Queue()
        emitter = QueueEmitter(q, clock=lambda: 0.0)
        agent = TracingAgent(self._model, self._env, emitter=emitter,
                             skill_block=skill_block, persona_block=persona_block,
                             **self._agent_cfg)
```

- [ ] **Step 4: Modify `harness/run_traced.py`** so the dev CLI resolves persona

Add an import (after line 31, `from harness import skills`):
```python
from harness import persona as _persona  # noqa: E402
from harness import paths as _paths_persona  # noqa: E402
```

Change `run_agent` (lines 125-128) signature and body from:
```python
    def run_agent(prompt, skill_block=""):
        runner = MiniSweAgentRunner(model, env, agent_cfg=agent_cfg)
        try:
            for event in runner.run(prompt, skill_block=skill_block):
```
to:
```python
    def run_agent(prompt, skill_block=""):
        persona_block = _persona.compose_persona(
            _paths_persona.default_workspace_dir()).block
        runner = MiniSweAgentRunner(model, env, agent_cfg=agent_cfg)
        try:
            for event in runner.run(prompt, skill_block=skill_block,
                                    persona_block=persona_block):
```

- [ ] **Step 5: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_runner.py tests/test_run_traced.py -q`
Expected: PASS (existing + 1 new; `run_traced` tests unaffected because the default workspace is absent → `persona_block=""`)

- [ ] **Step 6: Commit**

```bash
git add harness/runner.py harness/run_traced.py tests/test_runner.py
git commit -m "feat(runner): route persona_block through the non-ACP dev path"
```

---

### Task 10: Full-suite green + scope gate

**Files:** none (verification only).

- [ ] **Step 1: Run the whole suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS — all existing tests + the new ones. No regressions.

- [ ] **Step 2: Scope gate (review-checklist, not a test)**

Run: `git diff --stat main...HEAD`
Confirm only these files changed: `harness/persona.py`, `harness/paths.py`, `harness/acp_session.py`, `harness/acp_agent.py`, `harness/tracing_agent.py`, `harness/chat_handler.py`, `harness/acp_main.py`, `harness/runner.py`, `harness/run_traced.py`, and the matching `tests/` files (+ the two spec/plan docs).

Run: `grep -nE "import (toml|tomllib)|memory|cron|persona\.toml|HEARTBEAT|BOOTSTRAP" harness/persona.py`
Expected: no matches (no out-of-scope concepts leaked in).

- [ ] **Step 3: Commit (if any doc/checklist touch-ups)**

```bash
git add -A
git commit -m "chore(persona): Phase A complete — full suite green, scope held" --allow-empty
```

---

## Self-Review

**Spec coverage** (against `2026-06-26-phaseA-persona-contract-design.md`):

- §3.1 `persona.py` / `compose_persona` / `_trim` / absent-dir guard / blank-after-strip / trim → **Task 1** ✓
- §3.2 `TurnContext` + `compose_context` chokepoint → **Task 2** ✓
- §3.2 agent-path injection (base→persona→skills, identity-match) → **Task 4** ✓
- §3.2 chat-path injection (system message, non-empty, every turn) → **Task 5** ✓
- §3.2 gated `persona_load` event → **Task 7** (compose-once block) ✓
- §3.3 `SessionState.persona_block: str | None = None` → **Task 6** ✓
- §3.4 `default_workspace_dir()` + absent-dir no-op + wiring → **Tasks 3, 8** ✓
- §5 unit tests (trio/partial/absent/blank/whitespace/oversized/non-utf8) → **Task 1** ✓
- §5 injection-reach regression (both paths) → **Tasks 4, 5, 7** ✓
- §5 chokepoint coverage → **Task 2** ✓
- §5 gated-event + no-op → **Tasks 5, 7** ✓
- §7 all five construction sites (incl. `build_harness_agent`, `run_traced`/`runner`) → **Tasks 7, 9** ✓
- §8.6 scope gate → **Task 10** ✓

No gaps.

**Placeholder scan:** every code step shows complete code. The one soft spot is Task 7 Step 1's note about reusing/adding `_chat_router`/`_new`/`_prompt` helpers — this is intentional (the file's existing harness must be matched, not invented) and is flagged explicitly, not left as "TODO".

**Type consistency:** `persona_block` is the parameter name everywhere (TracingAgent, ChatHandler, runner, `_run_agent_turn`). `compose_persona(workspace_dir)`, `compose_context(workspace_dir, skill_roots, skill_names)`, `default_workspace_dir()`, `PersonaLoad`, `TurnContext`, `PERSONA_FILES`, `MAX_FILE_CHARS` are used identically in every task that references them.
