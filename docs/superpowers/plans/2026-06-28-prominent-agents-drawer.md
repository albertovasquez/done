# Prominent AGENTS Drawer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the persona rail into a prominent card list (name + status chip + sub-line per agent), pre-highlight the active persona when the drawer opens, and add a QUICK KEYS legend panel — all on the existing design system.

**Architecture:** `PersonaRow` gains a truthful `status: AgentState` (active row = real state, others IDLE). A pure `card_markup(row)` composes each card's markup; `AgentRail.set_rows` wraps it in a `Static.persona-card` ListItem and sets the highlight index to the active row. A new `QuickKeysPanel` static widget renders the legend. CSS in `app.tcss` gives the cards their boxed look + active/highlight accent. Enter-on-active is a no-op guard; ↑↓-only-highlight is already correct and preserved.

**Tech Stack:** Python 3.11, pytest, Textual. Vendored mini-swe-agent (`upstream/`, never edited).

**Spec:** `docs/superpowers/specs/2026-06-28-prominent-agents-drawer-design.md`.

## Global Constraints

- Work in the worktree, never `main` (AGENTS.md #1). Branch `worktree-prominent-agents-drawer`.
- **Zero `upstream/` edits** (AGENTS.md #4).
- Tests: `.venv/bin/python -m pytest tests/ -q` from the worktree root (primary venv; tests `sys.path.insert(0,".")`).
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- **No new design tokens** (components.md: reuse before invent). Colours/glyphs come from `theme.py`/`tokens.py`/`status_chip.py` maps.
- **Status is truthful-static:** active card = real `AgentState` + real task count; non-active = IDLE / "idle". Never fabricate.
- **`PersonaRow.status` MUST be the last field with a default** (`AgentState.IDLE`) so existing `PersonaRow(id=, name=, active=)` construction and the equality assertions in `tests/test_tui_roster.py` keep passing.

---

### Task 1: `PersonaRow.status` + roster threads the active state

**Files:**
- Modify: `harness/tui/roster.py` (add `status` field; `persona_rows` accepts the active state)
- Test: `tests/test_tui_roster.py` (add)

**Interfaces:**
- Consumes: `AgentState` from `harness.tui.state`.
- Produces: `PersonaRow(id, name, active, status: AgentState = AgentState.IDLE)`; `persona_rows(personas, active_id, name_of, active_status: AgentState = AgentState.IDLE) -> tuple[PersonaRow, ...]` — the active row carries `active_status`, all others `IDLE`. Task 2 reads `row.status`; Task 4 passes `active_status`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tui_roster.py  (add)
from harness.tui.state import AgentState


def test_active_row_carries_status_others_idle():
    rows = persona_rows(["default", "fred"], "fred", _names({}),
                        active_status=AgentState.RUNNING_TOOL)
    by_id = {r.id: r for r in rows}
    assert by_id["fred"].status == AgentState.RUNNING_TOOL    # active = real state
    assert by_id["default"].status == AgentState.IDLE         # others idle


def test_status_defaults_to_idle_when_not_passed():
    rows = persona_rows(["fred"], "fred", _names({}))
    assert rows[0].status == AgentState.IDLE


def test_personarow_still_constructs_with_three_positional_fields():
    # back-compat: existing call sites + equality assertions must keep working
    assert PersonaRow(id="x", name="X", active=True).status == AgentState.IDLE
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tui_roster.py -k "status or three_positional" -q`
Expected: FAIL — `PersonaRow` has no `status`; `persona_rows` has no `active_status`.

- [ ] **Step 3: Write minimal implementation**

In `harness/tui/roster.py`:

```python
from harness.tui.state import AgentState
...
@dataclass(frozen=True)
class PersonaRow:
    id: str
    name: str
    active: bool
    status: AgentState = AgentState.IDLE   # active row = real state; others IDLE


def persona_rows(
    personas: list[str],
    active_id: str,
    name_of: Callable[[str], str | None],
    active_status: AgentState = AgentState.IDLE,
) -> tuple[PersonaRow, ...]:
    rows = [
        PersonaRow(id=pid, name=(name_of(pid) or pid), active=(pid == active_id),
                   status=(active_status if pid == active_id else AgentState.IDLE))
        for pid in personas
    ]
    if not any(r.id == active_id for r in rows):
        rows.append(PersonaRow(id=active_id, name=active_id, active=True, status=active_status))
    return tuple(rows)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tui_roster.py -q`
Expected: PASS (new + all existing roster tests — the 3-field equality ones still pass via the default).

- [ ] **Step 5: Commit**

```bash
git add harness/tui/roster.py tests/test_tui_roster.py
git commit -m "feat(roster): PersonaRow.status (active row real state, others idle)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `card_markup` — the pure card renderer

**Files:**
- Modify: `harness/tui/widgets/agent_rail.py` (add `card_markup`; keep `_row_label` removed/replaced)
- Test: `tests/test_agent_rail.py` (create)

**Interfaces:**
- Consumes: `PersonaRow` (Task 1); `AgentState`; `_STATE_GLYPH`, `STATUS_LABEL`, `state_color_token` from `status_chip`/`tokens`.
- Produces: `card_markup(row: PersonaRow, subline: str) -> str` — a 2-line markup string: line 1 = name (left, `$accent` bold if active else `$foreground`) + status label + dot (right); line 2 = `$muted` subline. No icon-tile glyph. The caller (Task 3) computes `subline`.

> Status label/colour reuse: `from harness.tui.tokens import STATUS_LABEL` and
> `from harness.tui.widgets.status_chip import state_color_token, _STATE_GLYPH`,
> `_STATE_TOKEN`. The label is `STATUS_LABEL[_STATE_GLYPH-or-token key]`. Confirm
> the exact key: `state_color_token(state)` gives the colour token; for the LABEL,
> map the state via the same table the StatusChip uses. Simplest: derive both from
> the state with a tiny local helper `_status_label(state)` that returns
> `STATUS_LABEL.get(key, "IDLE")` — grep `STATUS_LABEL` keys in tokens.py and map
> AgentState→key (idle→"idle", running_tool/responding/thinking→"running",
> scheduled→"scheduled"). Keep it a pure dict lookup.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_agent_rail.py
import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

from harness.tui.roster import PersonaRow
from harness.tui.state import AgentState
from harness.tui.widgets.agent_rail import card_markup


def test_active_card_name_is_accent_bold_with_real_status():
    row = PersonaRow(id="fred", name="Fred", active=True, status=AgentState.RUNNING_TOOL)
    out = card_markup(row, "2 tasks")
    assert "Fred" in out
    assert "$accent" in out and "[b]" in out       # active name styling
    assert "RUNNING" in out                          # real status label
    assert "2 tasks" in out                          # sub-line


def test_idle_card_is_foreground_with_idle_status():
    row = PersonaRow(id="sam", name="Sam", active=False, status=AgentState.IDLE)
    out = card_markup(row, "idle")
    assert "Sam" in out
    assert "$foreground" in out                       # non-active name
    assert "IDLE" in out
    assert "idle" in out


def test_card_has_no_icon_tile_glyph():
    # the brand ≡ tile was dropped; it must not appear
    out = card_markup(PersonaRow(id="x", name="X", active=True), "idle")
    assert "≡" not in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_agent_rail.py -q`
Expected: FAIL — `card_markup` does not exist.

- [ ] **Step 3: Write minimal implementation**

In `harness/tui/widgets/agent_rail.py`, replace the `_row_label` one-liner with the
card renderer (keep `ACTIVE_GLYPH`/`IDLE_GLYPH` only if still used by the dot):

```python
from harness.tui.state import AgentState
from harness.tui.tokens import GLYPH, STATUS_LABEL
from harness.tui.widgets.status_chip import _STATE_GLYPH, state_color_token

_STATUS_KEY = {
    AgentState.IDLE: "idle",
    AgentState.THINKING: "running",
    AgentState.RESPONDING: "running",
    AgentState.RUNNING_TOOL: "running",
    AgentState.AWAITING_PERMISSION: "scheduled",
    AgentState.AWAITING_DECISION: "scheduled",
    AgentState.SCHEDULED: "scheduled",
    AgentState.DONE: "idle",
    AgentState.FAILED: "idle",
}


def _status_label(state: AgentState) -> str:
    return STATUS_LABEL.get(_STATUS_KEY.get(state, "idle"), "IDLE")


def card_markup(row: PersonaRow, subline: str) -> str:
    token = state_color_token(row.status)
    dot = GLYPH[_STATE_GLYPH.get(row.status, "idle")]
    name = f"[$accent][b]{row.name}[/b][/]" if row.active else f"[$foreground]{row.name}[/]"
    status = f"[${token}]{_status_label(row.status)} {dot}[/]"
    return f"{name}    {status}\n[$muted]{subline}[/]"
```

> Confirm `STATUS_LABEL` actually has keys `"idle"`/`"running"`/`"scheduled"` (grep
> showed idle/running/scheduled present at tokens.py:30-35). If a key is missing,
> the `.get(..., "IDLE")` fallback keeps it safe.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_agent_rail.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add harness/tui/widgets/agent_rail.py tests/test_agent_rail.py
git commit -m "feat(rail): card_markup — name + status chip + sub-line (no icon tile)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `AgentRail.set_rows` renders cards + pre-highlights active

**Files:**
- Modify: `harness/tui/widgets/agent_rail.py` (`set_rows`)
- Test: `tests/test_agent_rail.py` (add — pilot-style mount)

**Interfaces:**
- Consumes: `card_markup` (Task 2); `PersonaRow.status` (Task 1).
- Produces: `set_rows(rows, *, subline_of: Callable[[PersonaRow], str] | None = None)` — each `ListItem` holds a `Static(card_markup(r, subline))` with class `persona-card` (+ `active` on the active row); sets `self.index` to the active row's index (pre-highlight). `subline_of` defaults to a function returning `"idle"` for non-active and `""`/`"idle"` for active when the app doesn't supply counts (Task 4 supplies the real one).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_agent_rail.py  (add)
import pytest
from textual.app import App
from textual.widgets import Static
from harness.tui.widgets.agent_rail import AgentRail


class _Host(App):
    def compose(self):
        yield AgentRail(id="r")


@pytest.mark.asyncio
async def test_set_rows_renders_cards_and_preselects_active():
    rows = (PersonaRow(id="default", name="default", active=False),
            PersonaRow(id="fred", name="Fred", active=True, status=AgentState.RUNNING_TOOL))
    app = _Host()
    async with app.run_test() as pilot:
        rail = app.query_one("#r", AgentRail)
        rail.set_rows(rows)
        await pilot.pause()
        # pre-highlight on the active row (index 1, "fred")
        assert rail.index == 1
        # each item carries a persona-card Static; active item has the 'active' class
        items = list(rail.query("ListItem"))
        assert len(items) == 2
        assert items[1].has_class("active")
        assert any("Fred" in str(s.renderable) for s in app.query(Static))
```

> NOTE: if the repo's pilot tests use the `async def go(): ... asyncio.run(go())`
> wrapper instead of `@pytest.mark.asyncio` (see `tests/test_tui_pilot.py`), match
> THAT style. Grep `asyncio.run(go())` vs `pytest.mark.asyncio` in `tests/` and
> follow the majority. The assertion logic is unchanged.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_agent_rail.py -k set_rows -q`
Expected: FAIL — items render the old `Label`, no `persona-card`/`active` class, `index` not set to active.

- [ ] **Step 3: Write minimal implementation**

In `agent_rail.py`, rewrite `set_rows`:

```python
    def set_rows(self, rows: tuple[PersonaRow, ...], *, subline_of=None) -> None:
        self._rows = rows
        self.clear()
        active_index = 0
        for i, r in enumerate(rows):
            subline = (subline_of(r) if subline_of else ("idle"))
            item = ListItem(Static(card_markup(r, subline), markup=True))
            item.data = r.id
            item.add_class("persona-card")
            if r.active:
                item.add_class("active")
                active_index = i
            self.append(item)
        if rows:
            self.index = active_index           # pre-highlight the active persona
```

> Remove the now-unused `Label` import if nothing else uses it. Keep `_rail_text`
> (test helper) working — update it to use `card_markup` or the row names so it
> doesn't reference the deleted `_row_label`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_agent_rail.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/tui/widgets/agent_rail.py tests/test_agent_rail.py
git commit -m "feat(rail): render persona cards + pre-highlight the active row

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: App wires real active status + sub-line; CSS for the cards

**Files:**
- Modify: `harness/tui/app.py` (`_persona_rows` passes the active state; the two open sites pass `subline_of`), `harness/tui/app.tcss` (`.persona-card` styling + widen drawer)
- Test: `tests/test_tui_pilot.py` (add — open pre-highlights active)

**Interfaces:**
- Consumes: `persona_rows(..., active_status=...)` (Task 1); `set_rows(..., subline_of=...)` (Task 3).
- Produces: opening the drawer renders real cards (active = real state + real task count) and pre-highlights the active row.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tui_pilot.py  (add; match the file's go()/asyncio.run style + ctor)
def test_drawer_open_prehighlights_active_persona():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await _send_first_prompt(pilot, app, "hi")
            for _ in range(50):
                await pilot.pause()
                if app._started:
                    break
            app.action_toggle_rail()          # open the drawer
            await pilot.pause()
            from harness.tui.widgets.agent_rail import AgentRail
            rail = app.query_one("#agent-rail", AgentRail)
            assert rail.display
            # active persona row is pre-highlighted (not forced to index 0)
            active_id = app._current_persona()
            assert rail._rows[rail.index].id == active_id

    asyncio.run(go())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tui_pilot.py -k prehighlight -q`
Expected: FAIL if the active persona isn't at index 0 (or PASS trivially if it is — in that case ALSO assert `rail.index` equals the active row index explicitly, which Task 3 guarantees; the test stands as a regression guard).

- [ ] **Step 3: Write minimal implementation**

`harness/tui/app.py` — `_persona_rows` passes the active live state:

```python
    def _persona_rows(self):
        from harness import persona_select, persona_config, paths
        from harness.tui.roster import persona_rows
        def name_of(pid):
            ws = paths.default_workspace_dir() if pid == "default" \
                else paths.config_dir() / "agents" / pid
            return persona_config.read_name(ws)
        active = self._snapshot.active
        return persona_rows(persona_select.list_personas(), self._current_persona(),
                            name_of, active_status=(active.state if active else AgentState.IDLE))
```

A sub-line helper (real count for active, "idle" otherwise):

```python
    def _persona_subline(self, row):
        active = self._snapshot.active
        if row.active and active is not None:
            n = len(active.tasks)
            return f"{n} task{'s' if n != 1 else ''}" if n else "idle"
        return "idle"
```

Both open sites pass it. At `app.py:579` (tab) and `:1036` (toggle), change
`rail.set_rows(self._persona_rows())` to:

```python
            rail.set_rows(self._persona_rows(), subline_of=self._persona_subline)
```

Ensure `AgentState` is imported in `app.py` (grep; add to the `from harness.tui.state import (...)` block if absent).

`harness/tui/app.tcss` — replace the `#agent-rail` rule (line 147) with the card styling + wider drawer:

```css
/* ---- prominent agents drawer ---- */
#agent-rail { dock: right; width: 34; border-left: solid $surface; background: $background; padding: 1 1; }
#agent-rail .persona-card {
    background: $surface; border: round $surface; padding: 0 1; margin-bottom: 1; height: 4;
}
#agent-rail .persona-card.active { border: round $accent; background: $accent 10%; }
#agent-rail ListItem.--highlight .persona-card { border: round $accent; }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tui_pilot.py -k prehighlight -q`
Expected: PASS. Then the rail-related pilots: `.venv/bin/python -m pytest tests/test_tui_pilot.py -k "rail or persona or drawer" -q`.

- [ ] **Step 5: Commit**

```bash
git add harness/tui/app.py harness/tui/app.tcss tests/test_tui_pilot.py
git commit -m "feat(tui): wire real active status + sub-line; card CSS + wider drawer

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: No-op guard on enter-active

**Files:**
- Modify: `harness/tui/app.py` (`on_persona_selected`)
- Test: `tests/test_tui_pilot.py` (add)

**Interfaces:**
- Consumes: existing `on_persona_selected` / `_apply_persona_switch`.
- Produces: selecting the already-active persona closes the drawer WITHOUT calling `set_persona`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tui_pilot.py  (add)
def test_enter_on_active_persona_is_noop_close():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        calls = []
        async with app.run_test() as pilot:
            await _send_first_prompt(pilot, app, "hi")
            for _ in range(50):
                await pilot.pause()
                if app._started:
                    break

            async def fake_ext(method, params):
                calls.append((method, params))
                return {"ok": True, "id": params["id"], "session_id": "s"}
            app._conn.ext_method = fake_ext     # spy on set_persona

            from harness.tui.widgets.agent_rail import AgentRail, PersonaSelected
            rail = app.query_one("#agent-rail", AgentRail)
            rail.display = True
            active_id = app._current_persona()
            await app.on_persona_selected(PersonaSelected(active_id))   # enter on active
            await pilot.pause()
            assert calls == []                  # no set_persona call
            assert not rail.display             # drawer closed

    asyncio.run(go())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tui_pilot.py -k noop -q`
Expected: FAIL — `on_persona_selected` calls `ext_method` even for the active id.

- [ ] **Step 3: Write minimal implementation**

In `on_persona_selected` (app.py:1043), add the guard before the `ext_method` call:

```python
    async def on_persona_selected(self, event: PersonaSelected) -> None:
        event.stop()
        if self._turn_active:
            return
        if self._conn is None:
            return
        if event.id == self._current_persona():
            # already this persona — just close the drawer, no switch
            try:
                self.query_one("#agent-rail", AgentRail).display = False
            except Exception:
                pass
            self._active_input().focus()
            return
        try:
            resp = await self._conn.ext_method("harness/set_persona", {"id": event.id})
        ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tui_pilot.py -k noop -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/tui/app.py tests/test_tui_pilot.py
git commit -m "feat(tui): enter on the active persona is a no-op close (no switch)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: `QuickKeysPanel` legend + mount in the drawer

**Files:**
- Create: `harness/tui/widgets/quick_keys.py`
- Modify: `harness/tui/app.py` (compose the panel under the rail), `harness/tui/app.tcss` (panel styling)
- Test: `tests/test_quick_keys.py` (create)

**Interfaces:**
- Consumes: nothing new.
- Produces: `QuickKeysPanel` (a `Static`/`Vertical`) rendering `≡ QUICK KEYS` + one row per `QUICK_KEYS` entry. `QUICK_KEYS: list[tuple[str, str]]` lists only keys that work today: `tab`/`↑↓`/`enter`/`esc`/`/`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_quick_keys.py
import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

from harness.tui.widgets.quick_keys import QUICK_KEYS, quick_keys_markup


def test_quick_keys_lists_working_keys():
    keys = [k for k, _ in QUICK_KEYS]
    assert "tab" in keys and "enter" in keys and "/" in keys
    # the legend header + every label renders
    md = quick_keys_markup()
    assert "QUICK KEYS" in md
    for _, label in QUICK_KEYS:
        assert label in md


def test_quick_keys_does_not_list_unbound_keys():
    # legend documents real keys only — no '?' help unless it's actually bound
    assert "?" not in [k for k, _ in QUICK_KEYS]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_quick_keys.py -q`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Write minimal implementation**

```python
# harness/tui/widgets/quick_keys.py
"""QuickKeysPanel — the drawer's static keybinding legend (≡ QUICK KEYS).
A reference, not behavior; lists only keys that work today. Tokens only."""

from __future__ import annotations

from textual.widgets import Static

QUICK_KEYS: list[tuple[str, str]] = [
    ("tab", "switch panel"),
    ("↑↓", "navigate"),
    ("enter", "switch agent"),
    ("esc", "close"),
    ("/", "focus prompt"),
]


def quick_keys_markup() -> str:
    head = "[$muted][b]≡ QUICK KEYS[/b][/]"
    rows = "\n".join(f"[$muted on $surface] {k} [/]  [$muted]{label}[/]"
                     for k, label in QUICK_KEYS)
    return head + "\n" + rows


class QuickKeysPanel(Static):
    def __init__(self) -> None:
        super().__init__(quick_keys_markup(), markup=True, id="quick-keys")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_quick_keys.py -q`
Expected: PASS.

Then mount it in `app.py` right after the rail (`yield rail` at ~app.py:160):

```python
        rail = AgentRail(id="agent-rail")
        rail.display = False
        yield rail
        from harness.tui.widgets.quick_keys import QuickKeysPanel
        qk = QuickKeysPanel()
        qk.display = False               # shown/hidden with the rail
        yield qk
```

And toggle it together with the rail in BOTH open/close sites (`action_toggle_rail`
and the tab/esc handlers): set `qk.display = rail.display` whenever the rail's
display flips. Minimal helper:

```python
    def _set_drawer_visible(self, visible: bool) -> None:
        self.query_one("#agent-rail", AgentRail).display = visible
        try:
            self.query_one("#quick-keys", Static).display = visible
        except Exception:
            pass
```

Replace the direct `rail.display = True/False` assignments in `action_toggle_rail`,
the tab open (`app.py:580`), the esc close (`:592`), and `_apply_persona_switch`
(`:1079`) with `self._set_drawer_visible(...)` so the legend tracks the rail.

CSS (`app.tcss`):

```css
#quick-keys { dock: right; width: 34; height: auto; padding: 1 1; background: $background; offset-y: 100%; }
```

> The drawer layout: rail + quick-keys both dock right. If docking both is fiddly,
> wrap them in a single `#agent-drawer` Vertical (dock right, width 34) containing
> the rail (height 1fr) and the panel (height auto) — adjust compose to yield the
> container. Prefer the container if two docked siblings fight for space.

- [ ] **Step 5: Commit**

```bash
git add harness/tui/widgets/quick_keys.py harness/tui/app.py harness/tui/app.tcss tests/test_quick_keys.py
git commit -m "feat(tui): QUICK KEYS legend panel under the agents drawer

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Full-suite green + primary-checkout check

**Files:**
- Possibly modify: any pilot that asserted the OLD one-line rail text (e.g. expecting `"● name"`).

- [ ] **Step 1: Run the whole suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS. If a pilot asserted the old `● name` rail line or the old width-28
drawer, update it to the card form / new width and note the change in the commit.

- [ ] **Step 2: Verify primary checkout untouched**

Run: `git -C /Users/alberto/Work/Quiubo/harness status --short`
Expected: empty.

- [ ] **Step 3: Confirm `upstream/` untouched**

Run: `git diff --name-only main...HEAD | grep '^upstream/' || echo "upstream untouched"`
Expected: `upstream untouched`.

- [ ] **Step 4: Commit any cleanup**

```bash
git add -A
git commit -m "test: update rail baselines to the prominent card drawer; suite green

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- Spec §1 unit 1 (`PersonaRow.status`) → Task 1. ✓
- Spec §1 unit 2 + §2 (card visual, tokens, no icon tile) → Task 2 (`card_markup`) + Task 4 (CSS). ✓
- Spec §1 unit 3 + §3 (set_rows cards + pre-highlight active) → Task 3 + Task 4 (open sites pass subline + real status). ✓
- Spec §3 (enter no-op on active; ↑↓-only-highlight preserved; esc) → Task 5 (no-op guard); ↑↓ preserved (no `Highlighted` listener added — Task 3 only sets index). ✓
- Spec §1 unit 4 + §2 (QUICK KEYS panel) → Task 6. ✓
- Spec §4 (tests) → Tasks 1-6 tests; Task 7 full green. ✓
- Spec §5 (deferred: real telemetry, icon tile, new key behaviors) → not built; status truthful-static; legend lists only working keys. ✓

**Placeholder scan:** No TBD/TODO. Every code step shows code. Two NOTES point the implementer to confirm real specifics against neighboring code (the pilot test style `go()/asyncio.run` vs `pytest.mark.asyncio`; the two-docked-siblings-vs-container CSS choice) — these are "match the established harness / pick the simpler layout" pointers with the production code fully shown.

**Type consistency:** `PersonaRow.status: AgentState` (T1) consumed by `card_markup(row, subline)` (T2), `set_rows(rows, *, subline_of)` (T3), and `_persona_subline`/`_persona_rows(active_status=)` (T4). `card_markup(row: PersonaRow, subline: str) -> str` identical across T2/T3. `QUICK_KEYS: list[tuple[str,str]]` + `quick_keys_markup() -> str` (T6). The no-op guard reads `self._current_persona()` (T5), matching `_persona_rows`'s active source.

**One implementer caution (CSS):** Textual docking of two right-docked siblings (rail + quick-keys) can fight for vertical space. If the panel doesn't sit cleanly below the rail, wrap both in a `#agent-drawer` Vertical (dock right, width 34) per the Task 6 NOTE — purely a layout container, no logic change.
