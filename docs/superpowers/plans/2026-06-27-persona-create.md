# Persona Creation (TUI modal) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user create a new persona by name from a TUI modal (press `n` in the rail), seeding the inert template trio, then switch to it — reusing the C2c switch machinery.

**Architecture:** A new `create_persona(id)` engine function (generalizing `seed_default_workspace` via a shared byte-copy helper) + a `harness/create_persona` ACP ext-method that creates then chains into the extracted `_activate_seat` (C2c's switch body) + a `NewPersonaModal` (reuses the modal base + the `◐◓◑◒` spinner) opened by `n` in the rail.

**Tech Stack:** Python 3.11, Textual TUI, the ACP lib, pytest. Worktree: `.claude/worktrees/persona-create` (branch `persona-create`).

**Source spec:** `docs/superpowers/specs/2026-06-27-persona-create-design.md` (Codex-reviewed).

## Global Constraints

- **Reuse before invent** (`harness/tui/styles/components.md`): ONE new widget (`NewPersonaModal`, justified); reuse the modal base + the `ActivityStatus` `◐◓◑◒` cycle + the C2c switch path.
- **No second model home / no per-persona branch**: creation activates via the single-homed seat path; `default` is the one reserved id you canNOT create.
- **The no-op guarantee**: a no-persona launch is byte-identical; `seed_default_workspace` still no-ops if the default dir exists, never raises, and seeds byte-identical inert templates on first run.
- **Create is explicit-and-reported; seed is silent**: `create_persona` raises/reports failures; `seed_default_workspace` keeps swallowing `OSError` to protect startup. They share ONLY a private byte-copy helper — `seed` does NOT call the validating `create_persona`.
- **Charset gate**: persona ids match `^[a-z0-9_-]+$` (reuse `persona_select._VALID_ID`); `"default"` (`persona_select.RESERVED_KEY`) is reserved and rejected by an EXPLICIT check (the charset gate alone allows "default").
- **Work in this worktree**; run pytest with the worktree as cwd (editable-install shadowing). Test command: `.venv/bin/python -m pytest tests/ -q` from the worktree root.

---

## File Structure

- **Modify** `harness/persona.py` — add `PersonaExists`, `_copy_persona_templates(dest)` (shared byte-copy), `create_persona(id)`; refactor `seed_default_workspace` to use the shared helper.
- **Modify** `harness/acp_agent.py` — extract `_activate_seat(pid) -> dict` from the `set_persona` body; add the `harness/create_persona` ext-method (create → activate).
- **Create** `harness/tui/widgets/new_persona_modal.py` — `NewPersonaModal(ModalScreen)`.
- **Modify** `harness/tui/widgets/agent_rail.py` — add an `n` binding + a `NewPersonaRequested` message.
- **Modify** `harness/tui/app.py` — extract `_apply_persona_switch(resp)` from `on_persona_selected`; handle `NewPersonaRequested` (open the modal) + the modal result (create + switch).
- **Modify** `harness/tui/styles/components.md` — add the `NewPersonaModal` catalog entry.
- **Tests**: `tests/test_persona.py` (engine), `tests/test_acp_agent.py` (ext-method), `tests/test_tui_pilot.py` (modal + rail wiring).

---

### Task 1: Engine — `_copy_persona_templates` + `create_persona` + `PersonaExists`, refactor seed

**Files:**
- Modify: `harness/persona.py` (add `PersonaExists`, `_copy_persona_templates`, `create_persona`; refactor `seed_default_workspace` at ~L77-92)
- Test: `tests/test_persona.py`

**Interfaces:**
- Consumes: `harness.paths` (`config_dir`, `default_workspace_dir`, `bundled_persona_templates_dir`), `harness.persona_select` (`_VALID_ID`, `RESERVED_KEY`), `PERSONA_FILES` (already in persona.py).
- Produces:
  - `class PersonaExists(Exception)` — `str(e)` is the offending id.
  - `_copy_persona_templates(dest: Path) -> None` — mkdir + byte-copy the trio, skip existing files.
  - `create_persona(persona_id: str) -> Path` — validate/no-clobber/copy; raises `persona_select.InvalidPersonaId` or `PersonaExists`; returns the workspace path.

- [ ] **Step 1: Write the failing tests for `create_persona`**

```python
# tests/test_persona.py — add. Reuse the existing isolated-config pattern in this file
# (it points XDG_CONFIG_HOME / config_dir at a tmp dir). Grep this file for how other
# tests set config_dir to a tmp path and follow it; below assumes `agents_root` resolves
# under a tmp config dir via that fixture.
import pytest
from harness import persona, paths
from harness.persona_select import InvalidPersonaId


def test_create_persona_makes_dir_and_copies_trio(isolated_config):
    ws = persona.create_persona("fred")
    assert ws == paths.config_dir() / "agents" / "fred"
    assert ws.is_dir()
    for name in persona.PERSONA_FILES:
        assert (ws / name).is_file()


def test_create_persona_copies_bytes_identical(isolated_config):
    ws = persona.create_persona("fred")
    src = paths.bundled_persona_templates_dir()
    for name in persona.PERSONA_FILES:
        assert (ws / name).read_bytes() == (src / name).read_bytes()


def test_create_persona_rejects_default(isolated_config):
    with pytest.raises(InvalidPersonaId):
        persona.create_persona("default")


@pytest.mark.parametrize("bad", ["fred.smith", "Fred", "has space", "a/b"])
def test_create_persona_rejects_bad_charset(isolated_config, bad):
    with pytest.raises(InvalidPersonaId):
        persona.create_persona(bad)


def test_create_persona_rejects_existing_dir(isolated_config):
    persona.create_persona("fred")
    with pytest.raises(persona.PersonaExists):
        persona.create_persona("fred")


def test_create_persona_rejects_existing_file_collision(isolated_config):
    target = paths.config_dir() / "agents" / "fred"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("i am a file, not a dir")
    with pytest.raises(persona.PersonaExists):
        persona.create_persona("fred")
```

If `isolated_config` is not already a fixture in `tests/test_persona.py`, copy the one from `tests/test_acp_agent.py` (`monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path)); return tmp_path`) — confirm `paths.config_dir()` honors `XDG_CONFIG_HOME` (grep `paths.config_dir`).

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_persona.py -q -k create_persona`
Expected: FAIL — `AttributeError: module 'harness.persona' has no attribute 'create_persona'` / `PersonaExists`.

- [ ] **Step 3: Add `PersonaExists`, `_copy_persona_templates`, `create_persona`**

In `harness/persona.py` (near the top exception area / after `PERSONA_FILES`):

```python
from harness import persona_select   # _VALID_ID, RESERVED_KEY, InvalidPersonaId


class PersonaExists(Exception):
    """Raised by create_persona when the target workspace already exists.
    str(e) is the offending id. The opposite failure of UnknownPersona."""


def _copy_persona_templates(dest: Path) -> None:
    """Copy the bundled inert template trio into `dest`, byte-for-byte, creating
    the dir and skipping any file that already exists. The ONLY shared seeding
    logic — callers own validation and raise-policy (seed swallows, create reports)."""
    src = paths.bundled_persona_templates_dir()
    dest.mkdir(parents=True, exist_ok=True)
    for name in PERSONA_FILES:
        s, d = src / name, dest / name
        if s.is_file() and not d.exists():
            d.write_bytes(s.read_bytes())


def create_persona(persona_id: str) -> Path:
    """Create a NEW persona workspace under config_dir()/agents/<id> with the inert
    template trio, and return its path. Validation: charset (^[a-z0-9_-]+$) AND the
    reserved id "default" is rejected (the charset gate alone would allow it). No
    clobber: if the target path already exists (file or dir) -> PersonaExists.
    Explicit creation REPORTS failure (OSError propagates) — unlike seed_default_workspace."""
    if persona_id == persona_select.RESERVED_KEY or not persona_select._VALID_ID.match(persona_id):
        raise persona_select.InvalidPersonaId(persona_id)
    target = paths.config_dir() / "agents" / persona_id
    if target.exists():
        raise PersonaExists(persona_id)
    _copy_persona_templates(target)
    return target
```

(If `harness/persona.py` already imports `persona_select` or has a top `from pathlib import Path`, don't duplicate — grep first.)

- [ ] **Step 4: Run to verify the create tests pass**

Run: `.venv/bin/python -m pytest tests/test_persona.py -q -k create_persona`
Expected: PASS.

- [ ] **Step 5: Refactor `seed_default_workspace` to use the shared helper (failing-test-first)**

Add a regression test that locks the seed contract:

```python
# tests/test_persona.py — add
def test_seed_default_noop_when_exists_does_not_backfill(isolated_config):
    dest = paths.default_workspace_dir()
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "SOUL.md").write_text("user content")   # only one file present
    persona.seed_default_workspace()
    # existing dir => no-op: missing trio files are NOT backfilled
    assert (dest / "SOUL.md").read_text() == "user content"
    assert not (dest / "IDENTITY.md").exists()


