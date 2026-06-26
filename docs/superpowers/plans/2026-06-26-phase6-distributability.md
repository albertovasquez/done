# Phase 6: Full Distributability — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** A non-editable wheel of the harness (distribution `quiubo-done`) runs `dn` from any directory after the source checkout is deleted, without regressing the editable dev workflow.

**Architecture:** Introduce `harness/paths.py` as the single source of truth for asset resolution (XDG config dir, `.env` precedence, bundled+user skills roots, engine config via `find_spec`). Switch entrypoints off `REPO_ROOT`/`sys.path` hacks onto `paths.py`. Fix the wheel manifest (package discovery + package-data), pass env+cwd to the agent subprocess, and set the engine path source non-editable — gated by a mandatory delete-checkout smoke test with an executable vendoring fallback.

**Tech Stack:** Python 3.10+, setuptools, uv, importlib, python-dotenv, acp SDK, Textual.

**Spec:** `docs/superpowers/specs/2026-06-26-phase6-distributability-design.md`

## Global Constraints

- **Zero upstream edits.** Nothing under `upstream/` may be modified. (Changing OUR `pyproject.toml`'s source declaration is OUR config, not an upstream edit.)
- **Editable mode must not regress.** `uv tool install --editable .` keeps `dn` on live source; assets resolve to the checkout.
- **Distribution name is `quiubo-done`** (`pyproject.toml:6`); import package is `harness`; console scripts `dn`/`dn-agent`.
- **`run_traced.py` is OUT of the distributability success bar** (Phase-0 dev CLI; may use `paths.py` where trivial, not required to run from a wheel).
- **No new runtime dependency** for config-dir resolution (XDG is ~6 lines; no `platformdirs`).
- **STDOUT is the ACP wire** for the agent — no stray stdout prints in agent code paths (`MSWEA_SILENT_STARTUP=1` discipline).
- **`load_env(project_dir)` must run BEFORE any import that triggers `minisweagent`'s package init** (`acp_env.py`, `acp_main.py` engine imports).
- **Add tests only where they buy real safety; no speculative complexity.**
- **Test command (from worktree root):** `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/ -q`  — target `tests/` only (`upstream/tests` needs optional deps). Baseline: 104 passing.

## File Structure

- **Create** `harness/paths.py` — `config_dir()`, `load_env(project_dir)`, `bundled_skills_dir()`, `skills_dirs()`, `mini_yaml_path()`. The only module that knows XDG / wheel / find_spec.
- **Create** `tests/test_paths.py` — hermetic unit tests for the above (monkeypatched HOME/XDG/env; subprocess for the no-import check).
- **Create** `tests/test_packaging.py` — build the wheel, assert manifest contents.
- **Create** `scripts/smoke-wheel.sh` — the manual delete-checkout gate (documented, not pytest).
- **Modify** `harness/skills.py` — `load_catalog`/`compose` take an ordered list of roots (Traversable), merge by name.
- **Modify** `harness/acp_main.py` — drop `REPO_ROOT`/`sys.path`/`load_dotenv(REPO_ROOT/.env)`; call `paths.load_env`; use `paths.mini_yaml_path()` + `paths.skills_dirs()`; order load_env before engine imports.
- **Modify** `harness/tui_main.py` — drop the path-hacks; `paths.load_env(cwd)` before spawn.
- **Modify** `harness/tui/app.py` — `spawn_agent_process(..., env=dict(os.environ), cwd=self.cwd)`.
- **Modify** `pyproject.toml` — `packages.find include=["harness*"]`; package-data `"harness"=["skills/**/*"]`, `"harness.tui"=["*.tcss"]`; engine source `editable=false`.
- **Move** `skills/` → `harness/skills/`.
- **Update** `tests/test_skills.py` — ordered-roots signature.

---

### Task 1: `harness/paths.py` — config dir + `.env` loading

**Files:**
- Create: `harness/paths.py`
- Test: `tests/test_paths.py`

**Interfaces:**
- Produces: `config_dir() -> Path`; `load_env(project_dir: str | Path | None = None) -> None`.

- [ ] **Step 1: Write the failing tests** (`tests/test_paths.py`)

```python
import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

import os
from pathlib import Path
from harness import paths


def test_config_dir_honors_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert paths.config_dir() == tmp_path / "harness"


def test_config_dir_defaults_to_home_config(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert paths.config_dir() == tmp_path / ".config" / "harness"


def test_config_dir_does_not_create(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    d = paths.config_dir()
    assert not d.exists()


def test_load_env_precedence(monkeypatch, tmp_path):
    # process env wins over project .env wins over config .env; gaps filled only
    proj = tmp_path / "proj"; proj.mkdir()
    cfg = tmp_path / "cfg"; cfg.mkdir()
    (proj / ".env").write_text("A=proj\nB=proj\n")
    (cfg / ".env").write_text("A=cfg\nB=cfg\nC=cfg\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))   # config_dir -> tmp_path/harness
    (tmp_path / "harness").mkdir()
    (tmp_path / "harness" / ".env").write_text("A=cfg\nB=cfg\nC=cfg\n")
    monkeypatch.setenv("A", "env")        # already-set: must win
    monkeypatch.delenv("B", raising=False)
    monkeypatch.delenv("C", raising=False)
    paths.load_env(proj)
    assert os.environ["A"] == "env"       # process env untouched
    assert os.environ["B"] == "proj"      # project .env beats config .env
    assert os.environ["C"] == "cfg"       # only in config .env


def test_load_env_no_files_is_noop(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    paths.load_env(tmp_path)              # no .env anywhere -> no exception
```

- [ ] **Step 2: Run to verify they fail**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_paths.py -q`
Expected: FAIL (`No module named 'harness.paths'`).

- [ ] **Step 3: Implement `harness/paths.py` (config + env portion)**

```python
"""Single source of truth for runtime asset resolution: the XDG config dir,
.env loading precedence, the bundled+user skills roots, and the engine's
mini.yaml. Replaces the REPO_ROOT/__file__ assumptions so a wheel install works
after the source checkout is deleted."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


def config_dir() -> Path:
    """$XDG_CONFIG_HOME/harness if set & non-empty, else ~/.config/harness.
    Does NOT create the directory."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "harness"


def load_env(project_dir: str | Path | None = None) -> None:
    """Load .env in precedence (process env always wins — override=False):
    process env -> project_dir/.env -> config_dir()/.env. project_dir is the
    project the harness operates on (the TUI --cwd / the agent session cwd);
    the harness never chdir()s, so we anchor explicitly rather than Path.cwd()."""
    candidates = []
    if project_dir is not None:
        candidates.append(Path(project_dir) / ".env")
    candidates.append(config_dir() / ".env")
    for path in candidates:
        if path.is_file():
            load_dotenv(path, override=False)
```

- [ ] **Step 4: Run to verify they pass**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_paths.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add harness/paths.py tests/test_paths.py
git commit -m "feat(paths): config_dir + load_env (XDG, explicit project_dir)"
```

---

### Task 2: `harness/paths.py` — `mini_yaml_path` via `find_spec` (no engine import)

**Files:**
- Modify: `harness/paths.py`
- Test: `tests/test_paths.py` (add), plus a subprocess no-import test file.

**Interfaces:**
- Produces: `mini_yaml_path() -> Path`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_paths.py`:

```python
def test_mini_yaml_path_exists():
    p = paths.mini_yaml_path()
    assert p.name == "mini.yaml"
    assert p.is_file()
```

Create `tests/test_mini_yaml_no_import.py` (a subprocess proves a clean import state — an in-process assert is polluted by other tests importing the engine):

```python
import subprocess
import sys


def test_mini_yaml_path_does_not_import_minisweagent():
    code = (
        "import sys; sys.path.insert(0,'upstream/src'); sys.path.insert(0,'.');"
        "from harness import paths; p = paths.mini_yaml_path();"
        "assert p.is_file(), p;"
        "assert 'minisweagent' not in sys.modules, 'mini_yaml_path imported the engine';"
        "print('OK')"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "OK" in out.stdout
```

- [ ] **Step 2: Run to verify they fail**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_paths.py::test_mini_yaml_path_exists tests/test_mini_yaml_no_import.py -q`
Expected: FAIL (`mini_yaml_path` missing).

- [ ] **Step 3: Implement `mini_yaml_path`** (append to `harness/paths.py`)

```python
import importlib.util


def mini_yaml_path() -> Path:
    """Locate the engine's config/mini.yaml WITHOUT importing minisweagent
    (its __init__ runs dotenv/global-config side effects). Uses find_spec, which
    resolves the package location without executing it."""
    spec = importlib.util.find_spec("minisweagent")
    locations = list(spec.submodule_search_locations) if spec else []
    if not locations:
        raise RuntimeError("mini-swe-agent config not found; is the engine installed?")
    p = Path(locations[0]) / "config" / "mini.yaml"
    if not p.is_file():
        raise RuntimeError("mini-swe-agent config not found; is the engine installed?")
    return p
```

- [ ] **Step 4: Run to verify they pass**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_paths.py tests/test_mini_yaml_no_import.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/paths.py tests/test_paths.py tests/test_mini_yaml_no_import.py
git commit -m "feat(paths): mini_yaml_path via find_spec (no engine import)"
```

---

### Task 3: Move `skills/` into the package + ordered-roots skills API

**Files:**
- Move: `skills/` → `harness/skills/`
- Modify: `harness/skills.py`
- Modify: `harness/paths.py` (add `bundled_skills_dir`, `skills_dirs`)
- Test: `tests/test_skills.py` (update), `tests/test_paths.py` (add)

**Interfaces:**
- Consumes: `config_dir()` (Task 1).
- Produces: `paths.bundled_skills_dir() -> Path`; `paths.skills_dirs() -> list[Path]`;
  `skills.load_catalog(roots: list[Path]) -> list[tuple[str, str]]`;
  `skills.compose(roots: list[Path], names: list[str]) -> SkillLoad`.

- [ ] **Step 1: Move the skills dir (git mv preserves history)**

```bash
git mv skills harness/skills
```

- [ ] **Step 2: Write/adjust failing tests**

Update `tests/test_skills.py` to call the ordered-roots signature and add a merge test. The roots are plain `Path`s in tests (the bundled dir is a real path under an installed/editable layout; user dir is a real path):

```python
def test_load_catalog_merges_roots_user_overrides_bundled(tmp_path):
    bundled = tmp_path / "bundled"; (bundled / "a").mkdir(parents=True)
    (bundled / "a" / "SKILL.md").write_text("---\nname: a\ndescription: bundled A\n---\nbody\n")
    user = tmp_path / "user"; (user / "a").mkdir(parents=True)
    (user / "a" / "SKILL.md").write_text("---\nname: a\ndescription: user A\n---\nbody\n")
    cat = dict(skills.load_catalog([bundled, user]))   # later root wins
    assert cat["a"] == "user A"


def test_invalid_user_skill_does_not_shadow_bundled(tmp_path):
    bundled = tmp_path / "bundled"; (bundled / "a").mkdir(parents=True)
    (bundled / "a" / "SKILL.md").write_text("---\nname: a\ndescription: bundled A\n---\nbody\n")
    user = tmp_path / "user"; (user / "a").mkdir(parents=True)
    (user / "a" / "SKILL.md").write_text("not valid frontmatter")
    cat = dict(skills.load_catalog([bundled, user]))
    assert cat["a"] == "bundled A"     # invalid user skill ignored, bundled stays
```

Add to `tests/test_paths.py`:

```python
def test_skills_dirs_orders_bundled_then_user(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    (tmp_path / "harness" / "skills").mkdir(parents=True)   # user dir exists
    dirs = paths.skills_dirs()
    assert dirs[0] == paths.bundled_skills_dir()            # bundled first (lowest precedence)
    assert dirs[-1] == tmp_path / "harness" / "skills"      # user last (wins)
```

- [ ] **Step 3: Run to verify they fail**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_skills.py tests/test_paths.py -q`
Expected: FAIL (ordered-roots signature / `skills_dirs` missing).

- [ ] **Step 4: Implement ordered-roots in `harness/skills.py`**

Change both functions to iterate roots in order; later roots override by name. Replace the two function bodies:

```python
def load_catalog(roots: list[Path]) -> list[tuple[str, str]]:
    """Scan each root's <name>/SKILL.md; later roots override earlier by name.
    Invalid skill dirs are silently omitted (can't select what can't parse)."""
    merged: dict[str, str] = {}
    for root in roots:
        if not Path(root).is_dir():
            continue
        for child in sorted(Path(root).iterdir(), key=lambda p: p.name):
            if not child.is_dir():
                continue
            try:
                data, _ = _parse_skill_md(child / "SKILL.md")
                name, desc = data.get("name"), data.get("description")
                if not name or not desc:
                    raise ValueError("frontmatter missing name/description")
                if name != child.name:
                    raise ValueError("name mismatch")
            except (OSError, UnicodeDecodeError, yaml.YAMLError, ValueError):
                continue
            merged[name] = desc          # later root wins
    return sorted(merged.items())


def compose(roots: list[Path], names: list[str]) -> SkillLoad:
    """Compose selected skills' bodies. For each name, the LAST root that has a
    valid SKILL.md for it wins. Records failures in skipped; never raises."""
    load = SkillLoad()
    bodies: list[str] = []
    for name in names:
        chosen_body = None
        for root in roots:
            skill_md = Path(root) / name / "SKILL.md"
            if not skill_md.is_file():
                continue
            try:
                data, body = _parse_skill_md(skill_md)
                if data.get("name") != name:
                    raise ValueError("name mismatch")
            except (OSError, UnicodeDecodeError, yaml.YAMLError, ValueError):
                continue
            chosen_body = body           # later root overrides
        if chosen_body is None:
            load.skipped.append((name, "no valid SKILL.md in any root"))
            continue
        bodies.append(f"## {name}\n{chosen_body}")
        load.injected.append(name)
    if bodies:
        load.block = ("\n\n# Available Skills\n\n"
                      "The following skills apply to this task. Follow them.\n\n"
                      + "\n\n".join(bodies))
    return load
```

- [ ] **Step 5: Add `bundled_skills_dir` + `skills_dirs` to `harness/paths.py`**

```python
import importlib.resources


def bundled_skills_dir() -> Path:
    """The skills shipped inside the harness package (harness/skills/). Works in
    editable and installed (unzipped) wheels."""
    return Path(importlib.resources.files("harness")) / "skills"


def skills_dirs() -> list[Path]:
    """Ordered LOWEST precedence first: bundled, then the user dir. Absent roots
    are kept in the list — skills.load_catalog/compose skip non-dirs — so callers
    need not pre-filter."""
    return [bundled_skills_dir(), config_dir() / "skills"]
```

- [ ] **Step 6: Run to verify they pass**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_skills.py tests/test_paths.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add harness/skills.py harness/paths.py tests/test_skills.py tests/test_paths.py harness/skills
git commit -m "feat(skills): move into package + ordered-roots merge (user overrides bundled)"
```

---

### Task 4: Wire entrypoints onto `paths.py`

**Files:**
- Modify: `harness/acp_main.py`
- Modify: `harness/tui_main.py`
- Modify: `harness/tui/app.py`

**Interfaces:**
- Consumes: `paths.load_env`, `paths.mini_yaml_path`, `paths.skills_dirs` (Tasks 1-3).

- [ ] **Step 1: Update `harness/acp_main.py`** — load env before engine imports, use paths.

Keep `os.environ.setdefault("MSWEA_SILENT_STARTUP", "1")` at the very top. Remove `REPO_ROOT` and the two `sys.path.insert` lines and `from dotenv import load_dotenv`. Make engine-touching imports lazy OR ensure `load_env` runs first. Concretely:

Replace the top block (lines 17-28) with:

```python
os.environ.setdefault("MSWEA_SILENT_STARTUP", "1")   # MUST be before minisweagent import

import acp  # noqa: E402
from harness import paths  # noqa: E402
```

Move the harness/engine imports (`HarnessAgent`, `Router`, `complete`, `skills`) to AFTER `paths.load_env(...)` runs in `_main` — i.e. import them lazily inside `_main` (they pull in `minisweagent`). Update `_load_agent_cfg` and `_main`:

```python
def _load_agent_cfg() -> dict:
    import yaml
    cfg = yaml.safe_load(paths.mini_yaml_path().read_text())
    return cfg["agent"]


async def _main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="ACP harness agent")
    parser.add_argument("--model", choices=["mock", "vibeproxy"], default="mock")
    parser.add_argument("--cwd", default=None,
                        help="project dir the agent operates on (anchors .env)")
    args = parser.parse_args(argv)

    paths.load_env(args.cwd)          # BEFORE importing engine-touching modules

    from harness.acp_agent import HarnessAgent
    from harness.router import Router, complete
    from harness import skills

    worker_model_id = None if args.model == "mock" else os.getenv("VIBEPROXY_MODEL", "gpt-5.4")
    complete_fn = _stub_complete if os.getenv("HARNESS_ROUTER_STUB") == "1" else complete

    roots = paths.skills_dirs()
    agent = HarnessAgent(
        model_factory=_model_factory(args.model),
        agent_cfg=_load_agent_cfg(),
        skills_dir=roots,                                   # now an ordered list
        router=Router(complete_fn, catalog=skills.load_catalog(roots)),
        worker_model_id=worker_model_id,
    )
    await acp.run_agent(agent)
```

Note: `_model_factory`'s vibeproxy branch already imports `LitellmModel` lazily inside `make()` — leave it. `HarnessAgent.skills_dir` now receives an ordered `list[Path]`; verified `acp_agent.py` only stores it (`:32`) and passes it straight to `skills.compose(self._skills_dir, cls.skills)` (`:134`) — no `Path` ops, so the list flows through unchanged. Update the two type hints `skills_dir: Path` → `skills_dir: list[Path]` (`acp_agent.py:28`, `:252`) for accuracy (cosmetic, not behavioral).

- [ ] **Step 2: Update `harness/tui_main.py`** — drop path-hacks, load env before spawn.

Replace lines 15-24 (the `REPO_ROOT` block + path inserts + the app import) so the app import is not gated on a source tree, and load env in `main`:

```python
from harness import paths
from harness.tui.app import HarnessTui


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Harness Textual ACP client")
    parser.add_argument("--model", choices=["mock", "vibeproxy"], default="mock")
    parser.add_argument("--cwd", default=None,
                        help="project directory the agent operates on (default: current dir)")
    args = parser.parse_args(argv)

    cwd = str(Path(args.cwd).resolve()) if args.cwd else os.getcwd()
    paths.load_env(cwd)               # resolve VIBEPROXY_* before spawning the agent
    # Pass --cwd through so the agent subprocess anchors .env to the same project.
    agent_cmd = [sys.executable, "-m", "harness.acp_main", "--model", args.model, "--cwd", cwd]
    HarnessTui(agent_cmd=agent_cmd, cwd=cwd, model=args.model).run()
```

(Keep `import os, sys, argparse` and `from pathlib import Path` at the top.)

- [ ] **Step 3: Update `harness/tui/app.py`** — pass env + cwd to the spawn.

In `on_mount`, change the `spawn_agent_process` call to:

```python
self._cm = acp.spawn_agent_process(
    self._client, self.agent_cmd[0], *self.agent_cmd[1:],
    env=dict(os.environ),     # VIBEPROXY_* resolved by paths.load_env at startup
    cwd=self.cwd,             # agent runs in the project dir (anchors .env)
)
```

(`os` is already imported in `app.py`.)

- [ ] **Step 4: Run the full suite + a manual TUI-spawn sanity**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/ -q`
Expected: PASS (104 + the new paths/skills tests). The ACP smoke tests exercise the real `acp_main` subprocess + spawn — they must stay green, proving the rewired entrypoints + env handoff work end-to-end.

- [ ] **Step 5: Commit**

```bash
git add harness/acp_main.py harness/tui_main.py harness/tui/app.py
git commit -m "feat: wire entrypoints onto paths.py; pass env+cwd to agent subprocess"
```

---

### Task 5: Wheel manifest + engine non-editable (`pyproject.toml`) + packaging test

**Files:**
- Modify: `pyproject.toml`
- Test: `tests/test_packaging.py` (new)

**Interfaces:** none (build config).

- [ ] **Step 1: Write the failing packaging test** (`tests/test_packaging.py`)

```python
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _build_wheel(tmp_path) -> Path:
    for cmd in (
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(tmp_path)],
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path)],
    ):
        try:
            r = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True)
        except FileNotFoundError:
            continue
        if r.returncode == 0:
            wheels = list(tmp_path.glob("*.whl"))
            if wheels:
                return wheels[0]
    pytest.skip("no working wheel builder (python -m build / uv build) available")


def test_wheel_includes_tui_assets_and_skills(tmp_path):
    whl = _build_wheel(tmp_path)
    names = zipfile.ZipFile(whl).namelist()
    assert any(n.endswith("harness/tui/app.tcss") for n in names), names
    assert any(n.endswith("harness/tui/widgets/select_modal.py") for n in names), names
    assert any(n.endswith("/SKILL.md") and "harness/skills/" in n for n in names), names
```

- [ ] **Step 2: Run to verify it fails**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_packaging.py -q`
Expected: FAIL (current manifest excludes `harness.tui.widgets` + `app.tcss` + skills) — or SKIP if no builder; if it skips, install `build` into the venv (`.venv/bin/python -m pip install build`) so the gate is real, then re-run.

- [ ] **Step 3: Update `pyproject.toml`**

Replace `[tool.setuptools] packages = ["harness", "harness.tui"]` and the empty `[tool.setuptools.package-data]` with:

```toml
[tool.setuptools.packages.find]
include = ["harness*"]

[tool.setuptools.package-data]
"harness" = ["skills/**/*"]
"harness.tui" = ["*.tcss"]
```

And set the engine source non-editable:

```toml
[tool.uv.sources]
mini-swe-agent = { path = "upstream", editable = false }
```

- [ ] **Step 4: Run to verify it passes**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_packaging.py -q`
Expected: PASS (wheel now contains widgets, app.tcss, skills).

- [ ] **Step 5: Run the full suite (no regressions)**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml tests/test_packaging.py
git commit -m "build: package discovery + skills/tcss package-data; engine non-editable"
```

---

### Task 6: Smoke script + the delete-checkout gate (the linchpin)

**Files:**
- Create: `scripts/smoke-wheel.sh`

**Interfaces:** none (manual gate).

- [ ] **Step 1: Write `scripts/smoke-wheel.sh`**

```bash
#!/usr/bin/env bash
# Phase 6 distributability gate: prove a NON-editable wheel runs `dn` after the
# source checkout is deleted. Run from the repo root. NOT a pytest (mutates
# global install state, slow). Distribution name: quiubo-done.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"; SRC="$WORK/src"; PROJ="$WORK/proj"
trap 'rm -rf "$WORK"' EXIT

cp -R "$REPO" "$SRC"
( cd "$SRC" && { python -m build --wheel --outdir "$WORK/dist" || uv build --wheel --out-dir "$WORK/dist"; } )

uv tool install --force "$SRC"          # NON-editable
rm -rf "$SRC"                            # delete the source it installed from

mkdir -p "$HOME/.config/harness"
cp "$REPO/.env.example" "$HOME/.config/harness/.env" 2>/dev/null || true

mkdir -p "$PROJ"; cd "$PROJ"
echo "--- dn-agent must import the engine post-deletion (the linchpin) ---"
dn-agent --help >/dev/null && echo "OK: dn-agent runs (engine importable)"
echo "--- dn --model mock must launch from an unrelated cwd ---"
echo "Manually: run \`dn --model mock\` here, send a prompt, confirm the"
echo "task.classified chip + a reply render. (Interactive; not auto-asserted.)"
echo "SMOKE PASSED (engine import verified; run dn manually to finish)."
```

- [ ] **Step 2: Make it executable + run it**

```bash
chmod +x scripts/smoke-wheel.sh
./scripts/smoke-wheel.sh
```

Expected: prints `OK: dn-agent runs (engine importable)` and `SMOKE PASSED`. **This is the linchpin gate.** If `dn-agent --help` fails with a `minisweagent` import error, the non-editable path source did NOT copy the engine → trigger the executable fallback (Step 3). Otherwise skip Step 3.

- [ ] **Step 3 (ONLY if Step 2 failed the engine import): executable vendoring fallback**

Drop `[tool.uv.sources]`'s path entry and ship the engine as real discovered packages in our wheel. In `pyproject.toml`:

```toml
[tool.setuptools.packages.find]
where = [".", "upstream/src"]
include = ["harness*", "minisweagent*"]

[tool.setuptools.package-data]
"harness" = ["skills/**/*"]
"harness.tui" = ["*.tcss"]
"minisweagent" = ["config/**/*"]
```

Remove `"mini-swe-agent"` from `[project.dependencies]` and delete the `[tool.uv.sources]` block (the engine is now inside our wheel, not a separate dep). Re-run `./scripts/smoke-wheel.sh` until the engine import passes. Re-run `tests/test_packaging.py` and the full suite.

- [ ] **Step 4: Commit**

```bash
git add scripts/smoke-wheel.sh pyproject.toml
git commit -m "test: delete-checkout smoke gate (+ executable engine-vendor fallback if needed)"
```

---

### Task 7: Editable-mode regression check + README note

**Files:**
- Modify: `README.md` (install section)

**Interfaces:** none.

- [ ] **Step 1: Verify editable mode still resolves assets**

```bash
uv tool install --force --editable "$(pwd)"
cd /tmp && dn-agent --help && echo "editable dn-agent OK"
cd - >/dev/null
```

Expected: `editable dn-agent OK` — editable install still works (Global Constraint: no regression). If `dn-agent --help` doesn't exist as a flag, substitute a 1-line invocation that exits cleanly.

- [ ] **Step 2: Update `README.md`** install section to document both modes (replace the existing install paragraph):

```markdown
## Install

**Use it (portable):** `uv tool install .` — builds a wheel and installs `dn` /
`dn-agent` globally; works from any directory and survives deleting the checkout.
Put VibeProxy settings in `~/.config/harness/.env` (see `.env.example`).

**Develop it (always-latest):** `uv tool install --editable .` — `dn` runs your
live source; edits to skills in `harness/skills/` and code apply immediately.

Per-project overrides: drop a `.env` in the project dir, or extra skills in
`~/.config/harness/skills/` (they override bundled skills of the same name).
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document portable vs editable install + config dir"
```

---

## Self-Review

**Spec coverage:** paths.py (§components) → Tasks 1-3; entrypoints + env/cwd handoff (§entrypoints, §data flow) → Task 4; wheel manifest + engine non-editable (§pyproject) → Task 5; smoke gate + executable fallback (§testing Tier 2, §linchpin) → Task 6; editable no-regression + docs (§global constraints) → Task 7. mini_yaml via find_spec (§components) → Task 2. skills merge semantics (§skills.py) → Task 3. Unit tests (§testing Tier 1) → Tasks 1-3; packaging test → Task 5. run_traced out-of-scope → not touched (constraint honored).

**Placeholder scan:** none — every code step has concrete code; the one conditional step (Task 6 Step 3) is fully specified and explicitly gated on the smoke result, not a "TBD".

**Type consistency:** `load_env(project_dir)`, `mini_yaml_path()`, `bundled_skills_dir()`, `skills_dirs()`, `load_catalog(roots)`, `compose(roots, names)` are consistent across Tasks 1-5 and match the spec. `skills_dir` param on `HarnessAgent` now receives a list and flows to `skills.compose` (Task 4 note verifies the passthrough).
