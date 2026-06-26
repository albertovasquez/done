# done.conf Model Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist the harness's selected model (backend + model string) across logout/login in a `done.conf` TOML file, for the reserved `default` agent.

**Architecture:** A new self-contained `harness/config.py` reads `done.conf` with stdlib `tomllib` and writes it with a small hand-rolled atomic writer that preserves unrelated agent tables. The TUI (`tui_main.py`) loads the persisted default at startup unless `--model` was passed explicitly; the ACP agent (`acp_agent.py`) persists the model back to `done.conf` whenever the runtime `harness/set_model` hot-swap fires. Named (uuid-keyed) agents are a forward-compatible file shape only — not read or selected in this work.

**Tech Stack:** Python 3.11, stdlib `tomllib` (read) + hand-rolled writer (write), `dataclasses`, `pathlib`, `os`. pytest / pytest-asyncio for tests. No new dependencies.

## Global Constraints

- **Config location:** `paths.config_dir() / "done.conf"` — i.e. `$XDG_CONFIG_HOME/harness/done.conf` or `~/.config/harness/done.conf`. Resolve via `harness.paths.config_dir()`; never hardcode `~/.config`.
- **No new dependencies.** Read with stdlib `tomllib`; write with a hand-rolled serializer. Do not add `tomli-w`, `pydantic`, or similar.
- **Reserved key is `default`** (constant `RESERVED_KEY = "default"`). Future agents are uuid-keyed and carry a `name`; the reserved default carries no `name`.
- **`schema_version = 1`** is written at top level and preserved on every write.
- **Backend values** are exactly `"mock"` and `"vibeproxy"` (mirror the `--model` flag choices).
- **Persistence is best-effort, never fatal.** A missing/empty/corrupt file must not break boot; a failed write must not break the runtime model swap.
- **Python venv / test command (from the worktree root):**
  `.venv/bin/python -m pytest tests/ -q` (target `tests/` only). If the worktree has no `.venv`, build one:
  `uv venv --python 3.11 .venv && uv pip install --python .venv -e . pytest pytest-asyncio`.
- **Pre-existing flaky test:** `tests/test_tui_pilot.py::test_pilot_permission_modal_reject` (async timing) may fail once and pass on rerun — not caused by this work.

## File Structure

- **Create** `harness/config.py` — `AgentConfig` dataclass + `conf_path()`, `load()`, `load_default()`, `save_default()`. Sole owner of `done.conf` I/O. No TUI/ACP imports.
- **Create** `tests/test_config.py` — unit tests for the config module (no process spawning).
- **Modify** `harness/tui_main.py` — startup precedence: `--model` flag > `done.conf` default > hardcoded default; export persisted `model` into the spawned env.
- **Modify** `harness/acp_main.py` — pass the launch backend (`args.model`) into `HarnessAgent`.
- **Modify** `harness/acp_agent.py` — `HarnessAgent.__init__` accepts `backend`; `set_model` handler persists via `config.save_default(...)` (best-effort).
- **Create** `tests/test_acp_agent.py` (does not exist yet) — cover the persist-on-set_model behavior, including the write-fails-still-ok path.
- **Append to** `tests/test_tui_main.py` (already exists) — the `_resolve_model` precedence tests.

**Test-file preamble:** the existing `tests/test_tui_main.py` begins with
`sys.path.insert(0, "upstream/src")` / `sys.path.insert(0, ".")` before importing
`harness.*`. The worktree tests rely on this. **Every new test file in this plan
(`tests/test_config.py`, `tests/test_acp_agent.py`) MUST start with the same two
inserts** before any `from harness import ...`.

---

### Task 1: The config module — schema, path, load

