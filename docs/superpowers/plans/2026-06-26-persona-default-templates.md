# Persona Default Templates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A fresh install seeds `~/.config/harness/agents/default/` with three editable persona template files that are inert (inject nothing) until edited, preserving Phase A's byte-identical no-op.

**Architecture:** Templates ship as package-data (`harness/templates/agents/default/*.md`). A `seed_default_workspace()` copies them into the user config dir create-if-absent at startup. `compose_persona`'s blank check is generalized: a file that is only HTML comments after stripping is treated as blank and never injected — so the shipped templates change nothing until the user replaces the comment with real text.

**Tech Stack:** Python 3.11, pytest, `importlib.resources`, `re`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-06-26-persona-default-templates-design.md`

## Global Constraints

- **Tests in `tests/` only.** Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/ -q` from the worktree root (no local `.venv` in the worktree; use the primary checkout's interpreter at that absolute path).
- Every test file starts with exactly:
  ```python
  import sys
  sys.path.insert(0, "upstream/src")
  sys.path.insert(0, ".")
  ```
- **Zero upstream edits** — never touch `upstream/`.
- **Inertness marker is HTML comments only** (`<!-- … -->`), NOT `#` — `#` is a Markdown heading a user may want injected.
- **Inertness is all-or-nothing:** skip the whole file iff it is only comments + whitespace; never strip comments out of otherwise-real content.
- **Seeding is create-if-absent + never-overwrite + never-raise:** seed only when `~/.config/harness/agents/default/` does NOT exist; never overwrite an existing file; a copy failure must not break startup.
- **The no-op must survive:** a freshly seeded (unedited) workspace must be byte-identical to no workspace, including NO `persona_load` event. This is non-negotiable — it's the Phase A guarantee.
- `PERSONA_FILES = ["SOUL.md", "IDENTITY.md", "USER.md"]` already exists in `harness/persona.py`.
- Commit after each task with a `feat:`/`test:` conventional message.

---

### Task 1: Inertness rule — `_meaningful` + generalized blank check

Generalize `compose_persona`'s blank check so an HTML-comment-only file is treated as blank (skipped, never injected).

**Files:**
- Modify: `harness/persona.py` (add `_HTML_COMMENT`, `_meaningful`; change the blank check ~line 83)
- Test: `tests/test_persona.py` (append)

**Interfaces:**
- Consumes: nothing new.
- Produces: `_meaningful(raw: str) -> bool` (True iff injectable content remains after HTML comments are stripped).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_persona.py`:

```python
def test_html_comment_only_file_is_blank(tmp_path):
    # a template file (only an HTML comment) must be treated as blank -> not injected
    (tmp_path / "SOUL.md").write_text(
        "<!-- SOUL.md — describe the agent's tone here. -->\n", encoding="utf-8")
    load = compose_persona(tmp_path)
    assert ("SOUL.md", "blank") in load.skipped
    assert load.injected == []
    assert load.block == ""


def test_comment_plus_real_line_injects_whole_file(tmp_path):
    # once the user adds real content, the file injects (comment included is fine)
    (tmp_path / "SOUL.md").write_text(
        "<!-- hint -->\nYou are terse.", encoding="utf-8")
    load = compose_persona(tmp_path)
    assert load.injected == ["SOUL.md"]
    assert "You are terse." in load.block


def test_markdown_heading_is_not_a_comment(tmp_path):
    # '#' is a markdown heading, NOT a comment marker — it must inject
    (tmp_path / "SOUL.md").write_text("# Persona\nBe concise.", encoding="utf-8")
    load = compose_persona(tmp_path)
    assert load.injected == ["SOUL.md"]
    assert "# Persona" in load.block
```

(The existing `test_blank_file_skipped` and `test_whitespace_only_file_is_blank`
must still pass — the new rule is a superset.)

- [ ] **Step 2: Run to verify failure**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_persona.py -k "comment or heading" -q`
Expected: FAIL — the comment-only file currently injects (its `raw.strip()` is non-empty).

- [ ] **Step 3: Edit `harness/persona.py`**

Add near the top imports (after `from pathlib import Path`):
```python
import re
```
Add after the `MAX_FILE_CHARS` constant:
```python
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def _meaningful(raw: str) -> bool:
    """True if the file has injectable content — anything but whitespace remains
    after HTML comments are removed. A comment-only template => False (skipped,
    never injected), so shipped templates preserve the byte-identical no-op.
    HTML comments only: '#' is a Markdown heading and must NOT be treated as a
    comment."""
    return bool(_HTML_COMMENT.sub("", raw).strip())
```
Change the blank check in `compose_persona` (currently `if not raw.strip():`) to:
```python
        if not _meaningful(raw):                      # blank, whitespace, or comment-only
            load.skipped.append((name, "blank"))
            continue
```

- [ ] **Step 4: Run to verify pass**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_persona.py -q`
Expected: PASS (existing persona tests + 3 new).

- [ ] **Step 5: Commit**

```bash
git add harness/persona.py tests/test_persona.py
git commit -m "feat(persona): treat HTML-comment-only files as blank (inert templates)"
```

---

### Task 2: Bundled template files + `bundled_persona_templates_dir()`

Ship the three inert templates as package-data and add the resolver.

**Files:**
- Create: `harness/templates/agents/default/SOUL.md`
- Create: `harness/templates/agents/default/IDENTITY.md`
- Create: `harness/templates/agents/default/USER.md`
- Modify: `harness/paths.py` (add `bundled_persona_templates_dir()` after `bundled_skills_dir`)
- Modify: `pyproject.toml` (add `templates/**/*` to package-data)
- Test: `tests/test_paths.py` (append)

**Interfaces:**
- Consumes: nothing new.
- Produces: `paths.bundled_persona_templates_dir() -> Path` = `<harness pkg>/templates/agents/default`.

- [ ] **Step 1: Create the three template files**

`harness/templates/agents/default/SOUL.md`:
```markdown
<!-- SOUL.md — the agent's persona: tone, boundaries, how it behaves.
     Replace this comment with a sentence or two. Anything you write here is
     read into the agent's context. Example:
     "You are concise and pragmatic. You explain only when asked." -->
```

`harness/templates/agents/default/IDENTITY.md`:
```markdown
<!-- IDENTITY.md — the agent's name, vibe, emoji.
     Replace this comment. Example: "Name: Ada. Dry wit. 🛠️" -->
```

`harness/templates/agents/default/USER.md`:
```markdown
<!-- USER.md — who you are and how you want to be addressed.
     Replace this comment. Example:
     "I'm Alberto; prefer terse, code-first answers." -->
```

- [ ] **Step 2: Write the failing test**

Append to `tests/test_paths.py`:
```python
def test_bundled_persona_templates_dir_has_trio():
    d = paths.bundled_persona_templates_dir()
    assert d.is_dir()
    for name in ("SOUL.md", "IDENTITY.md", "USER.md"):
        assert (d / name).is_file(), name
```

- [ ] **Step 3: Run to verify failure**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_paths.py -k bundled_persona -q`
Expected: FAIL — `AttributeError: ... has no attribute 'bundled_persona_templates_dir'`.

- [ ] **Step 4: Add `bundled_persona_templates_dir()` to `harness/paths.py`**

After `bundled_skills_dir()`:
```python
def bundled_persona_templates_dir() -> Path:
    """The persona templates shipped inside the package
    (harness/templates/agents/default/). Works in editable and installed wheels."""
    return Path(importlib.resources.files("harness")) / "templates" / "agents" / "default"
```

- [ ] **Step 5: Add package-data to `pyproject.toml`**

Change the `[tool.setuptools.package-data]` `"harness"` line (currently
`"harness" = ["skills/**/*"]`) to:
```toml
"harness" = ["skills/**/*", "templates/**/*"]
```

- [ ] **Step 6: Run to verify pass**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_paths.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add harness/templates harness/paths.py pyproject.toml tests/test_paths.py
git commit -m "feat(persona): bundle inert persona templates as package-data"
```

---

### Task 3: `seed_default_workspace()` — create-if-absent, never clobber

Copy the bundled templates into the user config dir on first run.

**Files:**
- Modify: `harness/persona.py` (add `seed_default_workspace`; needs `paths` import)
- Test: `tests/test_persona.py` (append)

**Interfaces:**
- Consumes: `paths.default_workspace_dir()`, `paths.bundled_persona_templates_dir()`, `PERSONA_FILES`.
- Produces: `seed_default_workspace() -> None` (idempotent; create-if-absent; never overwrites; never raises).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_persona.py`:
```python
from harness.persona import seed_default_workspace
from harness import paths


def test_seed_creates_trio_when_absent(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    seed_default_workspace()
    ws = paths.default_workspace_dir()
    for name in ("SOUL.md", "IDENTITY.md", "USER.md"):
        assert (ws / name).is_file(), name
    # seeded templates are inert -> compose injects nothing
    assert compose_persona(ws).block == ""


def test_seed_does_not_clobber_existing_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    ws = paths.default_workspace_dir()
    ws.mkdir(parents=True)
    (ws / "SOUL.md").write_text("You are terse.", encoding="utf-8")  # user content
    seed_default_workspace()                                          # must NOT overwrite
    assert (ws / "SOUL.md").read_text(encoding="utf-8") == "You are terse."
    # and it did not drop in the other templates either (dir already existed)
    assert not (ws / "IDENTITY.md").exists()


def test_seed_never_raises_on_oserror(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    def boom(*a, **k):
        raise OSError("read-only home")
    # Force the mkdir inside seed_default_workspace to fail. persona.Path IS
    # pathlib.Path; monkeypatch auto-restores after the test, so the global patch
    # is safe here. (This is intentional — do not "simplify" it away.)
    monkeypatch.setattr("harness.persona.Path.mkdir", boom)
    seed_default_workspace()   # must not raise — best-effort startup
```

- [ ] **Step 2: Run to verify failure**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_persona.py -k seed -q`
Expected: FAIL — `ImportError: cannot import name 'seed_default_workspace'`.

- [ ] **Step 3: Add to `harness/persona.py`**

Add the import near the top (after the existing `from harness import skills`):
```python
from harness import paths
```
Add the function (after `compose_context` or at the end of the module):
```python
def seed_default_workspace() -> None:
    """Copy the bundled inert templates into ~/.config/harness/agents/default/ on
    first run. No-op if the dir already exists (never clobber a real workspace).
    Never overwrites a file. Best-effort: never raises into the startup path."""
    dest = paths.default_workspace_dir()
    if dest.exists():
        return                                  # user has a workspace; do not clobber
    try:
        src = paths.bundled_persona_templates_dir()
        dest.mkdir(parents=True, exist_ok=True)
        for name in PERSONA_FILES:
            s, d = src / name, dest / name
            if s.is_file() and not d.exists():
                d.write_text(s.read_text(encoding="utf-8"), encoding="utf-8")
    except OSError:
        pass                                    # read-only home etc. — never break startup
```

- [ ] **Step 4: Run to verify pass**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_persona.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/persona.py tests/test_persona.py
git commit -m "feat(persona): seed_default_workspace copies templates create-if-absent"
```

---

### Task 4: Call seeding at both entrypoints

Wire `seed_default_workspace()` into `acp_main` and `run_traced` startup.

**Files:**
- Modify: `harness/acp_main.py` (`_main`, after `paths.load_env`, before agent construction)
- Modify: `harness/run_traced.py` (`main`, near startup)
- Test: `tests/test_acp_agent.py` (append — assert `_main` calls seeding)

**Interfaces:**
- Consumes: `persona.seed_default_workspace()`.
- Produces: nothing new.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_acp_agent.py`:
```python
def test_acp_main_seeds_default_workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("HARNESS_ROUTER_STUB", "1")
    import asyncio
    import acp
    from harness import persona

    called = {"n": 0}
    real = persona.seed_default_workspace
    def spy():
        called["n"] += 1
        real()
    monkeypatch.setattr(persona, "seed_default_workspace", spy)
    monkeypatch.setattr(acp, "run_agent", lambda agent: asyncio.sleep(0))

    from harness import acp_main
    asyncio.run(acp_main._main(["--model", "mock"]))
    assert called["n"] == 1
    # and it actually seeded
    assert (tmp_path / "harness" / "agents" / "default" / "SOUL.md").is_file()
```

- [ ] **Step 2: Run to verify failure**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_acp_agent.py -k seeds -q`
Expected: FAIL — `assert called["n"] == 1` fails (0; not wired yet).

- [ ] **Step 3: Wire `harness/acp_main.py`**

In `_main`, after `paths.load_env(cwd)` and before the agent is constructed, add:
```python
    from harness import persona
    persona.seed_default_workspace()   # first-run: drop editable templates in the config dir
```
(Place it after the `from harness import skills` import block so `persona` is imported in the same local scope; if `persona` is already importable at module scope in this file, a module-level import is fine too. Match the file's existing local-import style.)

- [ ] **Step 4: Wire `harness/run_traced.py`**

In `main`, near the other startup calls (after `load_dotenv(...)`, before the run loop), add:
```python
    from harness import persona as _persona_seed
    _persona_seed.seed_default_workspace()
```
(If `run_traced.py` already imports persona under a name like `_persona`, reuse that import instead of adding a second alias — match the existing imports.)

- [ ] **Step 5: Run to verify pass**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_acp_agent.py tests/test_run_traced.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add harness/acp_main.py harness/run_traced.py tests/test_acp_agent.py
git commit -m "feat(persona): seed default workspace at acp_main and run_traced startup"
```

---

### Task 5: The no-op regression (the safety guarantee)

Prove a freshly seeded (unedited) workspace is byte-identical to no workspace, including no `persona_load` event.

**Files:**
- Test: `tests/test_acp_session_context.py` (append — reuses its existing harness)

**Interfaces:**
- Consumes: `persona.seed_default_workspace`, `paths.default_workspace_dir`, the file's `_build`/`_ScriptedRouter`/`_chat`/`_prompt`/`_meta_keys_in_order` helpers.
- Produces: nothing.

- [ ] **Step 1: Write the test**

Append to `tests/test_acp_session_context.py`:
```python
def test_seeded_default_workspace_is_byte_identical_noop(monkeypatch, tmp_path):
    # seeding ships only inert templates -> chat path has no system message AND
    # no persona_load event fires. The Phase A no-op guarantee must survive seeding.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from harness import persona, paths
    persona.seed_default_workspace()

    captured = {}
    def fake_completion(**kwargs):
        captured.update(kwargs)
        return iter([])
    import litellm
    monkeypatch.setattr(litellm, "completion", fake_completion)

    agent = _build(_ScriptedRouter([_chat()]), worker_model_id="gpt-5.4")
    agent._workspace_dir = paths.default_workspace_dir()   # the SEEDED dir
    sid = asyncio.run(agent.new_session(cwd=".")).session_id
    _prompt(agent, sid, "hi")
    # no system message injected (templates are inert)
    assert captured["messages"] == [{"role": "user", "content": "hi"}]
    # and no persona_load event emitted
    assert "persona_load" not in _meta_keys_in_order(agent)
```

- [ ] **Step 2: Run to verify it passes**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_acp_session_context.py -k seeded -q`
Expected: PASS (the inertness rule from Task 1 makes the seeded templates inject nothing).

> If this test FAILS, the inertness rule (Task 1) is wrong — STOP and fix Task 1, do not weaken this test. This is the load-bearing guarantee.

- [ ] **Step 3: Commit**

```bash
git add tests/test_acp_session_context.py
git commit -m "test(persona): seeded default workspace is a byte-identical no-op"
```

---

### Task 6: Packaging test + docs

Assert the templates ship in the wheel, and document the install behavior.

**Files:**
- Modify: `tests/test_packaging.py` (extend the existing wheel test)
- Modify: `docs/personas.md` (one line)

**Interfaces:**
- Consumes: nothing new.
- Produces: nothing.

- [ ] **Step 1: Extend the packaging test**

In `tests/test_packaging.py`, inside `test_wheel_includes_tui_assets_and_skills`, add after the existing asserts:
```python
    assert any(n.endswith("harness/templates/agents/default/SOUL.md") for n in names), names
```

- [ ] **Step 2: Run it**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_packaging.py -q`
Expected: PASS (or SKIP if no wheel builder is available — that's the test's existing behavior, acceptable).

- [ ] **Step 3: Document in `docs/personas.md`**

In the "Quick start" area of `docs/personas.md` (after the `mkdir`/`echo` block), add:
```markdown
On a fresh install, `~/.config/harness/agents/default/` is seeded for you with
three template files (`SOUL.md`, `IDENTITY.md`, `USER.md`). They contain only a
commented hint, so they inject nothing until you replace the comment with real
text — the agent's behavior is unchanged until you edit one.
```

- [ ] **Step 4: Commit**

```bash
git add tests/test_packaging.py docs/personas.md
git commit -m "test(persona): wheel ships templates; docs note the seeded install"
```

---

### Task 7: Full-suite + scope gate

**Files:** none (verification only).

- [ ] **Step 1: Full suite**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/ -q`
Expected: PASS — all existing tests + the new ones, zero regressions. The Phase A
persona tests must be unchanged-and-green (the inertness rule is a superset of the
old blank rule).

- [ ] **Step 2: Scope gate**

Run: `git diff --stat main...HEAD`
Confirm only these changed: `harness/persona.py`, `harness/paths.py`,
`harness/acp_main.py`, `harness/run_traced.py`, `pyproject.toml`,
`harness/templates/agents/default/{SOUL,IDENTITY,USER}.md`, the matching `tests/`,
and `docs/personas.md` (+ the spec/plan docs).

Run: `grep -rnE "BOOTSTRAP|attestation|persona\.toml|--persona|/persona" harness/persona.py harness/paths.py`
Expected: no matches (no Phase D/C concepts leaked in).

- [ ] **Step 3: Commit (if any touch-ups)**

```bash
git add -A
git commit -m "chore(persona): default templates complete — suite green, scope held" --allow-empty
```

---

## Self-Review

**Spec coverage** (against `2026-06-26-persona-default-templates-design.md`):

- §2 inertness rule (`_meaningful`, HTML-comment-only skipped, `#` not a comment, all-or-nothing) → **Task 1** ✓
- §3 bundled package-data + `bundled_persona_templates_dir()` → **Task 2** ✓
- §3 `seed_default_workspace()` (create-if-absent, never overwrite, never raise) → **Task 3** ✓
- §3 called at both entrypoints → **Task 4** ✓
- §4 the three template files → **Task 2** ✓
- §5 inertness tests → **Task 1**; seeding/no-clobber tests → **Task 3**; the no-op regression → **Task 5**; packaging test → **Task 6** ✓
- §6 files touched incl. `docs/personas.md` line → **Task 6** ✓
- §7 scope gate (no BOOTSTRAP/attestation/selection) → **Task 7** ✓

No gaps.

**Placeholder scan:** every code step has complete code. The two "match the file's
existing import style" notes (Task 4) are intentional guidance about a real
choice (local vs module import), not a TODO — the exact code to add is given.

**Type consistency:** `_meaningful(raw)`, `seed_default_workspace()`,
`bundled_persona_templates_dir()`, `PERSONA_FILES`, `default_workspace_dir()`,
`compose_persona` are used identically across all tasks that reference them.