def test_seed_default_seeds_byte_identical_on_first_run(isolated_config):
    persona.seed_default_workspace()
    dest = paths.default_workspace_dir()
    src = paths.bundled_persona_templates_dir()
    for name in persona.PERSONA_FILES:
        assert (dest / name).read_bytes() == (src / name).read_bytes()


def test_seed_default_never_raises_on_oserror(isolated_config, monkeypatch):
    monkeypatch.setattr(persona, "_copy_persona_templates",
                        lambda dest: (_ for _ in ()).throw(OSError("read-only")))
    # must NOT raise into the startup path
    persona.seed_default_workspace()
```

Run: `.venv/bin/python -m pytest tests/test_persona.py -q -k seed_default`
Expected: the byte-identical + never-raise tests may pass already; `noop_when_exists` must pass against the refactor. Run AFTER step 6 if red before.

- [ ] **Step 6: Refactor `seed_default_workspace`**

Replace its body (`harness/persona.py:77-92`) with:

```python
def seed_default_workspace() -> None:
    """Copy the bundled inert templates into ~/.config/harness/agents/default/ on
    first run. No-op if the dir already exists (never clobber / never backfill).
    Best-effort: never raises into the startup path."""
    dest = paths.default_workspace_dir()
    if dest.exists():
        return                                  # user has a workspace; do not clobber/backfill
    try:
        _copy_persona_templates(dest)
    except OSError:
        pass                                    # read-only home etc. — never break startup
