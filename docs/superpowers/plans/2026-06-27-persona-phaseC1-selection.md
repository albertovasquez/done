# Persona Phase C1 — Selection & Isolation Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make DoneDone multi-persona: `--persona <id>` selects which workspace the agent runs as, with per-persona session/memory isolation and a single-homed per-persona model in `done.conf [agents.<id>]`.

**Architecture:** Selection resolves a `workspace_dir` once at boot (in `acp_main`/`run_traced`) and the per-persona model once at boot (in `tui_main`, crossing to the agent subprocess via `VIBEPROXY_MODEL`); everything downstream runs the unchanged Phase-A/B `compose_context` injection path. The model is single-homed in `done.conf` keyed per persona (`config.py` already round-trips `[agents.<key>]` tables); `set_model`/`set_yolo` write the agent's own key, derived as `workspace_dir.name` (or `"default"` when no persona). No migration — the model never moves.

**Tech Stack:** Python 3.11+, stdlib `tomllib` (read) + hand-rolled TOML writer (existing in `config.py`), `argparse`, pytest. No new dependencies.

## Global Constraints

- **No per-persona code branch in the selection / precedence / injection path.** `default` is just persona #0 — resolved through the same functions as any id. The `default`-named config functions are thin `id="default"` wrappers, NOT branches.
- **Single-home model:** the worker model lives only in `done.conf [agents.<id>]`. No second writer. `persona.toml` never holds the model.
- **No-op guarantee:** no `--persona`, no `done.conf`, no persona files → engine-default model, zero persona/memory injection → byte-identical to pre-C1.
- **Best-effort config:** a missing/corrupt `done.conf`/`persona.toml` never raises into boot; reads degrade to defaults (`None`/`[]`).
- **Selection is explicit only:** `--persona <id>` resolves an EXISTING workspace; an unknown id is a hard boot error (non-zero exit), never a silent fallback to default.
- **Persona id = workspace directory basename.** `workspace_dir.name`; `None` workspace → `"default"`.
- **Engine default model:** `vibeproxy.DEFAULT_MODEL` (currently `"gpt-5.4"`). Backend flag `--model` is `mock`/`vibeproxy` only — it is NOT a model-id override; there is no model-id CLI flag.
- **Test command (from worktree root):** `.venv/bin/python -m pytest tests/ -q`. Full suite (393 today) must stay green.
- **Commit trailer:** end every commit message with `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

---

### Task 1: Per-persona config API in `config.py`

Generalize the `default`-only persistence to any persona id. Add `update_agent`/`save_agent`/`load_agent` and make `yolo_pinned` accept an id; the existing `update_default`/`save_default`/`load_default`/`yolo_pinned()` become thin `"default"` wrappers so all current `test_config.py` tests stay green.

**Files:**
- Modify: `harness/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: existing `AgentConfig`, `load()`, `_serialize()`, `RESERVED_KEY="default"`.
- Produces:
  - `update_agent(persona_id: str, *, backend: str | None = None, model: str | None = None, yolo_pinned: bool | None = None) -> None`
  - `save_agent(persona_id: str, cfg: AgentConfig) -> None`
  - `load_agent(persona_id: str) -> AgentConfig | None`
  - `yolo_pinned(persona_id: str = "default") -> bool`
  - `update_default(...)`, `save_default(cfg)`, `load_default()` preserved as `"default"` wrappers.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config.py`:

```python
def test_save_and_load_named_agent(isolated_config):
    config.save_agent("fred", config.AgentConfig(backend="vibeproxy", model="m1"))
    assert config.load_agent("fred") == config.AgentConfig(backend="vibeproxy", model="m1")

def test_named_agent_isolated_from_default(isolated_config):
    config.save_default(config.AgentConfig(backend="vibeproxy", model="d"))
    config.save_agent("fred", config.AgentConfig(backend="vibeproxy", model="f"))
    assert config.load_default() == config.AgentConfig(backend="vibeproxy", model="d")
    assert config.load_agent("fred") == config.AgentConfig(backend="vibeproxy", model="f")

def test_yolo_pinned_per_persona(isolated_config):
    config.update_agent("fred", backend="vibeproxy", model="f", yolo_pinned=True)
    assert config.yolo_pinned("fred") is True
    assert config.yolo_pinned("default") is False

def test_update_agent_refuses_incomplete_create(isolated_config):
    config.update_agent("fred", yolo_pinned=True)   # no backend/model yet
    assert config.load_agent("fred") is None         # nothing written
```

Note: `isolated_config` is the existing autouse fixture in `tests/test_config.py` setting `XDG_CONFIG_HOME` to a tmp dir — confirm it is present; if a given test file lacks it, add the same fixture.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_config.py::test_save_and_load_named_agent -v`
Expected: FAIL with `AttributeError: module 'harness.config' has no attribute 'save_agent'`.

- [ ] **Step 3: Write minimal implementation**

In `harness/config.py`, rename the body of `update_default` to a keyed `update_agent` and make `update_default` delegate. Replace the existing `update_default`/`save_default`/`load_default`/`yolo_pinned` block (lines ~79-165) with:

```python
def load_agent(persona_id: str) -> AgentConfig | None:
    """The [agents.<persona_id>] entry, or None when absent/unreadable."""
    return load().get(persona_id)


def load_default() -> AgentConfig | None:
    """The reserved [agents.default] entry, or None when absent/unreadable."""
    return load_agent(RESERVED_KEY)


def update_agent(
    persona_id: str,
    *,
    backend: str | None = None,
    model: str | None = None,
    yolo_pinned: bool | None = None,
) -> None:
    """Upsert [agents.<persona_id>], overlaying ONLY the kwargs passed (None =
    leave unchanged). Preserves untouched fields and every other agent table.
    Writes atomically (temp file + os.replace) under a created config dir.
    Best-effort: callers that must not fail on I/O errors should guard the call.

    Refuses to CREATE a new table with empty required fields: if the table does
    not exist yet and the merged backend/model would be blank, it no-ops rather
    than writing backend=""/model="" (which a later flagless launch would
    resolve to `--model ""` and crash the agent). Updating an EXISTING
    (already-complete) table is unaffected. The default's name stays None; named
    agents preserve their existing name."""
    agents = load()
    cur = agents.get(persona_id)
    base_backend = cur.backend if cur is not None else ""
    base_model = cur.model if cur is not None else ""
    base_pinned = cur.yolo_pinned if cur is not None else False
    base_name = cur.name if cur is not None else None
    merged_backend = base_backend if backend is None else backend
    merged_model = base_model if model is None else model
    if not merged_backend or not merged_model:
        return                              # don't persist an incomplete table
    agents[persona_id] = AgentConfig(
        backend=merged_backend,
        model=merged_model,
        name=base_name,
        yolo_pinned=base_pinned if yolo_pinned is None else yolo_pinned,
    )
    text = _serialize(agents)

    path = conf_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def update_default(
    *,
    backend: str | None = None,
    model: str | None = None,
    yolo_pinned: bool | None = None,
) -> None:
    """Upsert [agents.default]. Thin wrapper over update_agent("default", ...)."""
    update_agent(RESERVED_KEY, backend=backend, model=model, yolo_pinned=yolo_pinned)


def save_agent(persona_id: str, cfg: AgentConfig) -> None:
    """Upsert persona_id's backend+model, preserving its yolo_pinned and every
    other agent table. NOTE: deliberately ignores cfg.yolo_pinned — set_model
    passes a default-constructed cfg and must not clear an existing pin."""
    update_agent(persona_id, backend=cfg.backend, model=cfg.model)


def save_default(cfg: AgentConfig) -> None:
    """Upsert the default's backend+model. Thin wrapper over save_agent."""
    save_agent(RESERVED_KEY, cfg)


def yolo_pinned(persona_id: str = "default") -> bool:
    """Whether the persisted persona is pinned to launch in YOLO. False when the
    table is absent or the file is unreadable."""
    cur = load_agent(persona_id)
    return cur.yolo_pinned if cur is not None else False
```

IMPORTANT: `_serialize` already orders `RESERVED_KEY` first then sorts the rest, and preserves `name`/`yolo_pinned` — no change needed there. Verify `update_agent` passes `name=base_name` so a named agent's `name` survives an update (the old `update_default` dropped name because the default has none; the generalized version must keep it).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_config.py -q`
Expected: PASS — the 4 new tests AND all pre-existing tests (the `save_default`/`load_default`/`update_default`/`yolo_pinned()` wrappers keep them green).

- [ ] **Step 5: Commit**

```bash
git add harness/config.py tests/test_config.py
git commit -m "feat(config): per-persona keyed agent config (save/load/update_agent, yolo_pinned(id))

default-named functions become thin id=\"default\" wrappers — no branch.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `model_resolve.resolve_model` — the precedence ladder

A pure function implementing the 4-rung ladder. No global reads — every input is a parameter — so it is exhaustively unit-testable. This is the Codex-review unit.

**Files:**
- Create: `harness/model_resolve.py`
- Test: `tests/test_model_resolve.py`

**Interfaces:**
- Produces: `resolve_model(*, shell_env: str | None, dotenv: str | None, persisted: str | None, engine_default: str) -> str`
- Consumed by: Task 5 (`tui_main`), Task 6 (`run_traced`).

- [ ] **Step 1: Write the failing test**

Create `tests/test_model_resolve.py`:

```python
from harness.model_resolve import resolve_model


def test_shell_env_wins_over_everything():
    assert resolve_model(shell_env="S", dotenv="D", persisted="P", engine_default="E") == "S"

def test_persisted_beats_dotenv_and_default():
    assert resolve_model(shell_env=None, dotenv="D", persisted="P", engine_default="E") == "P"

def test_dotenv_beats_default():
    assert resolve_model(shell_env=None, dotenv="D", persisted=None, engine_default="E") == "D"

def test_falls_to_engine_default():
    assert resolve_model(shell_env=None, dotenv=None, persisted=None, engine_default="E") == "E"