**Files:**
- Create: `harness/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `harness.paths.config_dir() -> pathlib.Path` (existing).
- Produces:
  - `SCHEMA_VERSION: int = 1`
  - `RESERVED_KEY: str = "default"`
  - `@dataclass(frozen=True) class AgentConfig: backend: str; model: str; name: str | None = None`
  - `conf_path() -> pathlib.Path`
  - `load() -> dict[str, AgentConfig]` — keys are agent keys (e.g. `"default"`, or a uuid); `{}` on missing/empty/malformed file or malformed individual tables.
  - `load_default() -> AgentConfig | None` — `load().get(RESERVED_KEY)`.

- [ ] **Step 1: Write the failing tests for path + load**

Create `tests/test_config.py`:

```python
import sys

sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

from pathlib import Path

import pytest

from harness import config


def _write(tmp_path: Path, text: str) -> Path:
    """Point config at an isolated XDG dir and write done.conf into it."""
    cfg = tmp_path / "harness"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "done.conf").write_text(text)
    return cfg


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path


def test_conf_path_under_config_dir(tmp_path):
    assert config.conf_path() == tmp_path / "harness" / "done.conf"


def test_load_missing_file_returns_empty(tmp_path):
    assert config.load() == {}


def test_load_empty_file_returns_empty(tmp_path):
    _write(tmp_path, "")
    assert config.load() == {}


def test_load_malformed_toml_returns_empty(tmp_path):
    _write(tmp_path, "this is = = not toml [[[")
    assert config.load() == {}


def test_load_valid_default(tmp_path):
    _write(tmp_path, (
        'schema_version = 1\n'
        '[agents.default]\n'
        'backend = "vibeproxy"\n'
        'model = "gpt-5.4"\n'
    ))
    agents = config.load()
    assert agents["default"] == config.AgentConfig(backend="vibeproxy", model="gpt-5.4")


def test_load_skips_agent_missing_required_fields(tmp_path):
    _write(tmp_path, (
        '[agents.default]\n'
        'backend = "vibeproxy"\n'        # no model -> skipped
        '[agents.other]\n'
        'backend = "mock"\n'
        'model = "x"\n'
    ))
    agents = config.load()
    assert "default" not in agents
    assert agents["other"] == config.AgentConfig(backend="mock", model="x")


def test_load_named_uuid_agent_keeps_name(tmp_path):
    _write(tmp_path, (
        '[agents.6f1c-uuid]\n'
        'name = "bill"\n'
        'backend = "vibeproxy"\n'
        'model = "claude-opus-4-8"\n'
    ))
    agents = config.load()
    assert agents["6f1c-uuid"] == config.AgentConfig(
        backend="vibeproxy", model="claude-opus-4-8", name="bill")


def test_load_default_returns_none_when_absent(tmp_path):
    _write(tmp_path, '[agents.other]\nbackend = "mock"\nmodel = "x"\n')
    assert config.load_default() is None