```

- [ ] **Step 7: Run the whole persona suite (catch seed regressions)**

Run: `.venv/bin/python -m pytest tests/test_persona.py -q`
Expected: PASS (existing seed tests + the new create/seed tests). If a pre-existing seed test asserted the old `read_text` behavior, confirm byte-content is unchanged (templates are text; bytes == text here).

- [ ] **Step 8: Commit**

```bash
git add harness/persona.py tests/test_persona.py
git commit -m "feat(persona): create_persona + shared byte-copy seeder (persona-create task 1)"
```

---

### Task 2: ACP ext-method — extract `_activate_seat` + add `harness/create_persona`

**Files:**
- Modify: `harness/acp_agent.py` — extract `_activate_seat` from the `set_persona` branch (~L128-148); add the `harness/create_persona` branch.
- Test: `tests/test_acp_agent.py`

**Interfaces:**
- Consumes: `harness.persona.create_persona` + `PersonaExists` (Task 1); `harness.persona_select` (`UnknownPersona`, `InvalidPersonaId`); the existing `PersonaSessions`/`resolve_session_model` (C2c).
- Produces:
  - `_activate_seat(self, pid: str) -> dict` — `{ok, id, session_id, model}` (the current set_persona success path); may raise `UnknownPersona`/`InvalidPersonaId`.
  - `ext_method("harness/create_persona", {id})` → `{ok, id, session_id, model}` on success; `{ok:false, error}` on any failure, `_active_persona` unchanged.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_acp_agent.py — add. Reuse _make_agent + isolated_config + asyncio.run.
import asyncio
import pytest
from harness import persona


def test_create_persona_creates_and_activates(agent_default, isolated_config):
    # agent_default: a HarnessAgent at _active_persona="default"; set _cwd so seats mint.
    agent_default._cwd = "/x"
    resp = asyncio.run(agent_default.ext_method("harness/create_persona", {"id": "fred"}))
    assert resp["ok"] is True and resp["id"] == "fred"
    assert resp["session_id"]
    assert agent_default._active_persona == "fred"
    # the workspace now exists on disk
    from harness import paths
    assert (paths.config_dir() / "agents" / "fred").is_dir()


def test_create_persona_duplicate_keeps_active(agent_default, isolated_config):
    agent_default._cwd = "/x"
    asyncio.run(agent_default.ext_method("harness/create_persona", {"id": "fred"}))
    before = agent_default._active_persona
    resp = asyncio.run(agent_default.ext_method("harness/create_persona", {"id": "fred"}))
    assert resp["ok"] is False
    assert agent_default._active_persona == before   # "fred" (it was activated by the 1st create)


def test_create_persona_invalid_keeps_active(agent_default, isolated_config):
    before = agent_default._active_persona            # "default"
    resp = asyncio.run(agent_default.ext_method("harness/create_persona", {"id": "default"}))
    assert resp["ok"] is False
    assert agent_default._active_persona == before


def test_create_persona_missing_id(agent_default, isolated_config):
    resp = asyncio.run(agent_default.ext_method("harness/create_persona", {}))
    assert resp["ok"] is False
```