def test_empty_strings_are_treated_as_absent():
    # an empty env/persisted value must not win — fall through to the next rung
    assert resolve_model(shell_env="", dotenv="", persisted="", engine_default="E") == "E"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_model_resolve.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'harness.model_resolve'`.

- [ ] **Step 3: Write minimal implementation**

Create `harness/model_resolve.py`:

```python
"""The persona worker-model precedence ladder — a single pure function.

Highest rung wins:
  1. shell_env     — a model id the user exported in their SHELL (per-launch)
  2. persisted     — done.conf [agents.<persona>].model
  3. dotenv        — a model id from a project .env file
  4. engine_default

Pure: every input is a parameter, no os.environ / file reads here, so the ladder
is exhaustively testable. Empty strings count as absent (a blank env var must not
beat a real persisted model)."""

from __future__ import annotations


def resolve_model(
    *,
    shell_env: str | None,
    dotenv: str | None,
    persisted: str | None,
    engine_default: str,
) -> str:
    for candidate in (shell_env, persisted, dotenv):
        if candidate:                       # non-None and non-empty
            return candidate
    return engine_default
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_model_resolve.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add harness/model_resolve.py tests/test_model_resolve.py
git commit -m "feat(model): pure resolve_model precedence ladder (shell>persisted>dotenv>default)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `persona_select` — workspace resolver

Resolve a persona id to a workspace dir (error on unknown), enumerate personas, and the `UnknownPersona` exception. The single selection chokepoint.

**Files:**
- Create: `harness/persona_select.py`
- Test: `tests/test_persona_select.py`

**Interfaces:**
- Consumes: `paths.default_workspace_dir()`, `paths.config_dir()`.
- Produces:
  - `class UnknownPersona(Exception)` — `str(e)` is the bad id.
  - `resolve_workspace(persona_id: str | None) -> Path`
  - `list_personas() -> list[str]` (sorted ids of existing workspace dirs; read-only)

- [ ] **Step 1: Write the failing test**

Create `tests/test_persona_select.py`:

```python
import pytest
from harness import persona_select, paths


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path


def test_none_and_default_resolve_to_default_dir():
    assert persona_select.resolve_workspace(None) == paths.default_workspace_dir()
    assert persona_select.resolve_workspace("default") == paths.default_workspace_dir()

def test_named_persona_resolves_when_dir_exists():
    target = paths.config_dir() / "agents" / "fred"
    target.mkdir(parents=True)
    assert persona_select.resolve_workspace("fred") == target

def test_unknown_persona_raises():
    with pytest.raises(persona_select.UnknownPersona) as exc:
        persona_select.resolve_workspace("nope")
    assert "nope" in str(exc.value)

def test_list_personas_enumerates_existing_only_and_is_read_only(tmp_path):
    agents = paths.config_dir() / "agents"
    (agents / "default").mkdir(parents=True)
    (agents / "fred").mkdir(parents=True)
    (agents / "afile").parent.mkdir(parents=True, exist_ok=True)
    (agents / "afile").write_text("x")          # a non-dir must be ignored
    result = persona_select.list_personas()
    assert result == ["default", "fred"]
    # read-only: calling it created nothing new
    assert sorted(p.name for p in agents.iterdir()) == ["afile", "default", "fred"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_persona_select.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'harness.persona_select'`.

- [ ] **Step 3: Write minimal implementation**

Create `harness/persona_select.py`:

```python
"""Persona selection: resolve a persona id to its workspace directory.

The ONE selection chokepoint. None / "default" → the built-in default workspace;
a named id → config_dir()/agents/<id> IF it exists; a missing id is a hard error
(UnknownPersona) — selection is explicit, never a silent fallback to default.
Creation of new workspaces is out of scope (Phase D)."""

from __future__ import annotations

from pathlib import Path

from harness import paths

RESERVED_KEY = "default"


class UnknownPersona(Exception):
    """Raised when --persona names a workspace that does not exist."""


def _agents_dir() -> Path:
    return paths.config_dir() / "agents"


def resolve_workspace(persona_id: str | None) -> Path:
    """Resolve persona_id to its workspace dir. None/"default" → the built-in
    default workspace; a named id → agents/<id> if the dir exists, else raise
    UnknownPersona(persona_id)."""
    if persona_id is None or persona_id == RESERVED_KEY:
        return paths.default_workspace_dir()
    target = _agents_dir() / persona_id
    if not target.is_dir():
        raise UnknownPersona(persona_id)
    return target


def list_personas() -> list[str]:
    """Sorted ids of existing persona workspaces (subdirectories of agents/).
    Read-only: never creates anything. Returns [] when agents/ is absent."""
    agents = _agents_dir()
    try:
        return sorted(p.name for p in agents.iterdir() if p.is_dir())
    except OSError:
        return []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_persona_select.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add harness/persona_select.py tests/test_persona_select.py
git commit -m "feat(persona): persona_select.resolve_workspace (error on unknown) + list_personas

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `persona_config.read_skills` — persona.toml (non-model config)

A reader for the per-workspace `persona.toml`, used for extra skill dirs only. Never holds the model. Best-effort: missing/corrupt → `[]`.

**Files:**
- Create: `harness/persona_config.py`
- Test: `tests/test_persona_config.py`

**Interfaces:**
- Produces: `read_skills(workspace_dir: Path | None) -> list[Path]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_persona_config.py`:

```python
from pathlib import Path
from harness import persona_config