def test_load_default_returns_entry(tmp_path):
    _write(tmp_path, '[agents.default]\nbackend = "mock"\nmodel = "x"\n')
    assert config.load_default() == config.AgentConfig(backend="mock", model="x")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_config.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'harness.config'` (or `AttributeError` once the module exists but functions don't).

- [ ] **Step 3: Implement the schema, path, and load**

Create `harness/config.py`:

```python
"""Persisted agent -> model selection (`done.conf`).

A small, self-contained reader/writer for the TOML file at
`paths.config_dir()/done.conf`. Reads with stdlib `tomllib`; writes with a tiny
hand-rolled serializer (the schema is flat: a top-level `schema_version` plus
`[agents.<key>]` tables of string scalars), so there is no write-only TOML
dependency. Knows nothing about the TUI or ACP agent.

Reserved key `default` is the always-present primary agent (no `name`). Future
agents are uuid-keyed and carry a human `name`; this module round-trips them but
nothing here selects them yet.

Persistence is best-effort: a missing/empty/corrupt file yields {}, never raises
into the boot path; callers handle write failures (see save_default)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from harness import paths

SCHEMA_VERSION = 1
RESERVED_KEY = "default"


@dataclass(frozen=True)
class AgentConfig:
    backend: str            # "mock" | "vibeproxy"
    model: str              # model string, e.g. "gpt-5.4"
    name: str | None = None  # None for the reserved default; set for uuid agents


def conf_path() -> Path:
    """Absolute path to done.conf under the XDG config dir (not created here)."""
    return paths.config_dir() / "done.conf"


def load() -> dict[str, AgentConfig]:
    """All agents keyed by their table key. Returns {} if the file is missing,
    empty, or unparseable. Individual agent tables missing `backend` or `model`
    are skipped (not fatal)."""
    path = conf_path()
    try:
        raw = path.read_bytes()
    except OSError:
        return {}
    if not raw.strip():
        return {}
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        return {}
    agents_raw = data.get("agents")
    if not isinstance(agents_raw, dict):
        return {}
    out: dict[str, AgentConfig] = {}
    for key, table in agents_raw.items():
        if not isinstance(table, dict):
            continue
        backend = table.get("backend")
        model = table.get("model")
        if not isinstance(backend, str) or not isinstance(model, str):
            continue
        name = table.get("name")
        out[key] = AgentConfig(
            backend=backend,
            model=model,
            name=name if isinstance(name, str) else None,
        )
    return out


def load_default() -> AgentConfig | None:
    """The reserved [agents.default] entry, or None when absent/unreadable."""
    return load().get(RESERVED_KEY)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_config.py -q`
Expected: PASS (all load/path tests green).

- [ ] **Step 5: Commit**

```bash
git add harness/config.py tests/test_config.py
git commit -m "feat(config): done.conf schema + tomllib reader

AgentConfig dataclass, conf_path(), load(), load_default(). Reads the
reserved 'default' agent and forward-compatible uuid-keyed agents;
corrupt/empty/missing file -> {}, never fatal.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: The config writer — `save_default` (atomic, preserves others)

**Files:**
- Modify: `harness/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `AgentConfig`, `conf_path()`, `load()`, `RESERVED_KEY`, `SCHEMA_VERSION` (Task 1).
- Produces: `save_default(cfg: AgentConfig) -> None` — upserts `[agents.default]` from `cfg`, preserves all other agent tables verbatim, writes `schema_version`, creates the config dir, writes atomically.

- [ ] **Step 1: Write the failing tests for save_default**

Append to `tests/test_config.py`:

```python
def test_save_default_round_trips(tmp_path):
    config.save_default(config.AgentConfig(backend="vibeproxy", model="gpt-5.4"))
    assert config.load_default() == config.AgentConfig(backend="vibeproxy", model="gpt-5.4")


def test_save_default_writes_schema_version(tmp_path):
    config.save_default(config.AgentConfig(backend="mock", model="x"))
    text = config.conf_path().read_text()
    assert "schema_version = 1" in text


def test_save_default_creates_config_dir(tmp_path):
    # XDG dir exists (tmp_path) but the harness/ subdir does not yet.
    assert not config.conf_path().parent.exists()
    config.save_default(config.AgentConfig(backend="mock", model="x"))
    assert config.conf_path().is_file()


def test_save_default_preserves_other_agents(tmp_path):
    _write(tmp_path, (
        'schema_version = 1\n'
        '[agents.default]\n'
        'backend = "mock"\n'
        'model = "old"\n'
        '[agents.6f1c-uuid]\n'
        'name = "bill"\n'
        'backend = "vibeproxy"\n'
        'model = "claude-opus-4-8"\n'
    ))
    config.save_default(config.AgentConfig(backend="vibeproxy", model="gpt-5.4"))
    agents = config.load()
    assert agents["default"] == config.AgentConfig(backend="vibeproxy", model="gpt-5.4")
    assert agents["6f1c-uuid"] == config.AgentConfig(
        backend="vibeproxy", model="claude-opus-4-8", name="bill")


def test_save_default_escapes_special_chars(tmp_path):
    tricky = 'weird"model\\name'
    config.save_default(config.AgentConfig(backend="vibeproxy", model=tricky))
    assert config.load_default() == config.AgentConfig(backend="vibeproxy", model=tricky)


def test_save_default_no_partial_file_on_replace(tmp_path):
    # Two sequential saves; the file is always valid and reflects the latest.
    config.save_default(config.AgentConfig(backend="mock", model="a"))
    config.save_default(config.AgentConfig(backend="vibeproxy", model="b"))
    assert config.load_default() == config.AgentConfig(backend="vibeproxy", model="b")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_config.py -k save_default -q`
Expected: FAIL — `AttributeError: module 'harness.config' has no attribute 'save_default'`.

- [ ] **Step 3: Implement save_default + the hand-rolled writer**

Append to `harness/config.py`:

```python
def _quote(value: str) -> str:
    """Serialize a Python str as a TOML basic string (escape \\ and ")."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _serialize(agents: dict[str, AgentConfig]) -> str:
    """Render the flat schema: top-level schema_version then one [agents.<key>]
    table per agent (name only when set). Deterministic key order: the reserved
    default first, then the rest sorted, so diffs stay stable."""
    lines = [f"schema_version = {SCHEMA_VERSION}", ""]
    ordered = ([RESERVED_KEY] if RESERVED_KEY in agents else []) + sorted(
        k for k in agents if k != RESERVED_KEY
    )
    for key in ordered:
        cfg = agents[key]
        lines.append(f"[agents.{key}]")
        if cfg.name is not None:
            lines.append(f"name = {_quote(cfg.name)}")
        lines.append(f"backend = {_quote(cfg.backend)}")
        lines.append(f"model = {_quote(cfg.model)}")
        lines.append("")
    return "\n".join(lines)


def save_default(cfg: AgentConfig) -> None:
    """Upsert [agents.default] with `cfg`, preserving every other agent table.
    Writes atomically (temp file + os.replace) under a created config dir.
    Best-effort: callers that must not fail on I/O errors should guard the call."""
    agents = load()
    agents[RESERVED_KEY] = AgentConfig(backend=cfg.backend, model=cfg.model)  # default carries no name
    text = _serialize(agents)

    path = conf_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
```

Note: `[agents.<key>]` keys here are bare keys. The reserved key `default` and uuid keys (hex + dashes) are bare-key-safe in TOML. Uuid keys containing only `[A-Za-z0-9_-]` need no quoting; this is consistent with how Task 1 reads them back. (Quoting arbitrary table keys is a future-task concern when agent creation is added.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_config.py -q`
Expected: PASS (all Task 1 + Task 2 tests green).

- [ ] **Step 5: Commit**

```bash
git add harness/config.py tests/test_config.py
git commit -m "feat(config): atomic save_default preserving other agents

Hand-rolled TOML writer (flat schema, no new deps), atomic temp+replace,
mkdir -p, escapes basic-string specials, deterministic key order.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Persist on runtime model change (ACP agent)

**Files:**
- Modify: `harness/acp_agent.py` (`HarnessAgent.__init__` at `harness/acp_agent.py:29-38`; `set_model` handler at `harness/acp_agent.py:48-58`)
- Modify: `harness/acp_main.py` (the `HarnessAgent(...)` construction at `harness/acp_main.py:93-100`)
- Test: `tests/test_acp_agent.py` (create if absent)

**Interfaces:**
- Consumes: `config.save_default`, `config.AgentConfig` (Tasks 1-2).
- Produces: `HarnessAgent(__init__)` now accepts a keyword-only `backend: str` and stores `self._backend`; the `harness/set_model` handler persists `config.AgentConfig(backend=self._backend, model=model)` after updating `self._worker_model_id`, swallowing any exception so the swap still returns `{"ok": True, "model": ...}`.

- [ ] **Step 1: Write the failing tests for persist-on-set_model**

Create `tests/test_acp_agent.py` (it does not exist yet):

```python
import sys

sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

import asyncio

import pytest

from harness.acp_agent import HarnessAgent
from harness import config


def _make_agent(backend="vibeproxy"):
    """A HarnessAgent with cheap stand-ins; only set_model behavior is exercised."""
    return HarnessAgent(
        model_factory=lambda *a, **k: None,
        agent_cfg={},
        skills_dir=[],
        router=object(),
        worker_model_id="gpt-5.4",
        yolo=False,
        backend=backend,
    )


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path


def test_set_model_persists_backend_and_model():
    agent = _make_agent(backend="vibeproxy")
    result = asyncio.run(agent.ext_method("harness/set_model", {"model": "claude-opus-4-8"}))
    assert result == {"ok": True, "model": "claude-opus-4-8"}
    assert config.load_default() == config.AgentConfig(
        backend="vibeproxy", model="claude-opus-4-8")


def test_set_model_empty_model_does_not_persist():
    agent = _make_agent()
    asyncio.run(agent.ext_method("harness/set_model", {"model": ""}))
    assert config.load_default() is None  # nothing written for a no-op swap


def test_set_model_survives_save_failure(monkeypatch):
    def boom(_cfg):
        raise OSError("disk full")
    monkeypatch.setattr(config, "save_default", boom)
    agent = _make_agent()
    result = asyncio.run(agent.ext_method("harness/set_model", {"model": "x"}))
    assert result == {"ok": True, "model": "x"}  # swap still succeeds
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_acp_agent.py -q`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'backend'`.

- [ ] **Step 3: Add `backend` to `HarnessAgent.__init__`**

In `harness/acp_agent.py`, change the constructor signature and store the field. Replace `harness/acp_agent.py:29-38`:

```python
    def __init__(self, *, model_factory, agent_cfg, skills_dir: list[Path], router: Router,
                 worker_model_id, yolo: bool = False, backend: str = "vibeproxy"):
        self._model_factory = model_factory
        self._agent_cfg = agent_cfg
        self._skills_dir = skills_dir
        self._router = router
        self._worker_model_id = worker_model_id
        self._yolo = yolo                 # --yolo: auto-allow every command, no prompts
        self._backend = backend           # launch backend; paired with model on persist
        self._store = SessionStore()
        self._conn = None
```

- [ ] **Step 4: Persist in the `set_model` handler**

Add the import at the top of `harness/acp_agent.py` (with the other `from harness import ...` lines, near `harness/acp_agent.py:19`):

```python
from harness import config
```

Replace the `set_model` branch in `ext_method` (`harness/acp_agent.py:53-57`):

```python
        if method == "harness/set_model":
            model = (params or {}).get("model")
            if model:
                self._worker_model_id = model
                try:                       # best-effort: a failed write never breaks the swap
                    config.save_default(config.AgentConfig(backend=self._backend, model=model))
                except Exception:
                    pass
            return {"ok": True, "model": self._worker_model_id}
```

- [ ] **Step 5: Pass the launch backend from acp_main**

In `harness/acp_main.py`, replace the `HarnessAgent(...)` construction (`harness/acp_main.py:93-100`) to pass `backend=args.model`:

```python
    agent = HarnessAgent(
        model_factory=_model_factory(args.model),
        agent_cfg=_load_agent_cfg(),
        skills_dir=roots,                                   # now an ordered list
        router=Router(complete_fn, catalog=skills.load_catalog(roots)),
        worker_model_id=worker_model_id,
        yolo=args.yolo,
        backend=args.model,
    )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_acp_agent.py -q`
Expected: PASS (all three set_model tests green).

- [ ] **Step 7: Run the full suite to confirm no regression**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS except the known flaky `test_pilot_permission_modal_reject` (rerun it alone if it fails: `.venv/bin/python -m pytest tests/test_tui_pilot.py::test_pilot_permission_modal_reject -q`).

- [ ] **Step 8: Commit**

```bash
git add harness/acp_agent.py harness/acp_main.py tests/test_acp_agent.py
git commit -m "feat(acp): persist model to done.conf on set_model

HarnessAgent takes the launch backend; the set_model hot-swap writes
[agents.default] (backend+model). Best-effort: a write failure never
breaks the swap. acp_main passes backend=args.model.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Load persisted default at startup (TUI), with precedence

**Files:**
- Modify: `harness/tui_main.py` (`main()` at `harness/tui_main.py:38-54`)
- Test: `tests/test_tui_main.py` (ALREADY EXISTS — append; do not recreate)

**Interfaces:**
- Consumes: `config.load_default`, `config.AgentConfig` (Tasks 1-2).
- Produces: a pure resolver `_resolve_model(explicit_backend: str | None) -> tuple[str, str | None]` returning `(backend, model_or_None)`, and a `main()` that uses it. Precedence: explicit `--model` flag > `done.conf` default > hardcoded `("vibeproxy", None)`.

**Why a `None` argparse default:** argparse cannot tell a user-typed `--model vibeproxy` from the fallback. Set the flag default to `None` so "explicit" is detectable; resolve the real value after the config lookup. The existing `main()` tests in this file all pass `--model` explicitly, so they stay valid under the new default.

- [ ] **Step 1: Append the failing resolver tests**

`tests/test_tui_main.py` already exists (with its own `sys.path.insert` preamble,
`import tui_main`, and relaunch/main tests — leave all of that untouched). The
module already imports `from harness import tui_main`. **Append** the following to
the END of the file (add `from harness import config` to the existing imports if
not already present):

```python
@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path


def _write_default(xdg, backend, model):
    cfg = xdg / "harness"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "done.conf").write_text(
        f'[agents.default]\nbackend = "{backend}"\nmodel = "{model}"\n'
    )