Build `agent_default` from the existing `_make_agent()` helper (workspace_dir=None → `_active_persona="default"`). If there is no `agent_default` fixture, instantiate inline: `agent = _make_agent(backend="mock"); agent._cwd = "/x"`.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_acp_agent.py -q -k create_persona`
Expected: FAIL — unknown ext-method returns `{}`, so `resp["ok"]` KeyErrors / is falsy wrongly.

- [ ] **Step 3: Extract `_activate_seat`**

Replace the `set_persona` branch body (`harness/acp_agent.py:128-148`). First add the method (place it near `ext_method`, e.g. after it):

```python
    def _activate_seat(self, pid: str) -> dict:
        """Get-or-create the seat for persona `pid`, make it active, mirror its model
        into the read-site fallback + the session state, and return the switch result.
        Raises persona_select.UnknownPersona / InvalidPersonaId. The ONE activation path
        shared by set_persona and create_persona."""
        from harness import persona_select
        from harness.persona_sessions import resolve_session_model
        resolve_session_model_for = lambda p: resolve_session_model(
            p, shell_set_model=self._shell_set_model,
            shell_env=self._shell_env, dotenv=self._shell_env, backend=self._backend)
        seat = self._persona_sessions.get_or_create(
            pid, cwd=self._cwd, store=self._store,
            resolve_ws=persona_select.resolve_workspace,
            resolve_model=resolve_session_model_for)
        self._active_persona = pid
        self._worker_model_id = seat.model
        self._store.get(seat.session_id).worker_model = seat.model
        return {"ok": True, "id": pid, "session_id": seat.session_id, "model": seat.model}
```

Then make the `set_persona` branch a thin wrapper:

```python
        if method == "harness/set_persona":
            pid = (params or {}).get("id")
            if not isinstance(pid, str) or not pid:
                return {"ok": False, "error": "missing id"}
            from harness import persona_select
            try:
                return self._activate_seat(pid)
            except (persona_select.UnknownPersona, persona_select.InvalidPersonaId) as e:
                return {"ok": False, "error": str(e)}
```

- [ ] **Step 4: Add the `harness/create_persona` branch**

Right after the `set_persona` branch:

```python
        if method == "harness/create_persona":
            pid = (params or {}).get("id")
            if not isinstance(pid, str) or not pid:
                return {"ok": False, "error": "missing id"}
            from harness import persona, persona_select
            try:
                persona.create_persona(pid)              # raises Invalid/PersonaExists/OSError
                return self._activate_seat(pid)          # raises Unknown/Invalid
            except (persona_select.InvalidPersonaId, persona.PersonaExists,
                    persona_select.UnknownPersona, OSError) as e:
                return {"ok": False, "error": str(e)}