def test_missing_workspace_returns_empty(tmp_path):
    assert persona_config.read_skills(tmp_path / "nope") == []

def test_none_workspace_returns_empty():
    assert persona_config.read_skills(None) == []

def test_reads_skills_list(tmp_path):
    (tmp_path / "persona.toml").write_text('skills = ["/a/b", "~/c"]\n')
    got = persona_config.read_skills(tmp_path)
    assert got == [Path("/a/b"), Path("~/c").expanduser()]

def test_corrupt_toml_returns_empty(tmp_path):
    (tmp_path / "persona.toml").write_text("skills = [unclosed\n")
    assert persona_config.read_skills(tmp_path) == []

def test_no_skills_key_returns_empty(tmp_path):
    (tmp_path / "persona.toml").write_text('other = "x"\n')
    assert persona_config.read_skills(tmp_path) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_persona_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'harness.persona_config'`.

- [ ] **Step 3: Write minimal implementation**

Create `harness/persona_config.py`:

```python
"""Per-workspace persona.toml reader — NON-model static config only.

Currently exposes extra skill roots (Phase D's D4 config surface). The worker
model is deliberately NOT here: it is single-homed in done.conf [agents.<id>]
(see config.py) to avoid a dual-writer clobber. Best-effort, like config.load:
a missing/corrupt/empty file or a missing/ill-typed key degrades to []."""

from __future__ import annotations

import tomllib
from pathlib import Path

PERSONA_TOML = "persona.toml"


def read_skills(workspace_dir: Path | None) -> list[Path]:
    """Extra skill roots declared in <workspace_dir>/persona.toml `skills`.
    Returns [] when the dir/file is absent, unreadable, corrupt, or the key is
    missing or not a list of strings. `~` is expanded; relative paths are left
    as-is (resolved by the caller against its own base)."""
    if workspace_dir is None:
        return []
    path = workspace_dir / PERSONA_TOML
    try:
        raw = path.read_bytes()
    except OSError:
        return []
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        return []
    skills = data.get("skills")
    if not isinstance(skills, list):
        return []
    return [Path(s).expanduser() for s in skills if isinstance(s, str)]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_persona_config.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add harness/persona_config.py tests/test_persona_config.py
git commit -m "feat(persona): persona_config.read_skills (persona.toml, non-model config)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: `set_model`/`set_yolo` write the agent's own persona key

Repoint the live model/yolo persistence from the hardcoded default to the agent's own persona key (`self._workspace_dir.name`, or `"default"` when no workspace). Make `set_model` return the real persisted state on failure.

**Files:**
- Modify: `harness/acp_agent.py` (lines ~59-100, the `ext_method` body)
- Test: `tests/test_acp_agent.py`

**Interfaces:**
- Consumes: `config.save_agent(persona_id, cfg)`, `config.update_agent(persona_id, ...)`, `config.yolo_pinned(persona_id)` (Task 1).
- Produces: a `_persona_key()` helper on `HarnessAgent`; unchanged external `set_model`/`set_yolo` return shapes (except set_model now reports a real failure).

- [ ] **Step 1: Write/adjust the failing tests**

In `tests/test_acp_agent.py`, update `_make_agent` to accept a workspace, and retarget the two persistence tests + add a named-key test. Replace lines 14-53 with:

```python
def _make_agent(backend="vibeproxy", workspace_dir=None):
    """A HarnessAgent with cheap stand-ins; only set_model behavior is exercised."""
    return HarnessAgent(
        model_factory=lambda *a, **k: None,
        agent_cfg={},
        skills_dir=[],
        router=object(),
        worker_model_id="gpt-5.4",
        yolo=False,
        backend=backend,
        workspace_dir=workspace_dir,
    )


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path


def test_set_model_persists_under_default_when_no_workspace():
    agent = _make_agent(backend="vibeproxy")          # workspace_dir=None -> "default"
    result = asyncio.run(agent.ext_method("harness/set_model", {"model": "claude-opus-4-8"}))
    assert result == {"ok": True, "model": "claude-opus-4-8"}
    assert config.load_default() == config.AgentConfig(
        backend="vibeproxy", model="claude-opus-4-8")


def test_set_model_persists_under_named_persona(tmp_path):
    ws = tmp_path / "agents" / "fred"
    ws.mkdir(parents=True)
    agent = _make_agent(backend="vibeproxy", workspace_dir=ws)
    asyncio.run(agent.ext_method("harness/set_model", {"model": "m-fred"}))
    assert config.load_agent("fred") == config.AgentConfig(backend="vibeproxy", model="m-fred")
    assert config.load_default() is None               # default table untouched


def test_set_model_empty_model_does_not_persist():
    agent = _make_agent()
    asyncio.run(agent.ext_method("harness/set_model", {"model": ""}))
    assert config.load_default() is None  # nothing written for a no-op swap


def test_set_model_reports_failure(monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(config, "save_agent", boom)
    agent = _make_agent()
    result = asyncio.run(agent.ext_method("harness/set_model", {"model": "x"}))
    # swap still applies in-session, but the response reports it did NOT persist
    assert result["model"] == "x"
    assert result["ok"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_acp_agent.py::test_set_model_persists_under_named_persona -v`