def test_resolve_explicit_flag_wins_over_config(isolated_config):
    from harness import config  # noqa: F401  (ensures import even if not at top)
    _write_default(isolated_config, "mock", "from-config")
    # User typed --model vibeproxy -> config is ignored, no model override.
    assert tui_main._resolve_model("vibeproxy") == ("vibeproxy", None)


def test_resolve_uses_config_when_flag_absent(isolated_config):
    _write_default(isolated_config, "mock", "from-config")
    assert tui_main._resolve_model(None) == ("mock", "from-config")


def test_resolve_falls_back_to_hardcoded_when_no_config(isolated_config):
    assert tui_main._resolve_model(None) == ("vibeproxy", None)
```

Note: this fixture is NOT `autouse` (the file's existing `main()` tests must not
be forced under an isolated XDG dir). Each new test requests `isolated_config`
explicitly.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_tui_main.py -q`
Expected: FAIL — `AttributeError: module 'harness.tui_main' has no attribute '_resolve_model'`.

- [ ] **Step 3: Implement the resolver and wire main()**

In `harness/tui_main.py`, add the import alongside the existing ones (near `harness/tui_main.py:15`):

```python
from harness import config
```

Add the resolver (above `main`):

```python
def _resolve_model(explicit_backend: str | None) -> tuple[str, str | None]:
    """Resolve (backend, model_override) by precedence: an explicit --model flag
    wins (and applies no model override — env/defaults stand); else the persisted
    done.conf default; else the hardcoded ("vibeproxy", None). model_override is
    the persisted model string to export as VIBEPROXY_MODEL, or None to leave the
    env/default untouched."""
    if explicit_backend is not None:
        return explicit_backend, None
    persisted = config.load_default()
    if persisted is not None:
        return persisted.backend, persisted.model
    return "vibeproxy", None
```

