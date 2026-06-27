# Persona C2b — Rail + Switcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A toggleable right-side AgentRail that lists all persona workspaces (active highlighted), where selecting one switches to it by re-execing the agent with the new `--persona`.

**Architecture:** A pure `roster.persona_rows()` composes `[(id, name, active)]` from `list_personas()` + `read_name()` + C2a's `FleetSnapshot.active_id`; a dumb `AgentRail` widget renders rows and emits `PersonaSelected`; the app records the choice and triggers a re-exec reusing the `/reload` machinery (`tui_main` threads the chosen `--persona` into `_relaunch_command`).

**Tech Stack:** Python 3.11+, Textual, stdlib `tomllib`, pytest (+ pilot tests). No new dependencies.

## Global Constraints

- **No engine change.** C2b touches only the TUI + `persona_config`. The engine (`acp_agent`/`acp_main`), `new_session`, and concurrency are untouched. Switching is re-exec only (one process = one persona — C1).
- **Reuse C2a's seam:** the active persona comes from `FleetSnapshot.active_id` (already populated by C2a). The rail HIGHLIGHTS from it; it does not re-derive the active persona.
- **Roster invariant:** `persona_rows` ALWAYS includes the active id as a row, even if absent from `list_personas()` (mirrors C2a's "active is never None").
- **Toggle key is `tab`** — the landing hint already advertises "tab agents" (app.py:135). `tab` is Textual's default focus key, so the binding MUST intercept it at the app level with `event.stop()`.
- **Switch-to-same is a no-op:** selecting the already-active persona does NOT re-exec.
- **Unknown id errors, never silently switches:** `/persona <id>` validates against `list_personas()` membership; an unknown id shows a `_notify_line` error and does NOT re-exec.
- **Tolerant config:** `read_name` returns `None` on missing/corrupt/no-key/non-str (same contract as `read_skills`).
- **Test command (worktree as cwd):** `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/ -q`. Run with the WORKTREE as cwd (editable-install shadowing — verify with `import harness.X; print(X.__file__)` if surprised). Full suite must stay green.
- **Commit trailer:** end every commit with `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

---

### Task 1: `roster.persona_rows` — the pure roster model

**Files:**
- Create: `harness/tui/roster.py`
- Test: `tests/test_tui_roster.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class PersonaRow: id: str; name: str; active: bool`
  - `persona_rows(personas: list[str], active_id: str, name_of: Callable[[str], str | None]) -> tuple[PersonaRow, ...]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_tui_roster.py`:

```python
from harness.tui.roster import persona_rows, PersonaRow


def _names(d):
    return lambda pid: d.get(pid)


def test_rows_compose_with_names_and_active_flag():
    rows = persona_rows(["default", "fred"], "fred", _names({"fred": "Fred R."}))
    assert rows == (
        PersonaRow(id="default", name="default", active=False),
        PersonaRow(id="fred", name="Fred R.", active=True),
    )

def test_name_falls_back_to_id_when_name_of_returns_none():
    rows = persona_rows(["fred"], "fred", _names({}))
    assert rows == (PersonaRow(id="fred", name="fred", active=True),)

def test_active_id_always_appears_even_if_absent_from_personas():
    # invariant: the active persona must always be a row, appended if missing
    rows = persona_rows(["default"], "ghost", _names({}))
    assert PersonaRow(id="ghost", name="ghost", active=True) in rows
    assert rows[-1].id == "ghost"          # appended last
    assert [r.id for r in rows] == ["default", "ghost"]

def test_no_duplicate_when_active_in_personas():
    rows = persona_rows(["default", "fred"], "default", _names({}))
    assert [r.id for r in rows] == ["default", "fred"]   # no dup
    assert sum(r.active for r in rows) == 1              # exactly one active

def test_order_preserved():
    rows = persona_rows(["b", "a", "c"], "a", _names({}))
    assert [r.id for r in rows] == ["b", "a", "c"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tui_roster.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'harness.tui.roster'`.

- [ ] **Step 3: Write minimal implementation**

Create `harness/tui/roster.py`:

```python
"""The persona rail's pure roster model — no Textual, no I/O.

Composes the AgentRail's display rows from the existing-persona list, the active
id (from C2a's FleetSnapshot.active_id), and a name lookup. Pure so it is
exhaustively unit-testable, like render.py / state.py."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class PersonaRow:
    id: str
    name: str
    active: bool


def persona_rows(
    personas: list[str],
    active_id: str,
    name_of: Callable[[str], str | None],
) -> tuple[PersonaRow, ...]:
    """One row per persona, in the given order; name falls back to the id.
    INVARIANT: the active id is always present — appended last if not in
    `personas` — so the rail can always highlight the running persona (mirrors
    C2a's 'active is never None')."""
    rows = [
        PersonaRow(id=pid, name=(name_of(pid) or pid), active=(pid == active_id))
        for pid in personas
    ]
    if not any(r.id == active_id for r in rows):
        rows.append(PersonaRow(id=active_id, name=active_id, active=True))
    return tuple(rows)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tui_roster.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add harness/tui/roster.py tests/test_tui_roster.py
git commit -m "feat(tui): roster.persona_rows — pure rail model (active always present)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `persona_config.read_name`

**Files:**
- Modify: `harness/persona_config.py`
- Test: `tests/test_persona_config.py`

**Interfaces:**
- Consumes: existing `persona_config` module (`PERSONA_TOML`, the `tomllib` read pattern in `read_skills`).
- Produces: `read_name(workspace_dir: Path | None) -> str | None`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_persona_config.py`:

```python
def test_read_name_returns_name(tmp_path):
    (tmp_path / "persona.toml").write_text('name = "Fred R."\n')
    assert persona_config.read_name(tmp_path) == "Fred R."

def test_read_name_none_when_missing_workspace():
    assert persona_config.read_name(None) is None

def test_read_name_none_when_no_file(tmp_path):
    assert persona_config.read_name(tmp_path / "nope") is None

def test_read_name_none_when_no_key(tmp_path):
    (tmp_path / "persona.toml").write_text('skills = ["/a"]\n')
    assert persona_config.read_name(tmp_path) is None

def test_read_name_none_when_corrupt(tmp_path):
    (tmp_path / "persona.toml").write_text("name = [unclosed\n")
    assert persona_config.read_name(tmp_path) is None

def test_read_name_none_when_non_str(tmp_path):
    (tmp_path / "persona.toml").write_text("name = 42\n")
    assert persona_config.read_name(tmp_path) is None
```

(Confirm the test file imports `persona_config` — it does for the `read_skills` tests.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_persona_config.py -k read_name -v`
Expected: FAIL with `AttributeError: module 'harness.persona_config' has no attribute 'read_name'`.

- [ ] **Step 3: Write minimal implementation**

In `harness/persona_config.py`, add (mirroring `read_skills`'s tolerant shape):

```python
def read_name(workspace_dir: Path | None) -> str | None:
    """The persona's display name from <workspace_dir>/persona.toml `name`.
    Returns None when the dir/file is absent, unreadable, corrupt, or the key is
    missing or not a string. The caller falls back to the persona id."""
    if workspace_dir is None:
        return None
    path = workspace_dir / PERSONA_TOML
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        return None
    name = data.get("name")
    return name if isinstance(name, str) and name else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_persona_config.py -q`
Expected: PASS — the 6 new tests + all existing `read_skills` tests.

- [ ] **Step 5: Commit**

```bash
git add harness/persona_config.py tests/test_persona_config.py
git commit -m "feat(persona): persona_config.read_name (persona.toml display name)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `AgentRail` widget + `PersonaSelected` message

**Files:**
- Create: `harness/tui/widgets/agent_rail.py`
- Test: `tests/test_tui_pilot.py`

**Interfaces:**
- Consumes: `PersonaRow` (Task 1).
- Produces:
  - `class PersonaSelected(Message): id: str` (a Textual message)
  - `class AgentRail(Widget)` with `set_rows(rows: tuple[PersonaRow, ...]) -> None`; renders one selectable line per row; posts `PersonaSelected(id)` on enter/click of a row.

- [ ] **Step 1: Write the failing pilot test**

Add to `tests/test_tui_pilot.py` (reuse the file's pilot-app bootstrap idiom — read its top for `HarnessTui(agent_cmd=FAKE_CMD, ...)` and `app.run_test()`). Test the widget in isolation by mounting it and checking it renders rows + posts the message:

```python
async def test_agent_rail_renders_rows_and_posts_selection():
    from harness.tui.widgets.agent_rail import AgentRail, PersonaSelected
    from harness.tui.roster import PersonaRow
    from textual.app import App

    posted = []

    class _Probe(App):
        def compose(self):
            yield AgentRail(id="rail")
        def on_persona_selected(self, msg: PersonaSelected):
            posted.append(msg.id)

    app = _Probe()
    async with app.run_test() as pilot:
        rail = app.query_one("#rail", AgentRail)
        rail.set_rows((
            PersonaRow(id="default", name="default", active=False),
            PersonaRow(id="fred", name="Fred R.", active=True),
        ))
        await pilot.pause()
        # the rendered content shows both names
        text = rail._rail_text()           # a helper that returns the rendered lines as one str
        assert "default" in text and "Fred R." in text
        # selecting the "fred" row posts PersonaSelected("fred")
        rail.select_id("fred")             # a direct selection entrypoint the widget exposes
        await pilot.pause()
        assert posted == ["fred"]
```

If the pilot file's idiom differs (e.g. a shared `HarnessTui` fixture rather than a bespoke `App`), adapt: the essential assertions are (a) both names render, (b) selecting a row posts `PersonaSelected` with that id. Keep the two helper entrypoints (`_rail_text()` for the rendered text, `select_id(id)` for programmatic selection) so the test is deterministic without simulating key events.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tui_pilot.py -k agent_rail -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'harness.tui.widgets.agent_rail'`.

- [ ] **Step 3: Write the widget**

Create `harness/tui/widgets/agent_rail.py`. Use the SAME Textual list API the repo
already uses in `select_modal.py` — `ListView` + `ListItem(Label(...))` with
`item.data = id`, `lv.clear()`/`lv.append(item)`, and `@on(ListView.Selected)` reading
`event.item.data` (VERIFIED against select_modal.py):

```python
"""AgentRail — the persona list (the C2 drawer's right rail).

Dumb/reactive: given a tuple of PersonaRow, renders one selectable line per
persona (active marker + name) and posts PersonaSelected(id) when a row is
chosen. No business logic — the app composes the rows (roster.persona_rows) and
handles the selection (switch by re-exec). Mirrors select_modal's ListView usage."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.message import Message
from textual.widgets import Label, ListItem, ListView

from harness.tui.roster import PersonaRow

ACTIVE_GLYPH = "●"
IDLE_GLYPH = "○"


class PersonaSelected(Message):
    def __init__(self, id: str) -> None:
        self.id = id
        super().__init__()


def _row_label(r: PersonaRow) -> str:
    return f"{ACTIVE_GLYPH if r.active else IDLE_GLYPH} {r.name}"


class AgentRail(ListView):
    """A selectable persona list. Rows are set via set_rows(); choosing a row
    posts PersonaSelected(id)."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._rows: tuple[PersonaRow, ...] = ()

    def set_rows(self, rows: tuple[PersonaRow, ...]) -> None:
        self._rows = rows
        self.clear()
        for r in rows:
            item = ListItem(Label(_row_label(r), markup=False))
            item.data = r.id                 # carry the id for selection (select_modal pattern)
            self.append(item)

    def _rail_text(self) -> str:
        """The rendered lines as one string (test helper)."""
        return "\n".join(_row_label(r) for r in self._rows)

    def select_id(self, persona_id: str) -> None:
        """Programmatic selection entrypoint (used by tests + enter/click)."""
        self.post_message(PersonaSelected(persona_id))

    @on(ListView.Selected)
    def _on_selected(self, event: ListView.Selected) -> None:
        event.stop()
        pid = getattr(event.item, "data", None)
        if pid:
            self.post_message(PersonaSelected(pid))
```

NOTE: this mirrors `harness/tui/widgets/select_modal.py` (the in-repo ListView
reference) exactly — `ListView`, `ListItem(Label(...))`, `item.data`, `clear()`/
`append()`, `@on(ListView.Selected)` reading `event.item.data`. If `set_rows` is called
before the widget is mounted (no ListView yet), guard `clear()`/`append()` — in
practice the app calls `set_rows` in `action_toggle_rail` AFTER the rail is composed, so
it's mounted. The widget's CONTRACT: `set_rows`, `_rail_text`, `select_id`, posts
`PersonaSelected(id)` on user choice.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tui_pilot.py -k agent_rail -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/tui/widgets/agent_rail.py tests/test_tui_pilot.py
git commit -m "feat(tui): AgentRail widget + PersonaSelected message

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Mount the rail, toggle on `tab`, handle selection → re-exec wiring

**Files:**
- Modify: `harness/tui/app.py` (compose/mount the rail; `action_toggle_rail`; `on_persona_selected`; `_persona_rows`; `__init__` `_switch_persona`)
- Modify: `harness/tui/app.tcss` (rail layout, hidden by default)
- Modify: `harness/tui_main.py` (re-exec with `app._switch_persona`)
- Test: `tests/test_tui_pilot.py`, `tests/test_tui_main.py`

**Interfaces:**
- Consumes: `roster.persona_rows` (Task 1), `persona_config.read_name` (Task 2), `AgentRail`/`PersonaSelected` (Task 3), `persona_select.list_personas`, `paths`, the existing `self._snapshot.active_id` (C2a), `action_reload`'s `_reexec` pattern, `tui_main._relaunch_command`.
- Produces: `app._switch_persona: str | None`; the rail mounted as `#agent-rail` (hidden by default); `tab` toggles it.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_tui_pilot.py` (reuse the file's `HarnessTui` pilot harness):

```python
async def test_rail_hidden_by_default_and_tab_toggles():
    app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")   # the file's bootstrap (test_tui_pilot.py:60)
    async with app.run_test() as pilot:
        rail = app.query_one("#agent-rail")
        assert rail.display is False       # hidden by default
        await pilot.press("tab")
        assert rail.display is True        # tab opens it
        await pilot.press("tab")
        assert rail.display is False       # tab closes it


async def test_selecting_persona_sets_switch_and_reexec():
    app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
    async with app.run_test() as pilot:
        from harness.tui.widgets.agent_rail import PersonaSelected
        # simulate selecting a non-active persona
        app.post_message(PersonaSelected("fred"))
        await pilot.pause()
        assert app._switch_persona == "fred"
        assert app._reexec is True


async def test_selecting_active_persona_is_noop():
    app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
    async with app.run_test() as pilot:
        from harness.tui.widgets.agent_rail import PersonaSelected
        active = app._snapshot.active_id
        app.post_message(PersonaSelected(active))
        await pilot.pause()
        assert app._switch_persona is None     # no switch
        assert app._reexec is False            # no re-exec
```

Add to `tests/test_tui_main.py` (reuse its `isolated_config` fixture + the `_relaunch_command`/`_relaunch_args` pattern):

```python
def test_relaunch_carries_switch_persona(monkeypatch, tmp_path):
    from harness import tui_main
    import argparse
    args = argparse.Namespace(model="vibeproxy", cwd=str(tmp_path), yolo=False, persona="default")
    # simulate an app that requested a switch to "fred"
    class _App: _switch_persona = "fred"
    # the helper tui_main uses to thread the switch into args before relaunch:
    tui_main._apply_switch(args, _App())          # see Step 3 for this tiny helper
    assert args.persona == "fred"
    cmd = tui_main._relaunch_command(args, str(tmp_path))
    assert "--persona" in cmd and "fred" in cmd


def test_relaunch_without_switch_keeps_current_persona(tmp_path):
    from harness import tui_main
    import argparse
    args = argparse.Namespace(model="vibeproxy", cwd=str(tmp_path), yolo=False, persona="default")
    class _App: _switch_persona = None
    tui_main._apply_switch(args, _App())
    assert args.persona == "default"              # unchanged
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_tui_pilot.py -k "rail or persona" tests/test_tui_main.py -k switch -v`
Expected: FAIL — `#agent-rail` not found / `_apply_switch` undefined.

- [ ] **Step 3: Implement**

(a) `harness/tui_main.py` — add the tiny switch-applier and call it in the re-exec block. After `_relaunch_command` (near tui_main.py:66), add:

```python
def _apply_switch(args, app) -> None:
    """If the app requested a persona switch (C2b rail), thread it into args so
    the re-exec launches as the selected persona. No-op when no switch was made."""
    chosen = getattr(app, "_switch_persona", None)
    if chosen:
        args.persona = chosen
```

In `main()`, change the `_reexec` block (tui_main.py:117-118):

```python
    if getattr(app, "_reexec", False):
        _apply_switch(args, app)                   # C2b: switch persona on re-exec
        cmd = _relaunch_command(args, cwd)
```

(b) `harness/tui/app.py`:

Init in `__init__` (next to `self._reexec = False`, app.py:96):

```python
        self._switch_persona = None           # C2b: persona id chosen in the rail (re-exec target)
```

Add `tab` to `BINDINGS` (app.py:75) — append:

```python
                ("tab", "toggle_rail", "Agents")]
```

Mount the rail in `compose()` — after `yield self._status_bar()` (app.py:136), add the rail as a sibling, hidden by default:

```python
        rail = AgentRail(id="agent-rail")
        rail.display = False
        yield rail
```

(import `AgentRail`, `PersonaSelected` from `harness.tui.widgets.agent_rail` at the top.)

Add the toggle action + the row builder + the selection handler (near `action_reload`, app.py:889):

```python
    def _persona_rows(self):
        from harness import persona_select, persona_config, paths
        from harness.tui.roster import persona_rows
        def name_of(pid):
            ws = paths.default_workspace_dir() if pid == "default" \
                else paths.config_dir() / "agents" / pid
            return persona_config.read_name(ws)
        return persona_rows(persona_select.list_personas(),
                            self._snapshot.active_id, name_of)

    def action_toggle_rail(self) -> None:
        rail = self.query_one("#agent-rail", AgentRail)
        if not rail.display:
            rail.set_rows(self._persona_rows())   # refresh on open
            rail.display = True
            rail.focus()
        else:
            rail.display = False

    async def on_persona_selected(self, event: PersonaSelected) -> None:
        event.stop()
        if event.id == self._snapshot.active_id:
            return                                 # switch-to-same is a no-op
        if self._busy:
            return
        self._switch_persona = event.id
        self._busy = True
        self._reexec = True
        self.exit()                                # main() re-execs with the new persona
```

(c) `harness/tui/app.tcss` — add a rail rule (right dock, hidden handled by `display`):

```css
#agent-rail { dock: right; width: 28; border-left: solid $surface; background: $background; }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tui_pilot.py tests/test_tui_main.py -q`
Then the full suite: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS — the new rail/switch tests + zero regressions. If `tab` interferes with an existing focus test, ensure `action_toggle_rail` is the bound action and the binding `event.stop()`s (Textual BINDINGS consume the key). Verify the landing hint still reads "tab agents" (it now does something).

- [ ] **Step 5: Commit**

```bash
git add harness/tui/app.py harness/tui/app.tcss harness/tui_main.py tests/test_tui_pilot.py tests/test_tui_main.py
git commit -m "feat(tui): mount AgentRail, tab toggles, selection switches by re-exec (C2b)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: The `/persona` command

**Files:**
- Modify: `harness/tui/commands.py` (register `/persona`)
- Modify: `harness/tui/app.py` (an `action_persona(arg)` the command delegates to)
- Test: `tests/test_commands.py` (or wherever commands are tested — find it)

**Interfaces:**
- Consumes: `action_toggle_rail` (Task 4), `persona_select.list_personas`, `_notify_line`, the `_switch_persona`/`_reexec` path (Task 4).
- Produces: a `/persona` command; `app.action_persona(arg: str)`.

- [ ] **Step 1: Write the failing test**

Find the commands test file (`grep -rl "build_registry\|Command(" tests/`). Add tests that `/persona` is registered and routes correctly. If commands are tested via the app, add to `tests/test_tui_pilot.py` instead. The behavior to lock:

```python
async def test_slash_persona_no_arg_opens_rail():
    app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
    async with app.run_test() as pilot:
        await app.action_persona("")
        await pilot.pause()
        assert app.query_one("#agent-rail").display is True


async def test_slash_persona_unknown_id_errors_no_switch(monkeypatch):
    app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
    async with app.run_test() as pilot:
        notes = []
        monkeypatch.setattr(app, "_notify_line", lambda m: notes.append(m))
        await app.action_persona("ghost")        # not in list_personas()
        await pilot.pause()
        assert app._switch_persona is None        # no switch
        assert any("ghost" in n for n in notes)   # clear error


async def test_slash_persona_known_id_switches(monkeypatch, tmp_path):
    # make "fred" exist as a workspace so list_personas() includes it
    app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
    async with app.run_test() as pilot:
        monkeypatch.setattr("harness.persona_select.list_personas", lambda: ["default", "fred"])
        await app.action_persona("fred")
        await pilot.pause()
        assert app._switch_persona == "fred"
        assert app._reexec is True
```

Adapt `_boot_app`/`monkeypatch` targets to the test file's real idiom; the lockable behaviors are: no-arg opens the rail, unknown id errors without switching, known id switches.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest -k persona_command -v` (or the file you put them in)
Expected: FAIL — `action_persona` undefined / `/persona` not registered.

- [ ] **Step 3: Implement**

(a) `harness/tui/app.py` — add the action (near `action_toggle_rail`):

```python
    async def action_persona(self, arg: str = "") -> None:
        from harness import persona_select
        target = arg.strip()
        if not target:
            self.action_toggle_rail()              # no arg → open/close the rail
            return
        if target == self._snapshot.active_id:
            return                                 # already on it — no-op
        if target not in persona_select.list_personas():
            self._notify_line(f'no persona "{target}" — open the rail (tab) to see available personas')
            return
        if self._busy:
            return
        self._switch_persona = target
        self._busy = True
        self._reexec = True
        self.exit()
```

(b) `harness/tui/commands.py` — add the handler + register it. After `_yolo` (commands.py ~55):

```python
async def _persona(app, arg: str = "") -> None:
    await app.action_persona(arg)
```

In `build_registry()`'s list, add (after `reload` or near it):

```python
        Command("persona", "Switch persona (or open the agents rail)", _persona),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest -k persona_command -q` then the full suite `.venv/bin/python -m pytest tests/ -q`
Expected: PASS + zero regressions.

- [ ] **Step 5: Commit**

```bash
git add harness/tui/commands.py harness/tui/app.py tests/
git commit -m "feat(tui): /persona command (open rail, or switch by id) (C2b)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Docs + full-suite regression lock

**Files:**
- Modify: `README.md` / `docs/personas.md` (document the rail + switching)
- Test: full suite

- [ ] **Step 1: Update docs**

In `README.md`'s Personas section and `docs/personas.md`, add a short "Switching personas in the TUI" note:

```markdown
### Switching personas (TUI)

Press **tab** to open the **agents rail** — it lists every persona workspace under
`~/.config/harness/agents/`, with the active one marked. Select one (enter/click) to
switch to it; the agent restarts as that persona (its own sessions, memory, and
model). You can also type `/persona <id>` to switch directly, or `/persona` to open
the rail. Display names come from each workspace's `persona.toml` `name` (the id is
used if unset). Only one persona runs at a time today; concurrent personas land in a
later phase.
```

- [ ] **Step 2: Run the FULL suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS — everything green. If a pre-existing pilot flake fires (e.g.
`test_pilot_streams_deltas_into_one_markdown_widget`), re-run once to confirm it's the
known flake, not a C2b regression.

- [ ] **Step 3: Commit**

```bash
git add README.md docs/personas.md
git commit -m "docs(persona): document the agents rail + /persona switching (C2b)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Notes for the executor

- **Codex review point:** request a Codex review after **Task 4** (the switch wiring — re-exec with a chosen persona is the one place a bug means "switched to the wrong/no persona", and it touches the irreversible-ish re-exec path). Tasks 1-3 (pure roster, tolerant reader, dumb widget) and Task 5 (command) take standard review.
- **Read before you write:** Tasks 3-5 reuse the pilot harness in `tests/test_tui_pilot.py` and the `_relaunch_command` pattern in `tests/test_tui_main.py`. The real pilot bootstrap (verified at test_tui_pilot.py:60) is `app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")` then `async with app.run_test() as pilot:` — `REPO` and `FAKE_CMD` are module constants at the top of that file. The test snippets above use exactly that. For `test_tui_main.py`, reuse its `isolated_config` fixture + the `_relaunch_command`/`_relaunch_args` helpers.
- **Textual OptionList API:** Task 3 assumes `OptionList`/`Option`/`OptionSelected`. The installed Textual version's exact API is the source of truth — `harness/tui/widgets/select_modal.py` is the in-repo reference for list-selection widgets; mirror it if the assumed API differs. The widget's CONTRACT (`set_rows`, `_rail_text`, `select_id`, posts `PersonaSelected`) must hold regardless.
- **Order:** Task 1 (roster) → Task 2 (read_name) → Task 3 (widget, needs 1) → Task 4 (wiring, needs 1+2+3) → Task 5 (command, needs 4) → Task 6 (docs+lock). Execute in order.
- **Editable-install shadowing:** run pytest with the WORKTREE as cwd so `harness` resolves to the worktree, not the installed primary checkout.