Expected: FAIL — `set_model` currently calls `config.save_default(...)`, so `load_agent("fred")` is `None`. `test_set_model_reports_failure` also FAILs (current code returns `{"ok": True}` always).

- [ ] **Step 3: Write the implementation**

In `harness/acp_agent.py`, add a helper just below `_auto_allow` (after line 49):

```python
    def _persona_key(self) -> str:
        """The done.conf agent key this agent persists under: its own persona id,
        which is the workspace directory's basename, or "default" when running
        with no persona workspace. NOT a branch — "default" is just the id."""
        return self._workspace_dir.name if self._workspace_dir is not None else "default"
```

Replace the `harness/set_model` block (currently lines ~59-67):

```python
        if method == "harness/set_model":
            model = (params or {}).get("model")
            ok = True
            if model:
                self._worker_model_id = model
                try:                       # best-effort; report failure, never break the swap
                    config.save_agent(self._persona_key(),
                                      config.AgentConfig(backend=self._backend, model=model))
                except Exception:
                    ok = False
            return {"ok": ok, "model": self._worker_model_id}
```

In the `harness/set_yolo` block, change the two `config.update_default(...)` calls (lines ~91 and ~93) to `config.update_agent(self._persona_key(), ...)`:

```python
                        config.update_agent(self._persona_key(), **fields)
                    else:
                        config.update_agent(self._persona_key(), yolo_pinned=False)
```

And change the read at line ~97:

```python
                pinned = config.yolo_pinned(self._persona_key())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_acp_agent.py -q`
Expected: PASS — including the existing `test_set_yolo_*` tests (they use `workspace_dir=None` → `"default"` key, so `load_default()`/`yolo_pinned()` reads still see them).

- [ ] **Step 5: Commit**

```bash
git add harness/acp_agent.py tests/test_acp_agent.py
git commit -m "feat(agent): set_model/set_yolo persist under the agent's own persona key

_persona_key() = workspace_dir.name or \"default\"; set_model now reports real
persist failures instead of always claiming success.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: `--persona` in `acp_main` (child process)

The agent subprocess gains `--persona`; it resolves the workspace (hard error on unknown) and constructs `HarnessAgent` with the SELECTED dir instead of the hardcoded default.

**Files:**
- Modify: `harness/acp_main.py` (argparse ~66-73; agent construction ~95-104)
- Test: `tests/test_acp_main.py`

**Interfaces:**
- Consumes: `persona_select.resolve_workspace`, `persona_select.UnknownPersona` (Task 3).
- Produces: `acp_main` honoring `--persona <id>`; exits non-zero on unknown.

- [ ] **Step 1: Write the failing test**

Create (or extend) `tests/test_acp_main.py`. The agent construction is awaited inside `_main`; spy on `HarnessAgent` and stop before `acp.run_agent` by making the spy raise a sentinel:

```python
import asyncio
import pytest
from harness import acp_main, paths


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("MSWEA_SILENT_STARTUP", "1")
    return tmp_path


class _Stop(Exception):
    pass


def _spy_workspace(monkeypatch):
    """Patch HarnessAgent to capture workspace_dir then abort before run_agent."""
    captured = {}
    import harness.acp_agent as agent_mod

    def fake_init(self, **kw):
        captured["workspace_dir"] = kw.get("workspace_dir")
        raise _Stop()
    monkeypatch.setattr(agent_mod.HarnessAgent, "__init__", fake_init)
    return captured


def test_no_persona_uses_default_workspace(monkeypatch):
    captured = _spy_workspace(monkeypatch)
    with pytest.raises(_Stop):
        asyncio.run(acp_main._main(["--model", "mock", "--cwd", "."]))
    assert captured["workspace_dir"] == paths.default_workspace_dir()


def test_named_persona_uses_its_workspace(monkeypatch, tmp_path):
    ws = paths.config_dir() / "agents" / "fred"
    ws.mkdir(parents=True)
    captured = _spy_workspace(monkeypatch)
    with pytest.raises(_Stop):
        asyncio.run(acp_main._main(["--model", "mock", "--cwd", ".", "--persona", "fred"]))
    assert captured["workspace_dir"] == ws