Change the `--model` argparse default to `None` (`harness/tui_main.py:40`):

```python
    parser.add_argument("--model", choices=["mock", "vibeproxy"], default=None)
```

Wire resolution into `main()` after `paths.load_env(cwd)` (replace `harness/tui_main.py:48-53`):

```python
    paths.load_env(cwd)               # resolve VIBEPROXY_* before spawning the agent
    backend, model_override = _resolve_model(args.model)
    args.model = backend              # normalize so _relaunch_args carries the resolved backend
    if model_override is not None:
        os.environ.setdefault("VIBEPROXY_MODEL", model_override)  # process env still wins
    # Pass --cwd through so the agent subprocess anchors .env to the same project.
    agent_cmd = [sys.executable, "-m", "harness.acp_main", "--model", backend, "--cwd", cwd]
    if args.yolo:
        agent_cmd.append("--yolo")    # auto-allow flows to the agent, which owns the gate
    app = HarnessTui(agent_cmd=agent_cmd, cwd=cwd, model=backend)
```

Rationale for `args.model = backend`: `_relaunch_command` / `_relaunch_args` (`harness/tui_main.py:19-35`) read `args.model` to rebuild the launch flags on `/reload`. Normalizing it to the resolved backend keeps re-exec consistent and (because it is now non-`None`) avoids re-resolving from config on every reload.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tui_main.py -q`
Expected: PASS (all three resolver tests green).

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS except the known flaky `test_pilot_permission_modal_reject` (rerun alone if it fails).

- [ ] **Step 6: Commit**

```bash
git add harness/tui_main.py tests/test_tui_main.py
git commit -m "feat(tui): load persisted model at startup