```

(`_active_persona` is only set inside `_activate_seat`, which runs after a successful create — so any create/activation failure leaves it unchanged.)

- [ ] **Step 5: Run to verify pass + full set_persona regression**

Run: `.venv/bin/python -m pytest tests/test_acp_agent.py -q -k "create_persona or set_persona"`
Expected: PASS — the extracted `_activate_seat` must keep all existing `set_persona` tests green.

- [ ] **Step 6: Commit**

```bash
git add harness/acp_agent.py tests/test_acp_agent.py
git commit -m "feat(persona): harness/create_persona ext-method + _activate_seat extraction (persona-create task 2)"
```

---

### Task 3: `NewPersonaModal` widget

**Files:**
- Create: `harness/tui/widgets/new_persona_modal.py`
- Test: `tests/test_tui_pilot.py` (modal-only pilot)

**Interfaces:**
- Consumes: Textual `ModalScreen`, `Input`, `Static`; the `◐◓◑◒` cycle (copy the 4-frame list + reduced-motion fallback from `harness/tui/widgets/activity_status.py:14` `_CYCLE`).
- Produces: `class NewPersonaModal(ModalScreen)` — `dismiss(id: str | None)`; `set_error(msg)`; `set_creating()`.

- [ ] **Step 1: Write the failing modal test**

```python
# tests/test_tui_pilot.py — add. Use the existing pilot/app harness in this file.
import pytest
from harness.tui.widgets.new_persona_modal import NewPersonaModal


@pytest.mark.asyncio
async def test_new_persona_modal_enter_dismisses_with_name():
    from textual.app import App
    class _Host(App):
        result = "UNSET"
        def on_mount(self):
            self.push_screen(NewPersonaModal(), lambda r: setattr(self, "result", r))
    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        inp = app.screen.query_one("#new-persona-name")
        inp.value = "fred"
        await pilot.press("enter")
        await pilot.pause()
    assert app.result == "fred"


@pytest.mark.asyncio
async def test_new_persona_modal_empty_name_ignored():
    from textual.app import App
    class _Host(App):
        result = "UNSET"
        def on_mount(self):
            self.push_screen(NewPersonaModal(), lambda r: setattr(self, "result", r))
    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")     # empty -> ignored, modal stays
        await pilot.pause()
        assert app.result == "UNSET"   # not dismissed
        await pilot.press("escape")
        await pilot.pause()
    assert app.result is None
```

(Match the async-pilot style already in `tests/test_tui_pilot.py` — if it uses a shared `HarnessTui` harness, prefer that; the standalone `_Host` above is a fallback that exercises the modal in isolation.)

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_tui_pilot.py -q -k new_persona_modal`
Expected: FAIL — `ModuleNotFoundError: harness.tui.widgets.new_persona_modal`.

- [ ] **Step 3: Implement the modal**

```python
# harness/tui/widgets/new_persona_modal.py
"""NewPersonaModal — name a new persona, create it, switch to it.

Lifecycle: input (type a name) → creating (spinner) → dismiss(id) on success, or
error (inline message, back to input). Enter on an empty name is ignored; esc
cancels (dismiss None). The app owns the actual create call (via the ext-method);
this widget only collects the name, shows progress/errors, and dismisses with the
id. Mirrors SelectModal's ModalScreen/dismiss pattern. Tokens only."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static

_SPINNER = ["◐", "◓", "◑", "◒"]            # mirrors ActivityStatus._CYCLE


class NewPersonaModal(ModalScreen):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, reduced_motion: bool = False) -> None:
        super().__init__()
        self._reduced_motion = reduced_motion
        self._i = 0
        self._timer = None

    def compose(self) -> ComposeResult:
        with Vertical(id="new-persona-box"):
            yield Static("[b]New persona[/b]   [$muted]esc[/]",
                         id="new-persona-title", markup=True)
            yield Input(placeholder="name (a-z 0-9 - _)", id="new-persona-name")
            yield Static("", id="new-persona-status", markup=True)

    def on_mount(self) -> None:
        self.query_one("#new-persona-name", Input).focus()

    @on(Input.Submitted, "#new-persona-name")
    def _submit(self) -> None:
        name = self.query_one("#new-persona-name", Input).value.strip()
        if not name:
            return                                  # empty -> ignore, stay open
        self.dismiss(name)

    def set_creating(self) -> None:
        """Switch to the creating state: disable input, start the spinner."""
        self.query_one("#new-persona-name", Input).disabled = True
        self._tick()
        if not self._reduced_motion:
            self._timer = self.set_interval(0.15, self._tick)

    def _tick(self) -> None:
        glyph = "◐" if self._reduced_motion else _SPINNER[self._i % len(_SPINNER)]
        self._i += 1
        self.query_one("#new-persona-status", Static).update(
            f"[$accent]{glyph}[/] creating…")

    def set_error(self, msg: str) -> None:
        """Show an error, re-enable input for a retry."""
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        inp = self.query_one("#new-persona-name", Input)
        inp.disabled = False
        inp.focus()
        self.query_one("#new-persona-status", Static).update(f"[$error]{msg}[/]")

    def action_cancel(self) -> None:
        self.dismiss(None)
```

