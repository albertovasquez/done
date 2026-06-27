# Persona Phase B — Memory + Isolation Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give a persona persistent memory (`MEMORY.md` + daily notes) read on session start and injected through the existing chokepoint, written by the agent via a prompt-injected shell protocol — plus a per-session workspace dimension on `SessionStore`/`SessionState` (the isolation core pulled forward from Phase C).

**Architecture:** A new `harness/memory.py` mirrors `persona.py`: `resolve_memory(workspace_dir, *, today)` reads the memory files into one content-gated block (protocol preamble + file sections). Memory threads through `compose_context` to both dispatch paths (agent system template + chat system message), resolved once per session and cached on `SessionState`, with a gated `memory_load` telemetry event — all mirroring the Phase A persona machinery. The byte-identical no-op is preserved by content-gating (empty memory → empty block → no change).

**Tech Stack:** Python 3.11, pytest, dataclasses, pathlib, datetime. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-06-26-persona-phaseB-memory-design.md`

## Global Constraints

- **Tests in `tests/` only.** Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/ -q` from the worktree root (no local `.venv`; use the primary checkout's interpreter at that absolute path).
- Every test file starts with exactly:
  ```python
  import sys
  sys.path.insert(0, "upstream/src")
  sys.path.insert(0, ".")
  ```
- **Zero upstream edits.**
- **CONTENT-GATED injection:** memory (protocol preamble + block) injects **iff at least one memory file has real content** (`injected` non-empty) — exactly like persona/skills. An absent OR present-but-empty/inert workspace produces `block == ""`. This preserves the Phase A byte-identical no-op (the seeded default install must stay unchanged).
- **No ambient clock:** `resolve_memory(workspace_dir, *, today: date)` takes `today` as a param; the caller computes it once per session at session start in local time (`yesterday = today - timedelta(days=1)`).
- **Memory reuses `persona._meaningful` and `persona._trim`** (promoted to importable in Task 1). Blank/comment-only files skip; oversized files trim with the `…[truncated]…` marker.
- **Gated `memory_load` event:** emitted iff `injected` non-empty, after `task_classified`, only on personalized turns (not clarify/ambiguous), once per session — mirrors `persona_load` exactly.
- **System-prompt order:** base → persona → memory → skills.
- `MAX_MEMORY_CHARS = 8000`. `MEMORY_FILE = "MEMORY.md"`, `MEMORY_DIR = "memory"`.
- Commit after each task with a `feat:`/`test:` conventional message.

---

### Task 1: Promote `_meaningful` / `_trim` to importable helpers

`harness/memory.py` reuses them. Today they're private in `persona.py`. Make them importable without behavior change.

**Files:**
- Modify: `harness/persona.py`
- Test: `tests/test_persona.py` (append a tiny import-smoke test)

**Interfaces:**
- Produces: `persona._meaningful`, `persona._trim`, `persona._HTML_COMMENT` remain importable (they already exist; this task only LOCKS them as a reuse surface — no rename, no behavior change).

- [ ] **Step 1: Write the import-smoke test**

Append to `tests/test_persona.py`:
```python
def test_meaningful_and_trim_are_importable_helpers():
    # Phase B's memory module reuses these; lock them as a stable import surface.
    from harness.persona import _meaningful, _trim
    assert _meaningful("real text") is True
    assert _meaningful("<!-- only a comment -->") is False
    body, trimmed = _trim("x" * 10, 4)
    assert body == "xxxx" and trimmed is True
```

- [ ] **Step 2: Run to verify it passes** (they already exist)

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_persona.py -k importable -q`
Expected: PASS — `_meaningful`/`_trim` already exist; this just locks them.

(If it fails to import, the helpers were renamed — STOP, they must stay named `_meaningful`/`_trim`.)

- [ ] **Step 3: Commit**

```bash
git add tests/test_persona.py
git commit -m "test(persona): lock _meaningful/_trim as a reuse surface for memory"
```

---

### Task 2: `harness/memory.py` — `resolve_memory`

The content module. Reads MEMORY.md + today + yesterday into one content-gated block.

**Files:**
- Create: `harness/memory.py`
- Test: `tests/test_memory.py`

**Interfaces:**
- Consumes: `persona._meaningful`, `persona._trim` (Task 1).
- Produces:
  - `MemoryLoad` dataclass: `block: str = ""`, `injected: list[str]`, `skipped: list[tuple[str,str]]`
  - `MEMORY_FILE = "MEMORY.md"`, `MEMORY_DIR = "memory"`, `MAX_MEMORY_CHARS = 8000`
  - `MEMORY_PROTOCOL: str` (the preamble constant — takes a workspace path)
  - `resolve_memory(workspace_dir: Path | None, *, today: date) -> MemoryLoad`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_memory.py`:
```python
import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

from datetime import date
from pathlib import Path
from harness.memory import MemoryLoad, resolve_memory, MAX_MEMORY_CHARS

TODAY = date(2026, 6, 26)        # yesterday = 2026-06-25


def _write(ws: Path, rel: str, body: str):
    p = ws / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def test_absent_workspace_is_empty(tmp_path):
    load = resolve_memory(tmp_path / "nope", today=TODAY)
    assert load == MemoryLoad()
    assert load.block == "" and load.injected == []


def test_present_but_empty_is_content_gated_noop(tmp_path):
    # workspace EXISTS but has no memory content -> still empty block (no protocol).
    tmp_path.mkdir(exist_ok=True)
    load = resolve_memory(tmp_path, today=TODAY)
    assert load.block == "" and load.injected == []


def test_comment_only_memory_is_skipped(tmp_path):
    _write(tmp_path, "MEMORY.md", "<!-- nothing yet -->")
    load = resolve_memory(tmp_path, today=TODAY)
    assert load.block == "" and load.injected == []


def test_durable_memory_injects_with_protocol(tmp_path):
    _write(tmp_path, "MEMORY.md", "Prefers terse answers.")
    load = resolve_memory(tmp_path, today=TODAY)
    assert load.injected == ["MEMORY.md"]
    assert "Prefers terse answers." in load.block
    assert "# Memory" in load.block
    # the protocol preamble is present (it teaches the write protocol)
    assert "read" in load.block.lower() and "append" in load.block.lower()
    # protocol uses the absolute, quoted workspace path
    assert str(tmp_path) in load.block


def test_daily_files_today_and_yesterday(tmp_path):
    _write(tmp_path, "memory/2026-06-26.md", "TODAY note")
    _write(tmp_path, "memory/2026-06-25.md", "YESTERDAY note")
    _write(tmp_path, "memory/2026-06-24.md", "OLD note")   # must NOT be read
    load = resolve_memory(tmp_path, today=TODAY)
    assert "TODAY note" in load.block
    assert "YESTERDAY note" in load.block
    assert "OLD note" not in load.block


def test_oversized_memory_trimmed(tmp_path):
    _write(tmp_path, "MEMORY.md", "y" * (MAX_MEMORY_CHARS + 500))
    load = resolve_memory(tmp_path, today=TODAY)
    assert "…[truncated]…" in load.block
    assert load.block.count("y") == MAX_MEMORY_CHARS


def test_non_utf8_memory_skipped_not_raised(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "MEMORY.md").write_bytes(b"\xff\xfe\x00bad")
    load = resolve_memory(tmp_path, today=TODAY)
    assert load.injected == []
    assert load.skipped and load.skipped[0][0] == "MEMORY.md"
```

- [ ] **Step 2: Run to verify failure**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_memory.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'harness.memory'`

- [ ] **Step 3: Write `harness/memory.py`**

```python
"""Persona MEMORY content layer: read a workspace's memory files (MEMORY.md +
memory/<today>.md + memory/<yesterday>.md) into one injectable block.

Parallel to persona.py; reuses its _meaningful/_trim discipline. The block is
CONTENT-GATED: it is empty unless at least one memory file has real content, so a
seeded-but-unused default persona stays byte-identical (the Phase A no-op). When
non-empty, the block carries a protocol preamble teaching the agent how to write
to its memory via plain shell.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from harness.persona import _meaningful, _trim

MEMORY_FILE = "MEMORY.md"
MEMORY_DIR = "memory"
MAX_MEMORY_CHARS = 8000


@dataclass
class MemoryLoad:
    block: str = ""
    injected: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)


def _protocol(workspace: Path) -> str:
    """The write-protocol preamble, with absolute, double-quoted paths (the
    workspace is under the XDG/home config dir and may contain spaces)."""
    ws = str(workspace)
    mem = f'{ws}/{MEMORY_DIR}'
    return (
        "You have a persistent memory in this workspace; its files appear above "
        "(when present). To record something worth remembering:\n"
        f'1. ensure the dir exists: `mkdir -p "{mem}"`\n'
        '2. read before writing: `test -f "<file>" && cat "<file>"`\n'
        "3. append a concrete entry: `printf '%s\\n' \"...\" >> \"<file>\"`\n"
        "Write only real updates — decisions, preferences, constraints, open "
        "loops. Never write empty placeholders. Durable facts go in "
        f'`"{ws}/{MEMORY_FILE}"`; today\'s notes go in '
        f'`"{mem}/<today>.md"`. You may re-read any memory file anytime.'
    )


def _read_section(workspace: Path, rel: str, label: str,
                  load: MemoryLoad) -> str | None:
    """Read one memory file; return its '## label\\nbody' section, or None when
    missing/blank/inert/unreadable (recording skips). Never raises."""
    path = workspace / rel
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as e:
        load.skipped.append((rel, type(e).__name__))
        return None
    if not _meaningful(raw):
        load.skipped.append((rel, "blank"))
        return None
    body, trimmed = _trim(raw, MAX_MEMORY_CHARS)
    if trimmed:
        body = body + "\n\n…[truncated]…"
    load.injected.append(rel)
    return f"## {label}\n{body}"


def resolve_memory(workspace_dir: Path | None, *, today: date) -> MemoryLoad:
    """Read MEMORY.md + today's + yesterday's daily notes into one content-gated
    block. None/absent/empty/inert => empty MemoryLoad (no block, no protocol)."""
    load = MemoryLoad()
    if workspace_dir is None:
        return load
    workspace = Path(workspace_dir)
    if not workspace.is_dir():
        return load
    yesterday = today - timedelta(days=1)
    sections = []
    for rel, label in [
        (MEMORY_FILE, "MEMORY.md"),
        (f"{MEMORY_DIR}/{today.isoformat()}.md", f"memory/{today.isoformat()}"),
        (f"{MEMORY_DIR}/{yesterday.isoformat()}.md", f"memory/{yesterday.isoformat()}"),
    ]:
        section = _read_section(workspace, rel, label, load)
        if section is not None:
            sections.append(section)
    if load.injected:                       # CONTENT-GATED: only when something was read
        load.block = ("\n\n# Memory\n\n" + _protocol(workspace) + "\n\n"
                      + "\n\n".join(sections))
    return load
```

- [ ] **Step 4: Run to verify pass**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_memory.py -q`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add harness/memory.py tests/test_memory.py
git commit -m "feat(memory): resolve_memory reads MEMORY + daily notes (content-gated)"
```

---

### Task 3: `SessionState` / `SessionStore` gain the workspace + memory dimensions

The isolation-core fields + the memory cache.

**Files:**
- Modify: `harness/acp_session.py`
- Test: `tests/test_acp_session.py` (append)

**Interfaces:**
- Consumes: `memory.MemoryLoad` (Task 2) — typed via `TYPE_CHECKING` to avoid an import cycle.
- Produces: `SessionState.workspace_dir: Path | None`, `.memory_block: str | None`, `.memory_load`, `.memory_load_emitted`; `SessionStore.new(cwd, workspace_dir=None)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_acp_session.py`:
```python
def test_session_state_has_workspace_and_memory_fields():
    from harness.acp_session import SessionState
    s = SessionState(cwd="/tmp")
    assert s.workspace_dir is None
    assert s.memory_block is None        # sentinel: not-yet-composed
    assert s.memory_load is None
    assert s.memory_load_emitted is False


def test_store_new_records_workspace_dir(tmp_path):
    from harness.acp_session import SessionStore
    store = SessionStore()
    sid = store.new(cwd=".", workspace_dir=tmp_path)
    assert store.get(sid).workspace_dir == tmp_path
```

- [ ] **Step 2: Run to verify failure**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_acp_session.py -k "workspace or memory" -q`
Expected: FAIL — fields/param missing.

- [ ] **Step 3: Modify `harness/acp_session.py`**

In the `TYPE_CHECKING` block (top), add:
```python
if TYPE_CHECKING:
    from harness.persona import PersonaLoad
    from harness.memory import MemoryLoad
    from pathlib import Path
```
In `SessionState`, after the persona fields, add:
```python
    workspace_dir: "Path | None" = None  # the persona workspace this session uses (Phase B isolation core)
    memory_block: str | None = None      # None = not-yet-composed; "" = composed-empty
    memory_load: "MemoryLoad | None" = None
    memory_load_emitted: bool = False
```
Change `SessionStore.new`:
```python
    def new(self, cwd: str, workspace_dir=None) -> str:
        session_id = uuid4().hex
        self._sessions[session_id] = SessionState(cwd=cwd, workspace_dir=workspace_dir)
        return session_id
```

- [ ] **Step 4: Run to verify pass**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_acp_session.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/acp_session.py tests/test_acp_session.py
git commit -m "feat(session): per-session workspace_dir + memory cache fields"
```

---

### Task 4: `compose_context` / `TurnContext` gain `memory_block`; `TracingAgent` injects it

The chokepoint + the agent-path injection.

**Files:**
- Modify: `harness/persona.py` (`compose_context`, `TurnContext`)
- Modify: `harness/tracing_agent.py` (`__init__`, `_render_template`)
- Test: `tests/test_persona.py` (append), `tests/test_tracing_agent_skills.py` (append)

**Interfaces:**
- Produces: `compose_context(persona_block, memory_block, skill_roots, skill_names) -> TurnContext`; `TurnContext.memory_block: str`; `TracingAgent(..., memory_block="")`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_persona.py`:
```python
def test_compose_context_carries_memory_block(tmp_path):
    from harness.persona import compose_context, TurnContext
    ctx = compose_context("PERSONA", "MEMORY", [tmp_path], [])
    assert ctx.persona_block == "PERSONA"
    assert ctx.memory_block == "MEMORY"
```

Append to `tests/test_tracing_agent_skills.py` (reuse its `_agent_p` helper pattern — it builds a TracingAgent with `system_template="SYS BASE"`):
```python
def test_memory_block_injected_between_persona_and_skills(tmp_path):
    from harness.events import Emitter
    from harness.tracing_agent import TracingAgent
    from harness.models_mock import build_mock_model
    from minisweagent.environments.local import LocalEnvironment
    em = Emitter(tmp_path / "e3.jsonl", clock=lambda: 0.0, console=False)
    a = TracingAgent(build_mock_model(), LocalEnvironment(cwd=str(tmp_path)),
                     emitter=em, persona_block="\n\nP", memory_block="\n\nM",
                     skill_block="\n\nS",
                     system_template="SYS BASE", instance_template="INST {{task}}")
    assert a._render_template(a.config.system_template) == "SYS BASE\n\nP\n\nM\n\nS"


def test_empty_memory_block_is_byte_identical(tmp_path):
    from harness.events import Emitter
    from harness.tracing_agent import TracingAgent
    from harness.models_mock import build_mock_model
    from minisweagent.environments.local import LocalEnvironment
    em = Emitter(tmp_path / "e4.jsonl", clock=lambda: 0.0, console=False)
    a = TracingAgent(build_mock_model(), LocalEnvironment(cwd=str(tmp_path)),
                     emitter=em, persona_block="", memory_block="", skill_block="",
                     system_template="SYS BASE", instance_template="INST {{task}}")
    assert a._render_template(a.config.system_template) == "SYS BASE"
```

- [ ] **Step 2: Run to verify failure**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_persona.py tests/test_tracing_agent_skills.py -k "memory" -q`
Expected: FAIL — `compose_context()` arity / `TracingAgent` kwarg.

- [ ] **Step 3a: Modify `harness/persona.py`**

`TurnContext` — add the field (after `persona_block`):
```python
    persona_block: str = ""
    memory_block: str = ""
    skill_block: str = ""
    skills: "skills.SkillLoad" = field(default_factory=lambda: skills.SkillLoad())
```
`compose_context` — add the param + pass-through:
```python
def compose_context(persona_block: str, memory_block: str, skill_roots: list[Path],
                    skill_names: list[str]) -> TurnContext:
    """Bundle already-resolved persona + memory blocks with a fresh skill compose.
    Persona+memory resolve once per session (caller-cached); skills per turn."""
    skill_load = skills.compose(skill_roots, skill_names)
    return TurnContext(persona_block=persona_block, memory_block=memory_block,
                       skill_block=skill_load.block, skills=skill_load)
```

**Also update the 3 EXISTING `compose_context` test calls in `tests/test_persona.py`**
(lines ~92, 101, 107) — the new 2nd positional param breaks them. Add `""` for
`memory_block`:
```python
# line ~92:
    ctx = compose_context("PERSONA TEXT", "", [skroot], ["tdd"])
# line ~101:
    ctx = compose_context("", "", [skroot], ["tdd"])
# line ~107:
    ctx = compose_context("", "", [tmp_path], [])
```

- [ ] **Step 3b: Modify `harness/tracing_agent.py`**

`__init__` — add `memory_block: str = ""` after `persona_block`, store `self._memory_block = memory_block`. In `_render_template`, insert memory between persona and skill:
```python
        out = super()._render_template(template)
        if template is self.config.system_template:
            if self._persona_block:
                out += self._persona_block
            if self._memory_block:
                out += self._memory_block
            if self._skill_block:
                out += self._skill_block
        return out
```

- [ ] **Step 4: Run to verify pass**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_persona.py tests/test_tracing_agent_skills.py -q`
Expected: PASS (existing + new).

> NOTE: the one PRODUCTION `compose_context` call site (`acp_agent.py:193`) still passes the old 3-arg form until Task 6 updates it — so any test that drives `acp_agent.prompt()` through the agent path (e.g. parts of `test_acp_session_context.py`, `test_acp_smoke.py`) will be RED between here and Task 6. That's expected sequencing. This task's verification (Step 4) runs only `test_persona.py` + `test_tracing_agent_skills.py`, which are GREEN after the in-task fix to the 3 existing compose_context calls. Run the FULL suite only at Task 8.

- [ ] **Step 5: Commit**

```bash
git add harness/persona.py harness/tracing_agent.py tests/test_persona.py tests/test_tracing_agent_skills.py
git commit -m "feat(memory): thread memory_block through compose_context + TracingAgent"
```

---

### Task 5: `MiniSweAgentRunner.run` forwards `memory_block`

So memory reaches the non-ACP dev path (#6 from the spec).

**Files:**
- Modify: `harness/runner.py`
- Test: `tests/test_runner.py` (append)

**Interfaces:**
- Produces: `MiniSweAgentRunner.run(task, *, skill_block="", persona_block="", memory_block="", **kwargs)`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_runner.py`:
```python
def test_runner_forwards_memory_block(tmp_path, monkeypatch):
    from harness.runner import MiniSweAgentRunner
    from harness.models_mock import build_mock_model
    from minisweagent.environments.local import LocalEnvironment
    import yaml
    from pathlib import Path
    captured = {}
    import harness.runner as rmod
    real = rmod.TracingAgent
    def spy(*a, **k):
        captured.update(k)
        return real(*a, **k)
    monkeypatch.setattr(rmod, "TracingAgent", spy)
    cfg = yaml.safe_load(Path("upstream/src/minisweagent/config/mini.yaml").read_text())["agent"]
    runner = MiniSweAgentRunner(build_mock_model(), LocalEnvironment(cwd=str(tmp_path)), agent_cfg=cfg)
    list(runner.run("t", skill_block="\n\nS", persona_block="\n\nP", memory_block="\n\nM"))
    assert captured.get("memory_block") == "\n\nM"
```

- [ ] **Step 2: Run to verify failure**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_runner.py -k memory -q`
Expected: FAIL — `run()` rejects `memory_block`.

- [ ] **Step 3: Modify `harness/runner.py`**

```python
    def run(self, task: str, *, skill_block: str = "", persona_block: str = "",
            memory_block: str = "", **kwargs) -> Iterator[Event]:
        q: "queue.Queue[Any]" = queue.Queue()
        emitter = QueueEmitter(q, clock=lambda: 0.0)
        agent = TracingAgent(self._model, self._env, emitter=emitter,
                             skill_block=skill_block, persona_block=persona_block,
                             memory_block=memory_block, **self._agent_cfg)
```

- [ ] **Step 4: Run to verify pass**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_runner.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/runner.py tests/test_runner.py
git commit -m "feat(runner): forward memory_block to TracingAgent (dev path)"
```

---

### Task 6: Wire memory into `HarnessAgent` — resolve, cache, gated emit, inject both paths

The integration crux. Mirrors the persona machinery exactly.

**Files:**
- Modify: `harness/acp_agent.py`
- Test: `tests/test_acp_session_context.py` (append)

**Interfaces:**
- Consumes: `memory.resolve_memory` (Task 2), the `SessionState` memory fields (Task 3), `compose_context(persona_block, memory_block, ...)` (Task 4).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_acp_session_context.py` (reuses `_build`/`_ScriptedRouter`/`_chat`/`_agent_fix`/`_prompt`/`_meta_keys_in_order`):
```python
def test_memory_reaches_agent_path(tmp_path, monkeypatch):
    # a workspace with MEMORY.md content -> the agent system message carries it
    ws = tmp_path / "ws"; ws.mkdir()
    (ws / "MEMORY.md").write_text("REMEMBER: prefers tabs", encoding="utf-8")
    captured = {}
    # capture the TracingAgent's rendered system message via a spy on the model
    agent = _build(_ScriptedRouter([_agent_fix()]), worker_model_id=None)
    agent._workspace_dir = ws
    sid = asyncio.run(agent.new_session(cwd=str(tmp_path))).session_id
    _prompt(agent, sid, "do a thing")
    # the memory block was cached on the session and carries the content
    assert "REMEMBER: prefers tabs" in (agent._store.get(sid).memory_block or "")


def test_memory_load_event_gated_off_for_empty_memory(tmp_path):
    # seeded-but-empty (no memory content) -> NO memory_load event (the no-op)
    ws = tmp_path / "ws"; ws.mkdir()
    (ws / "SOUL.md").write_text("BE TERSE", encoding="utf-8")   # persona but no memory
    agent = _build(_ScriptedRouter([_agent_fix()]), worker_model_id=None)
    agent._workspace_dir = ws
    sid = asyncio.run(agent.new_session(cwd=str(tmp_path))).session_id
    _prompt(agent, sid, "do a thing")
    assert "memory_load" not in _meta_keys_in_order(agent)


def test_memory_load_emits_after_task_classified(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    (ws / "MEMORY.md").write_text("durable fact", encoding="utf-8")
    agent = _build(_ScriptedRouter([_agent_fix()]), worker_model_id=None)
    agent._workspace_dir = ws
    sid = asyncio.run(agent.new_session(cwd=str(tmp_path))).session_id
    _prompt(agent, sid, "do a thing")
    keys = _meta_keys_in_order(agent)
    assert "memory_load" in keys
    assert keys.index("task_classified") < keys.index("memory_load")
```

- [ ] **Step 2: Run to verify failure**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_acp_session_context.py -k memory -q`
Expected: FAIL — memory not wired.

- [ ] **Step 3: Modify `harness/acp_agent.py`**

Add the import near `from harness import persona`:
```python
from harness import memory as memory_mod
```
In `prompt()`, right after the persona compose-once block (where `state.persona_block is None` is handled), add a parallel memory compose-once block (compute `today` once):
```python
        memory_first_load = None
        if state.memory_block is None:
            from datetime import date
            mload = await loop.run_in_executor(
                None, lambda: memory_mod.resolve_memory(state.workspace_dir, today=date.today()))
            state.memory_block = mload.block
            state.memory_load = mload
            memory_first_load = mload
```
After the gated `persona_load` emit block, add the parallel gated `memory_load` emit:
```python
        if (not state.memory_load_emitted and state.memory_load
                and state.memory_load.injected and personalized):
            await self._conn.session_update(session_id,
                with_meta(message_chunk(""),
                          {"memory_load": {"injected": state.memory_load.injected,
                                           "skipped": state.memory_load.skipped}}))
            state.memory_load_emitted = True
```
Update the `ChatHandler` construction (pre-concat persona+memory, #5):
```python
            handler = ChatHandler(self._worker_model_id, catalog=self._router.catalog,
                                  persona_block=(state.persona_block or "") + (state.memory_block or ""))
```
Update the `compose_context` call (add `memory_block`):
```python
        ctx = await loop.run_in_executor(
            None, persona.compose_context, state.persona_block or "",
            state.memory_block or "", self._skills_dir, cls.skills)
```
Update `_run_agent_turn` to accept + forward `memory_block`:
- signature: `async def _run_agent_turn(self, loop, session_id, state, text, skill_block, prior, persona_block="", memory_block=""):`
- the call site: `engine = await self._run_agent_turn(loop, session_id, state, text, ctx.skill_block, transcript, ctx.persona_block, ctx.memory_block)`
- the `TracingAgent(...)` construction inside `run_engine`: add `memory_block=memory_block,`

Also update `new_session` to record the workspace per session:
```python
    async def new_session(self, cwd, additional_directories=None, mcp_servers=None, **kw):
        return acp.NewSessionResponse(
            session_id=self._store.new(cwd=cwd, workspace_dir=self._workspace_dir))
```

- [ ] **Step 4: Run to verify pass**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_acp_session_context.py -q`
Expected: PASS (existing + 3 new).

- [ ] **Step 5: Commit**

```bash
git add harness/acp_agent.py tests/test_acp_session_context.py
git commit -m "feat(agent): resolve+inject memory both paths, gated event, per-session workspace"
```

---

### Task 7: Wire memory into the dev path (`run_traced`)

**Files:**
- Modify: `harness/run_traced.py`
- Test: `tests/test_run_traced.py` (append) — optional, the full suite covers it

**Interfaces:**
- Consumes: `memory.resolve_memory`, `MiniSweAgentRunner.run(memory_block=...)` (Task 5).

- [ ] **Step 1: Modify `harness/run_traced.py`**

Add the import (with the other `# noqa: E402` imports):
```python
from harness import memory as _memory  # noqa: E402
```
Where persona resolves (line ~124), resolve memory too and thread it through both the agent and chat paths:
```python
    from datetime import date
    persona_block = _persona.resolve_persona(_paths_persona.default_workspace_dir()).block
    memory_block = _memory.resolve_memory(_paths_persona.default_workspace_dir(), today=date.today()).block
```
In `run_agent`'s `runner.run(...)` call, add `memory_block=memory_block`. In the `make_chat_handler` lambda, pre-concat: `persona_block=persona_block + memory_block`.

- [ ] **Step 2: Run the dev-path tests**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_run_traced.py -q`
Expected: PASS (the default workspace has no memory content in tests → `memory_block == ""` → byte-identical).

- [ ] **Step 3: Commit**

```bash
git add harness/run_traced.py
git commit -m "feat(run_traced): resolve + thread memory through the dev path"
```

---

### Task 8: No-op regression + isolation tests + full suite

The load-bearing guarantees.

**Files:**
- Test: `tests/test_acp_session_context.py` (append), `tests/test_memory.py` (append isolation)

**Interfaces:** none new.

- [ ] **Step 1: Write the no-op + isolation tests**

Append to `tests/test_acp_session_context.py`:
```python
def test_seeded_default_workspace_memory_is_byte_identical_noop(monkeypatch, tmp_path):
    # the seeded default (inert templates, NO memory content) must stay
    # byte-identical: no system message, no persona_load, no memory_load.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from harness import persona, paths
    persona.seed_default_workspace()
    captured = {}
    def fake_completion(**kwargs):
        captured.update(kwargs); return iter([])
    import litellm
    monkeypatch.setattr(litellm, "completion", fake_completion)
    agent = _build(_ScriptedRouter([_chat()]), worker_model_id="gpt-5.4")
    agent._workspace_dir = paths.default_workspace_dir()
    sid = asyncio.run(agent.new_session(cwd=".")).session_id
    _prompt(agent, sid, "hi")
    assert captured["messages"] == [{"role": "user", "content": "hi"}]
    keys = _meta_keys_in_order(agent)
    assert "persona_load" not in keys and "memory_load" not in keys
```

Append to `tests/test_memory.py` (the isolation test — proves the plumbing LAYER per spec #8):
```python
def test_two_workspaces_have_isolated_memory(tmp_path):
    a = tmp_path / "a"; a.mkdir(); (a / "MEMORY.md").write_text("A-fact", encoding="utf-8")
    b = tmp_path / "b"; b.mkdir(); (b / "MEMORY.md").write_text("B-fact", encoding="utf-8")
    la = resolve_memory(a, today=TODAY)
    lb = resolve_memory(b, today=TODAY)
    assert "A-fact" in la.block and "A-fact" not in lb.block
    assert "B-fact" in lb.block and "B-fact" not in la.block
```

- [ ] **Step 2: Run the new tests**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_acp_session_context.py tests/test_memory.py -k "noop or isolated" -q`
Expected: PASS. (If the no-op test fails, content-gating is broken — STOP and fix Task 2, do not weaken the test.)

- [ ] **Step 3: FULL suite (zero-regression gate)**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/ -q`
Expected: PASS — all existing tests (incl. the Phase A persona/no-op tests, unchanged) + all new.

- [ ] **Step 4: Commit**

```bash
git add tests/
git commit -m "test(memory): no-op regression (seeded default) + workspace isolation"
```

---

### Task 9: Scope gate

**Files:** none (verification only).

- [ ] **Step 1: Scope gate**

Run: `git diff --stat main...HEAD`
Confirm only: `harness/memory.py`, `harness/persona.py`, `harness/acp_session.py`, `harness/acp_agent.py`, `harness/tracing_agent.py`, `harness/runner.py`, `harness/run_traced.py`, the matching `tests/`, and the spec/plan docs.

Run: `grep -rnE "\-\-persona|/persona|persona\.toml|compaction|summariz" harness/memory.py harness/acp_session.py`
Expected: no matches (no Phase C/selection or compaction concepts leaked in).

- [ ] **Step 2: Commit (if any touch-ups)**

```bash
git add -A && git commit -m "chore(memory): Phase B complete — suite green, scope held" --allow-empty
```

---

## Self-Review

**Spec coverage** (against `2026-06-26-persona-phaseB-memory-design.md`):
- §3 `memory.py`/`resolve_memory`/content-gating/today-injected/trim → **Task 2** ✓
- §3.1 `compose_context`+`TurnContext` memory_block; agent-path injection order → **Task 4** ✓
- §3.1/#5 chat pre-concat → **Task 6** ✓
- §4 isolation core (workspace_dir on SessionState/Store) → **Task 3** ✓; isolation test (plumbing layer, #8) → **Task 8** ✓
- §5 protocol (content-gated #1, mkdir #2, quoted/test-f #3) → **Task 2** (`_protocol`) ✓
- §6 gated memory_load (after task_classified, personalized, once) + #4 caveat → **Task 6** ✓
- §6 no-op (seeded default byte-identical) → **Task 8** ✓
- §7 `runner.py` (#6) → **Task 5** ✓; `_meaningful`/`_trim` reuse → **Task 1** ✓
- §7 run_traced → **Task 7** ✓
- §8/§9 scope held → **Task 9** ✓

No gaps.

**Placeholder scan:** every code step has complete code. The one note (Task 4 Step 4 "suite RED until Task 7") is an intentional sequencing flag, not a TODO.

**Type consistency:** `resolve_memory(workspace_dir, *, today)`, `MemoryLoad`, `compose_context(persona_block, memory_block, skill_roots, skill_names)`, `MAX_MEMORY_CHARS`, `memory_block` param name, the `state.memory_block`/`memory_load`/`memory_load_emitted` fields are used identically across all tasks.