_resolve_model: --model flag > done.conf default > hardcoded. Exports
the persisted model as VIBEPROXY_MODEL (process env still wins) and
normalizes args.model so /reload re-exec carries the resolved backend.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: End-to-end round-trip + docs

**Files:**
- Test: `tests/test_config.py` (add a round-trip integration test)
- Modify: `README.md` (document `done.conf`) — only if README has a config/settings section; otherwise skip the README edit and keep the test.

**Interfaces:**
- Consumes: everything above. No new production code.

- [ ] **Step 1: Write the cross-module round-trip test**

Append to `tests/test_config.py`:

```python
def test_round_trip_set_model_then_resolve(tmp_path):
    """ACP persists a model; a later TUI startup resolves it back."""
    from harness import tui_main

    # 1) Persist as the ACP set_model handler would.
    config.save_default(config.AgentConfig(backend="vibeproxy", model="claude-opus-4-8"))

    # 2) A fresh launch with NO --model flag picks it up.
    assert tui_main._resolve_model(None) == ("vibeproxy", "claude-opus-4-8")

    # 3) A launch WITH an explicit flag ignores it.
    assert tui_main._resolve_model("mock") == ("mock", None)
```

- [ ] **Step 2: Run it to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_config.py::test_round_trip_set_model_then_resolve -q`
Expected: PASS.

- [ ] **Step 3: Document `done.conf` (only if README has a config section)**

Check `README.md` for an existing configuration/settings section (e.g. a `.env` / `~/.config/harness` mention). If present, add a short subsection; if not, skip and note "no README config section — skipped" in the commit body. Example block to add:

```markdown
### Model persistence (`done.conf`)