NOTE on flow: the modal `dismiss(name)`s on Enter; the APP's result-callback runs the create ext-method and, on error, the modal is already dismissed. To show the spinner + inline errors WITHOUT dismissing first, the app instead drives create while the modal is still open (see Task 4 — the app calls `modal.set_creating()` before the ext-method and `modal.set_error()` / `modal.dismiss(id)` after). The `_submit` above dismisses immediately for the simplest path; Task 4 decides whether to keep the modal open during create. **Implementer: follow Task 4's wiring — it is the source of truth for the open-during-create flow; this widget exposes `set_creating`/`set_error` for it.**

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_tui_pilot.py -q -k new_persona_modal`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/tui/widgets/new_persona_modal.py tests/test_tui_pilot.py
git commit -m "feat(persona): NewPersonaModal widget (persona-create task 3)"
```

---

### Task 4: Rail `n` key + app wiring (open modal → create → switch)

**Files:**
- Modify: `harness/tui/widgets/agent_rail.py` — add `n` binding + `NewPersonaRequested` message.
- Modify: `harness/tui/app.py` — extract `_apply_persona_switch(resp)`; handle `NewPersonaRequested`; the modal result-callback.
- Test: `tests/test_tui_pilot.py`

**Interfaces:**
- Consumes: `NewPersonaModal` (Task 3); `ext_method("harness/create_persona")` (Task 2); the existing `on_persona_selected` success block (`app.py:967-981`).
- Produces:
  - `agent_rail.NewPersonaRequested(Message)`.
  - `app._apply_persona_switch(resp: dict) -> None` — the extracted success block (repoint session, apply PersonaResolved, refresh chip + footer, close rail, refocus).

- [ ] **Step 1: Write the failing rail-key + create-flow tests**

```python
# tests/test_tui_pilot.py — add. Use the project's HarnessTui pilot harness + a fake _conn
# whose ext_method records calls and returns a scripted create response (mirror the C2c
# on_persona_selected pilot tests already in this file).

async def test_rail_n_opens_new_persona_modal(harness_app):
    app, conn = harness_app
    app.action_toggle_rail()                       # open the rail
    await _pause(app)
    rail = app.query_one("#agent-rail")
    rail.focus()
    await _press(app, "n")
    await _pause(app)
    from harness.tui.widgets.new_persona_modal import NewPersonaModal
    assert isinstance(app.screen, NewPersonaModal)


async def test_create_flow_switches_to_new_persona(harness_app):
    app, conn = harness_app
    conn.create_persona_response = {"ok": True, "id": "fred",
                                    "session_id": "sess-fred", "model": "m-fred"}
    app._turn_active = False
    # drive the result-callback directly with the created name
    await app._create_persona("fred")              # the app helper Task 4 adds
    assert ("harness/create_persona", {"id": "fred"}) in conn.ext_calls
    assert app._session_id == "sess-fred"
    assert app._snapshot.active_id == "fred"


async def test_create_flow_error_shows_notice(harness_app):
    app, conn = harness_app
    conn.create_persona_response = {"ok": False, "error": "persona 'fred' already exists"}
    await app._create_persona("fred")
    assert ("harness/create_persona", {"id": "fred"}) in conn.ext_calls
    assert app._session_id != "sess-fred"          # unchanged
```