def test_unknown_persona_exits_nonzero(monkeypatch, capsys):
    _spy_workspace(monkeypatch)   # should never be reached
    with pytest.raises(SystemExit) as exc:
        asyncio.run(acp_main._main(["--model", "mock", "--cwd", ".", "--persona", "ghost"]))
    assert exc.value.code != 0
    assert "ghost" in capsys.readouterr().err
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_acp_main.py::test_named_persona_uses_its_workspace -v`
Expected: FAIL — `acp_main` has no `--persona` arg / still passes `paths.default_workspace_dir()`.

- [ ] **Step 3: Write the implementation**

In `harness/acp_main.py`, add the arg after the `--yolo` arg (line ~72):

```python
    parser.add_argument("--persona", default=None,
                        help="persona workspace id to run as (default: the built-in default)")
```

Replace the hardcoded workspace construction. After `cwd = ...` / `paths.load_env(cwd)` (around line 76) and before `from harness import persona`, resolve the workspace:

```python
    import sys
    from harness import persona_select
    try:
        workspace_dir = persona_select.resolve_workspace(args.persona)
    except persona_select.UnknownPersona as e:
        print(f'no persona "{e}" — run /persona to list available personas',
              file=sys.stderr)
        raise SystemExit(2)
```

Then change the `HarnessAgent(...)` kwarg (line ~103) from:

```python
        workspace_dir=paths.default_workspace_dir(),
```
to:
```python
        workspace_dir=workspace_dir,
```

Note: `persona.seed_default_workspace()` (line 79) stays — it seeds the default templates and is independent of selection (a named persona's dir already exists, proven by `resolve_workspace`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_acp_main.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add harness/acp_main.py tests/test_acp_main.py
git commit -m "feat(acp_main): --persona resolves the agent workspace (hard error on unknown)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: `--persona` in `tui_main` — per-persona model resolution + plumbing

The parent process gains `--persona`: it reads the per-persona model key, resolves the model via the ladder, exports `VIBEPROXY_MODEL`, passes `--persona` to the agent subprocess, and carries it through `/reload`.

**Files:**
- Modify: `harness/tui_main.py` (`_resolve_model` ~20-31; `_resolve_yolo` ~34-39; `_relaunch_args` ~54-60; `main` ~73-108)
- Test: `tests/test_tui_main.py`

**Interfaces:**
- Consumes: `config.load_agent` (Task 1), `model_resolve.resolve_model` (Task 2), `vibeproxy.DEFAULT_MODEL`.
- Produces: `tui_main` honoring `--persona`; `_resolve_model(explicit_backend, persona_id)`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_tui_main.py` (it already has the env-precedence tests; keep them green):

```python
def test_resolve_model_reads_named_persona_key(isolated_config, monkeypatch):
    from harness import tui_main, config
    config.save_agent("fred", config.AgentConfig(backend="vibeproxy", model="m-fred"))
    backend, model = tui_main._resolve_model(None, "fred")
    assert (backend, model) == ("vibeproxy", "m-fred")


def test_resolve_model_default_persona_unchanged(isolated_config, monkeypatch):
    from harness import tui_main, config
    config.save_default(config.AgentConfig(backend="vibeproxy", model="m-def"))
    assert tui_main._resolve_model(None, "default") == ("vibeproxy", "m-def")
    assert tui_main._resolve_model(None, None) == ("vibeproxy", "m-def")


def test_persona_flows_into_agent_cmd_and_relaunch(isolated_config, monkeypatch, tmp_path):
    from harness import tui_main, paths
    (paths.config_dir() / "agents" / "fred").mkdir(parents=True)
    captured = {}
    monkeypatch.setattr(tui_main, "HarnessTui",
        lambda **kw: captured.update(kw) or type("A", (), {"run": lambda self: None, "_reexec": False})())
    tui_main.main(["--model", "vibeproxy", "--cwd", str(tmp_path), "--persona", "fred"])
    assert "--persona" in captured["agent_cmd"]
    assert "fred" in captured["agent_cmd"]
```

`isolated_config` is the existing (non-autouse) fixture in `tests/test_tui_main.py:96` that sets `XDG_CONFIG_HOME` to `tmp_path` — request it by name as shown. The env-precedence tests in that file (`test_persisted_model_beats_dotenv`, `test_real_shell_env_beats_persisted_model`) are UNCHANGED behaviorally; if they call `_resolve_model(backend)` positionally, the new optional `persona_id` default keeps them green — verify they still pass in Step 4.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tui_main.py::test_resolve_model_reads_named_persona_key -v`
Expected: FAIL — `_resolve_model` takes one arg today (`TypeError`).

- [ ] **Step 3: Write the implementation**

In `harness/tui_main.py`:

(a) Generalize `_resolve_model` (lines 20-31) to take a persona id:

```python
def _resolve_model(explicit_backend: str | None,
                   persona_id: str | None = None) -> tuple[str, str | None]:
    """Resolve (backend, model_override) by precedence: an explicit --model flag
    wins (and applies no model override — env/defaults stand); else the persisted
    done.conf entry for this PERSONA; else the hardcoded ("vibeproxy", None).
    model_override is the persisted model string to export as VIBEPROXY_MODEL, or
    None to leave the env/default untouched."""
    if explicit_backend is not None:
        return explicit_backend, None
    persisted = config.load_agent(persona_id or "default")
    if persisted is not None:
        return persisted.backend, persisted.model
    return "vibeproxy", None