The harness remembers your selected model across sessions in
`~/.config/harness/done.conf` (TOML). The reserved `default` agent stores the
backend and model:

\`\`\`toml
schema_version = 1

[agents.default]
backend = "vibeproxy"
model   = "gpt-5.4"
\`\`\`

Changing the model at runtime (the `/models` hot-swap) writes it back here.
Passing `--model` explicitly at launch overrides the saved value for that
session but does not erase it; a later runtime change re-saves.
```

- [ ] **Step 4: Run the full suite one last time**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS except the known flaky `test_pilot_permission_modal_reject`.

- [ ] **Step 5: Commit**

```bash
git add tests/test_config.py README.md
git commit -m "test(config): end-to-end set_model -> resolve round-trip; docs

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Notes for the implementer

- **Run from the worktree root** (`.claude/worktrees/done-conf-model-persistence`). All paths above are relative to it.
- **Verify new files land in the worktree, not the primary checkout** — after any file creation, `ls ./<relpath>` from the worktree root before `git add`. (A prior session's Write landed in the primary checkout; if `git add` says "pathspec did not match", the file is in the wrong tree — `cp` it in, `rm` from primary, re-check `git -C <primary-root> status --short` is clean.)
- **`tests/test_acp_agent.py` may already exist.** If so, append the new tests and reuse any existing agent-construction helper instead of `_make_agent` (match the existing fixture style; ensure `backend=` is passed).
- **Do not touch** `harness/run_traced.py` or `harness/router.py` model defaults — they are separate entrypoints out of scope here.
- **Do not** add named-agent selection, agent CRUD, or per-agent runtime backend switching — explicitly future work.