Match the exact fixture names + helpers (`_pause`/`_press`/`harness_app`/the fake-conn) used by the existing C2c `on_persona_selected` pilot tests in this file — read 2-3 of them first and mirror them. If those tests drive `on_persona_selected` directly rather than through key events, drive `_create_persona`/`on_new_persona_requested` the same way.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_tui_pilot.py -q -k "rail_n or create_flow"`
Expected: FAIL — no `n` binding / no `_create_persona` helper.

- [ ] **Step 3: Add the rail `n` binding + message**

In `harness/tui/widgets/agent_rail.py` (the `AgentRail` class):

```python
from textual.binding import Binding


class NewPersonaRequested(Message):
    """Posted when the user presses `n` in the rail to create a new persona."""


class AgentRail(ListView):
    BINDINGS = [Binding("n", "new_persona", "New persona")]

    # ... existing __init__/set_rows/etc ...

    def action_new_persona(self) -> None:
        self.post_message(NewPersonaRequested())
```

(`Message` is already imported in this file — it defines `PersonaSelected(Message)`. Add `Binding` import.)

- [ ] **Step 4: Extract `_apply_persona_switch` + add the create flow in `app.py`**

Extract the success block from `on_persona_selected` (`app.py:967-981`) into a method, and call it from both:

```python
    def _apply_persona_switch(self, resp: dict) -> None:
        """Apply a successful set_persona/create_persona result: repoint the session,
        update the indicator + footer, close the rail, refocus. Shared by switch + create."""
        self._session_id = resp["session_id"]
        self._persona_seen = True
        self._apply(PersonaResolved(resp["id"]))
        self._refresh_persona()
        model = resp.get("model")
        if model:
            self._worker_model_id = model
            self._refresh_meta_line()
        try:
            self.query_one("#agent-rail", AgentRail).display = False
        except Exception:
            pass
        self._active_input().focus()
```

Then `on_persona_selected`'s success tail becomes `self._apply_persona_switch(resp)` (replace lines 967-981).

Add the rail-`n` handler + the create flow:

```python
    def on_new_persona_requested(self, event) -> None:
        event.stop()
        from harness.tui.widgets.new_persona_modal import NewPersonaModal
        def _created(name):
            if name:
                self.run_worker(self._create_persona(name), thread=False)
        self.push_screen(NewPersonaModal(), _created)

    async def _create_persona(self, name: str) -> None:
        if self._conn is None:
            return
        try:
            resp = await self._conn.ext_method("harness/create_persona", {"id": name})
        except Exception as e:
            self._notify_line(f"could not create persona: {e}")
            return
        if not resp.get("ok"):
            self._notify_line(f"persona: {resp.get('error', 'create failed')}")
            return
        self._apply_persona_switch(resp)