```

(b) Generalize `_resolve_yolo` (lines 34-39) to read the persona's pin:

```python
def _resolve_yolo(flag: bool, persona_id: str | None = None) -> bool:
    """--yolo forces auto-allow on; else the persisted pin for this persona; else
    off."""
    if flag:
        return True
    return config.yolo_pinned(persona_id or "default")
```

(c) Carry `--persona` through `_relaunch_args` (lines 54-60):

```python
def _relaunch_args(args, cwd) -> list[str]:
    """Flags to re-launch THIS TUI with, reconstructed from parsed args (not raw
    sys.argv) so they are correct however it was invoked. --cwd is always explicit."""
    flags = ["--model", args.model, "--cwd", cwd]
    if args.yolo:
        flags.append("--yolo")
    if getattr(args, "persona", None):
        flags += ["--persona", args.persona]
    return flags
```

(d) In `main` (after the `--yolo` arg, ~line 79) add:

```python
    parser.add_argument("--persona", default=None,
                        help="persona workspace id to run as (default: the built-in default)")
```

(e) In `main`, thread the persona id into the resolves and the agent command. Change the `_resolve_model`/`_resolve_yolo` calls (lines 89, 91):

```python
    backend, model_override = _resolve_model(args.model, args.persona)
    args.model = backend
    yolo = _resolve_yolo(args.yolo, args.persona)
```

And the `agent_cmd` (line 104) — append `--persona` when set:

```python
    agent_cmd = [sys.executable, "-m", "harness.acp_main", "--model", backend, "--cwd", cwd]
    if args.persona:
        agent_cmd += ["--persona", args.persona]
    if args.yolo:
        agent_cmd.append("--yolo")
```

Note: the `shell_set_model` / `load_env` / `os.environ["VIBEPROXY_MODEL"]` block (lines 83-98) is UNCHANGED — it still implements rungs 1/3/4 of the ladder (shell vs .env vs default); Task 7 only changes which persona key supplies `model_override` (rung 2). Do not touch that block.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tui_main.py -q`
Expected: PASS — the 3 new tests AND the existing `test_persisted_model_beats_dotenv` / `test_real_shell_env_beats_persisted_model` (the env-precedence block is untouched).

- [ ] **Step 5: Commit**