```

(Import `NewPersonaRequested` is not needed — Textual dispatches `on_new_persona_requested` by the message class name. Confirm the message-name→handler mapping by how `on_persona_selected` is dispatched from `PersonaSelected`; mirror it. If the app needs the import for isinstance, add `from harness.tui.widgets.agent_rail import NewPersonaRequested`.)

NOTE on the spinner-during-create: the simplest correct flow (modal dismisses on Enter, then the app runs create) is what the tests above assert. If you want the spinner visible DURING create (modal stays open), instead push the modal, on its submit call `modal.set_creating()` then `await self._conn.ext_method(...)`, then `modal.dismiss(id)` or `modal.set_error(msg)`. Pick the open-during-create variant ONLY if the existing pilot harness supports awaiting inside the modal; otherwise the dismiss-then-create flow is acceptable for v1 and still shows a brief spinner if `set_creating` is called before dismiss. Keep it simple; the tests define the contract.

- [ ] **Step 5: Run to verify pass + full TUI suite**

Run: `.venv/bin/python -m pytest tests/test_tui_pilot.py -q`
Expected: PASS (new + existing, incl. the C2c switch tests that now go through `_apply_persona_switch`). Known pre-existing flake `test_pilot_streams_deltas_into_one_markdown_widget` — if ONLY that fails, re-run alone.

- [ ] **Step 6: Commit**

```bash
git add harness/tui/widgets/agent_rail.py harness/tui/app.py tests/test_tui_pilot.py
git commit -m "feat(persona): rail 'n' opens create modal + create-then-switch wiring (persona-create task 4)"
```

---

### Task 5: Catalog entry + full-suite green

**Files:**
- Modify: `harness/tui/styles/components.md` (add `NewPersonaModal` under group D/F).

- [ ] **Step 1: Add the catalog entry**

In `harness/tui/styles/components.md`, under the modals group (near `SelectModal`/`PermissionModal`), add:

```markdown
### `NewPersonaModal`   `✅ shipped` (persona-create)
Name-a-new-persona overlay: an `Input` + a status line. Lifecycle: input → creating
(the `◐◓◑◒` spinner, reduced-motion static `◐`) → dismiss(id) on success / inline
`$error` on failure. Opened by `n` in the `AgentRail`; on success the app creates the
workspace (inert template trio) and switches to it (the C2c `_apply_persona_switch`
path). Sibling of `SelectModal`/`PermissionModal`; the ONE create-input modal (no
existing modal takes a free-text create input with a create-then-switch lifecycle).
- **In:** none (collects a name); **Out:** the created id via `dismiss`.
- **When to use:** to create a persona. For PICKING an existing one use the rail; for a
  generic list pick use `SelectModal`.
```

Also update the at-a-glance table to add a `NewPersonaModal | ✅ shipped` row.

- [ ] **Step 2: Run the full suite from the worktree root**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS. Confirm the import resolves into the worktree if anything surprises: `python -c "import harness.persona as m; print(m.__file__)"` → must point into `.claude/worktrees/persona-create`.

- [ ] **Step 3: Confirm primary checkout clean**

Run: `git -C /Users/alberto/Work/quiubo/harness status --short`
Expected: empty.

- [ ] **Step 4: Commit**

```bash
git add harness/tui/styles/components.md
git commit -m "docs(components): NewPersonaModal shipped (persona-create task 5)"
```

---

## Self-Review

**1. Spec coverage:**
- §3 `create_persona` + shared `_copy_persona_templates` + `PersonaExists` → Task 1. ✓
- §3 seed refactor (no-op-if-exists, never-raise, byte-identical) → Task 1 (steps 5-7). ✓
- §3 `_activate_seat` extraction + `harness/create_persona` ext-method (create→activate, active-unchanged-on-failure) → Task 2. ✓
- §4/§7 `NewPersonaModal` (spinner reuse, tokens) → Task 3. ✓
- §2/§6 rail `n` entry → Task 4. ✓
- §2 create+switch (reuse the switch path) → Task 4 (`_apply_persona_switch`). ✓
- §5 error handling (invalid/dup/empty/FS, active unchanged) → Tasks 1/2/4 tests. ✓
- §7 catalog entry → Task 5. ✓
- §8 no-op + full suite → Tasks 1 (seed) + 5. ✓

**2. Placeholder scan:** No TBD/handle-errors. Two steps say "mirror the existing pilot fixture names" (Tasks 3-4) — deliberate: the repo's pilot harness names are the truth; fabricating them is worse. Each names the exact tests to read.

**3. Type consistency:** `create_persona(id)->Path`, `PersonaExists`, `_copy_persona_templates(dest)`, `_activate_seat(pid)->dict`, `harness/create_persona → {ok,id,session_id,model}`, `_apply_persona_switch(resp)`, `NewPersonaRequested`, `NewPersonaModal.dismiss(id|None)`/`set_creating`/`set_error` — consistent across Tasks 1-5.

**Crux tasks for Codex review during build (per spec §9):** Task 1 (seed/create split — the byte-copy + no-backfill contract) and Task 2 (create-then-activate boundary — `_active_persona` unchanged on failure).