```bash
git add harness/tui_main.py tests/test_tui_main.py
git commit -m "feat(tui_main): --persona resolves per-persona model + flows to agent subprocess & /reload

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: `--persona` in `run_traced` (the third reader)

The standalone dev path honors `--persona` so it isn't permanently pinned to the default workspace + model.

**Files:**
- Modify: `harness/run_traced.py` (argparse ~104-107; model + persona/memory resolution ~115, 126-127)
- Test: `tests/test_run_traced.py`

**Interfaces:**
- Consumes: `persona_select.resolve_workspace` (Task 3).
- Produces: `run_traced` honoring `--persona`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_run_traced.py` (the file already exists; append these tests and the `isolated` fixture only if an equivalent XDG-isolation fixture isn't already present — check first to avoid a duplicate-fixture error). Test the resolution seam without running a real agent by spying on `resolve_persona`:

```python
import pytest
from harness import run_traced, paths


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path


def test_persona_arg_selects_workspace(monkeypatch, tmp_path):
    ws = paths.config_dir() / "agents" / "fred"
    ws.mkdir(parents=True)
    seen = {}
    import harness.persona as persona_mod

    def spy_resolve(workspace_dir):
        seen["ws"] = workspace_dir
        raise SystemExit(0)        # stop run_traced before it builds a model
    monkeypatch.setattr(persona_mod, "resolve_persona", spy_resolve)
    with pytest.raises(SystemExit):
        run_traced.main(["--model", "mock", "--persona", "fred", "--cwd", str(tmp_path)])
    assert seen["ws"] == ws


def test_unknown_persona_exits_nonzero(monkeypatch, tmp_path):
    with pytest.raises(SystemExit) as exc:
        run_traced.main(["--model", "mock", "--persona", "ghost", "--cwd", str(tmp_path)])
    assert exc.value.code != 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_run_traced.py::test_persona_arg_selects_workspace -v`
Expected: FAIL — `run_traced` has no `--persona` arg and resolves `default_workspace_dir()` unconditionally.

- [ ] **Step 3: Write the implementation**

In `harness/run_traced.py`:

(a) Add the arg after `--cwd` (line ~106):

```python
    parser.add_argument("--persona", default=None,
                        help="persona workspace id to run as (default: the built-in default)")
```

(b) Resolve the workspace once, right after `args = parser.parse_args(argv)` (line ~107):

```python
    import sys as _sys
    from harness import persona_select as _persona_select
    try:
        workspace_dir = _persona_select.resolve_workspace(args.persona)
    except _persona_select.UnknownPersona as e:
        print(f'no persona "{e}"', file=_sys.stderr)
        raise SystemExit(2)
```

(c) Replace the two hardcoded `default_workspace_dir()` calls (lines 126-127):

```python
    persona_block = _persona.resolve_persona(workspace_dir).block
    memory_block = _memory.resolve_memory(workspace_dir, today=date.today()).block
```

Note: the per-persona MODEL for `run_traced` stays the engine default unless a shell `VIBEPROXY_MODEL` is set (line 115 is unchanged) — `run_traced` is a dev harness that doesn't persist model swaps, so wiring it to `done.conf [agents.<id>]` is out of scope; the spec's "per-persona resolve" for run_traced means the WORKSPACE (persona+memory), which is what these lines fix. Leave line 115 as-is.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_run_traced.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add harness/run_traced.py tests/test_run_traced.py
git commit -m "feat(run_traced): --persona selects the workspace for persona+memory injection

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Full-suite regression + no-op/back-compat locks + docs

Prove the whole suite is green and the two load-bearing guarantees hold, and document the new flag.

**Files:**
- Test: `tests/test_persona_phaseC1_guarantees.py` (NEW)
- Modify: `README.md` (Personas section), `docs/personas.md`

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Write the guarantee tests**

Create `tests/test_persona_phaseC1_guarantees.py`:

```python
import pytest
from harness import config, tui_main, model_resolve, vibeproxy, paths


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("VIBEPROXY_MODEL", raising=False)
    return tmp_path


def test_noop_no_config_resolves_engine_default():
    # no done.conf, no --persona → ladder returns the engine default
    assert model_resolve.resolve_model(
        shell_env=None, dotenv=None, persisted=None,
        engine_default=vibeproxy.DEFAULT_MODEL) == vibeproxy.DEFAULT_MODEL
    # and _resolve_model yields no override (env/default stand)
    assert tui_main._resolve_model(None, None) == ("vibeproxy", None)


def test_backcompat_existing_default_model_preserved():
    # a pre-C1 install: model lives in [agents.default]
    config.save_default(config.AgentConfig(backend="vibeproxy", model="legacy-model"))
    # after C1, a flagless launch still resolves it (model never moved)
    assert tui_main._resolve_model(None, None) == ("vibeproxy", "legacy-model")
    assert tui_main._resolve_model(None, "default") == ("vibeproxy", "legacy-model")
```

- [ ] **Step 2: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_persona_phaseC1_guarantees.py -q`
Expected: PASS (2 tests). (These pass because the model never moved — they LOCK that.)

- [ ] **Step 3: Run the FULL suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS — all pre-existing tests (393) plus the C1 additions. Zero failures, zero regressions. If any pre-existing test fails, STOP and fix the regression before continuing — do not modify the failing test unless it asserts behavior C1 intentionally changed (only the `set_model`/`set_yolo` persistence tests in Task 5 were intentionally retargeted).

- [ ] **Step 4: Update docs**

In `README.md`, in the Personas section, add a short subsection (keep the existing prose; append):

```markdown
### Selecting a persona

Run as a named persona workspace with `--persona <id>`:

    dn --persona fred

Without `--persona`, the built-in `default` persona is used. The id must be an
existing workspace under `~/.config/harness/agents/<id>/` — an unknown id is a
hard error (persona *creation* lands in a later phase). Each persona has its own
sessions, memory, and model (persisted in `done.conf` under `[agents.<id>]`); a
live `/models` swap is remembered per persona.
```

In `docs/personas.md`, add the same `--persona` explanation under a "Selection" heading, plus one line noting `persona.toml` may declare extra `skills` dirs.

- [ ] **Step 5: Commit**

```bash
git add tests/test_persona_phaseC1_guarantees.py README.md docs/personas.md
git commit -m "test+docs(persona): Phase C1 no-op/back-compat guarantee locks + --persona docs

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Notes for the executor

- **`persona_config.read_skills` (Task 4) is intentionally built-but-unwired in C1.** It is the persona.toml reader the spec lists as a component; wiring extra skill dirs into `paths.skills_dirs()` resolution is Phase D (D4) work. It ships tested and ready so D4 only has to call it — this is deliberate, not a forgotten consumer. If a reviewer flags it as dead code, that is the expected trade-off, recorded here.
- **Known C2 boundary (do not try to solve in C1):** `new_session` records the agent's single `self._workspace_dir` and takes no per-session workspace argument. That is correct for C1 (one process = one persona). Multiplexing personas in one process is C2's job — leave `new_session` as-is.
- **Order matters:** Tasks 1-4 are independent leaf modules (config, ladder, resolver, persona.toml). Task 5 depends on Task 1. Tasks 6-8 depend on Tasks 1-3. Task 9 depends on all. Execute in numeric order.
- **The `default` is never special-cased:** every place that handles `"default"` does so as an ordinary id value passed to a general function (`_persona_key`, `resolve_workspace`, `load_agent`). If you find yourself writing `if persona_id == "default":` in selection/precedence/injection logic, stop — that violates a Global Constraint.
