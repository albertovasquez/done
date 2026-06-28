# Persona-Switch UX — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the persona-switch transcript-bleed bug — on switch, clear the old persona's conversation from the screen, show a per-persona room header, and handle mid-turn switches by queueing instead of silently ignoring.

**Architecture:** All changes are client-side in `harness/tui/app.py` (no engine change). The sync switch handler `_apply_persona_switch` gains a synchronous transcript clear (extracted from the async `_reset_conversation` so it can be called without `await`), a room header using the local `persona_config.read_name` lookup, and a `_pending_persona` field that defers a mid-turn switch to turn-end — applied *before* the existing queued-prompt drain so a queued prompt runs in the new persona's room.

**Tech Stack:** Python 3.11, Textual TUI, pytest (Textual `run_test()` pilot harness).

## Global Constraints

- **No engine change.** Phase 1 is client-only (`harness/tui/app.py` + tests). Do not modify `acp_agent.py`, `acp_session.py`, `persona_sessions.py`.
- **Keep `_apply_persona_switch` SYNCHRONOUS.** It is called synchronously from the create-persona modal callback (app.py:1149-1153). Making it async would force that path to change — out of scope.
- **Do not reset `_snapshot` in the transcript clear.** `_apply_persona_switch` already applies `PersonaResolved(id)` which owns the snapshot; the clear must touch only transcript children + stream state.
- **Per-persona accent color is DEFERRED.** Header uses the persona **display name** only, in the existing single brand `$accent` token. No new palette.
- **Phase 1 copy must NOT promise visible persistence.** Use "separate," never "remembers across switches" (that claim waits for Phase 2 replay). Exact copy strings are in the tasks.
- **Test command (from this worktree):** `<repo-root>/.venv/bin/python -m pytest tests/<file> -q -p no:cacheprovider` — the repo-root `.venv`; `tests/conftest.py` (merged in #94) resolves imports to this worktree regardless of cwd.
- **Verify TUI behavior with a pilot test** (Textual `run_test()`), not by eyeballing — but layout/visual claims still get a manual SVG/screenshot check at the end (per project rule).

---

## File Structure

- `harness/tui/app.py` (modify) — all production changes:
  - new sync `_clear_transcript()` (extracted visual reset)
  - new `_persona_display_name(pid)` helper (DRY: shared by header + rail's `name_of`)
  - `_apply_persona_switch` — call clear + write room header
  - `on_persona_selected` — mid-turn: set `_pending_persona` + "still working" line
  - new `_pending_persona` instance field + turn-end `finally` applies it before `_drain_queue`
- `tests/test_persona_switch_ux.py` (create) — all Phase 1 behavior tests.

---

## Task 1: Extract a synchronous `_clear_transcript()` and use it in `_reset_conversation`

**Files:**
- Modify: `harness/tui/app.py` (`_reset_conversation` ~L764)
- Test: `tests/test_persona_switch_ux.py` (create)

**Interfaces:**
- Produces: `_clear_transcript(self) -> None` — sync; removes `#transcript` children (when `_started`) and resets stream state (`_streaming_md=None`, `_stream_buf=""`, `_stream_closed=True`, `_boundary_after=False`). Does NOT touch `_snapshot`, `_tokens`, or call async APIs.

**Context:** Today `_reset_conversation` (async) does both the visual clear AND the snapshot/token reset (app.py:764-781). We split the visual+stream part into a sync helper so the sync switch path can call it. `_reset_conversation` keeps its existing behavior by calling the new helper then doing the snapshot/token reset — so its current callers (`action_clear`, etc.) are unchanged.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_persona_switch_ux.py
import asyncio
from pathlib import Path

from harness.tui.app import HarnessTui
from harness.tui.widgets.prompt_area import PromptArea
from harness.tui.widgets.agent_rail import PersonaSelected
from textual.containers import VerticalScroll
from textual.widgets import Markdown, Static
import sys

REPO = Path(__file__).resolve().parent.parent
FAKE_CMD = [sys.executable, str(REPO / "tests/fake_agent.py")]


def _transcript_children(app):
    try:
        return list(app.query_one("#transcript", VerticalScroll).children)
    except Exception:
        return None


async def _send_first_prompt(pilot, app, text):
    app.query_one("#landing-input", PromptArea).focus()
    app.query_one("#landing-input", PromptArea).value = text
    await pilot.press("enter")


def test_clear_transcript_empties_children_and_resets_stream_state():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await _send_first_prompt(pilot, app, "hello")
            for _ in range(60):
                await pilot.pause()
                if app._started and app.query("#transcript"):
                    break
            assert _transcript_children(app), "precondition: transcript has children"
            snap_before = app._snapshot          # must be preserved
            app._clear_transcript()
            await pilot.pause()
            assert _transcript_children(app) == [], "transcript not emptied"
            assert app._streaming_md is None
            assert app._stream_buf == ""
            assert app._stream_closed is True
            assert app._boundary_after is False
            assert app._snapshot is snap_before, "_clear_transcript must NOT touch _snapshot"

    asyncio.run(go())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `<repo-root>/.venv/bin/python -m pytest tests/test_persona_switch_ux.py::test_clear_transcript_empties_children_and_resets_stream_state -q -p no:cacheprovider`
Expected: FAIL with `AttributeError: 'HarnessTui' object has no attribute '_clear_transcript'`

- [ ] **Step 3: Add the sync helper and refactor `_reset_conversation` to use it**

Replace the body of `_reset_conversation` (app.py ~L764-781). New code:

```python
    def _clear_transcript(self) -> None:
        """Sync visual reset: empty the transcript and reset stream-accumulation
        state so no late delta bleeds into a fresh view. Does NOT touch _snapshot
        (its owner re-applies it) or _tokens. Safe to call from sync paths (e.g.
        the persona switch) — unlike async _reset_conversation."""
        if self._started:
            self._transcript.remove_children()
        self._streaming_md = None
        self._stream_buf = ""
        self._stream_closed = True
        self._boundary_after = False

    async def _reset_conversation(self) -> None:
        """Empty the transcript and reset per-conversation state WITHOUT leaving
        the conversation view (flipping _started=False would query the removed
        #landing-input/#header-text and crash). No-op before the first prompt."""
        self._clear_transcript()
        self._tokens = 0
        self._snapshot = initial_snapshot()
        self._refresh_status()
        # Refresh mounted widgets if they exist (they may not be in all states)
        try:
            self.query_one("#activity-region", ActivityRegion).update_from(self._snapshot.active)
        except Exception:
            pass
```

> Note: `remove_children()` is awaited in the original. Textual's `remove_children()` returns an `AwaitComplete` that also works fire-and-forget; the pilot test's `await pilot.pause()` lets it settle. The async `_reset_conversation` no longer awaits it — acceptable because callers `await` the coroutine and pump the event loop. The test asserts `== []` after a `pause()`, proving it settles.

- [ ] **Step 4: Run the test to verify it passes**

Run: `<repo-root>/.venv/bin/python -m pytest tests/test_persona_switch_ux.py::test_clear_transcript_empties_children_and_resets_stream_state -q -p no:cacheprovider`
Expected: PASS

- [ ] **Step 5: Run the existing clear/reset tests to confirm no regression**

Run: `<repo-root>/.venv/bin/python -m pytest tests/test_tui_pilot.py -q -p no:cacheprovider`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add tests/test_persona_switch_ux.py harness/tui/app.py
git commit -m "refactor(tui): extract sync _clear_transcript from _reset_conversation"
```

---

## Task 2: Add `_persona_display_name(pid)` helper (DRY name lookup)

**Files:**
- Modify: `harness/tui/app.py` (`_persona_rows` ~L1087-1097)
- Test: `tests/test_persona_switch_ux.py`

**Interfaces:**
- Produces: `_persona_display_name(self, pid: str) -> str` — returns `persona_config.read_name(ws)` for the persona's workspace dir, falling back to `pid` when name is absent. `ws = paths.default_workspace_dir()` if `pid == "default"` else `paths.config_dir() / "agents" / pid`.
- Consumes (refactor): `_persona_rows`'s inner `name_of` should delegate to this helper to avoid two copies of the resolution.

**Context:** `_persona_rows` (app.py:1087-1093) already resolves a persona's display name via `read_name(ws)`. The room header (Task 3) needs the same lookup. Extract it once.

- [ ] **Step 1: Write the failing test**

```python
def test_persona_display_name_falls_back_to_id(tmp_path, monkeypatch):
    # default persona with no name set → returns the id "default"
    app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
    name = app._persona_display_name("default")
    assert isinstance(name, str) and name, "must return a non-empty string"
    # an unknown persona id with no workspace → falls back to the id verbatim
    assert app._persona_display_name("nope-nonexistent") == "nope-nonexistent"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `<repo-root>/.venv/bin/python -m pytest tests/test_persona_switch_ux.py::test_persona_display_name_falls_back_to_id -q -p no:cacheprovider`
Expected: FAIL with `AttributeError: ... '_persona_display_name'`

- [ ] **Step 3: Add the helper and refactor `_persona_rows.name_of` to use it**

Add this method (near `_persona_rows`, app.py ~L1087):

```python
    def _persona_display_name(self, pid: str) -> str:
        """The persona's display name from its persona.toml `name`, falling back
        to the id. One lookup shared by the rail rows and the room header."""
        from harness import persona_config, paths
        ws = paths.default_workspace_dir() if pid == "default" \
            else paths.config_dir() / "agents" / pid
        return persona_config.read_name(ws) or pid
```

Then change `_persona_rows`'s inner `name_of` (app.py ~L1090-1093) to delegate:

```python
    def _persona_rows(self):
        from harness import persona_select
        from harness.tui.roster import persona_rows
        active = self._snapshot.active
        return persona_rows(persona_select.list_personas(), self._current_persona(),
                            self._persona_display_name,
                            active_status=(active.state if active else AgentState.IDLE))
```

> Note: `name_of` took `pid` and so does `_persona_display_name` — same signature, direct substitution. Remove the now-unused local `name_of` and the `persona_config, paths` imports from `_persona_rows` (they moved into the helper).

- [ ] **Step 4: Run the test to verify it passes**

Run: `<repo-root>/.venv/bin/python -m pytest tests/test_persona_switch_ux.py::test_persona_display_name_falls_back_to_id -q -p no:cacheprovider`
Expected: PASS

- [ ] **Step 5: Confirm rail rows still render (no regression)**

Run: `<repo-root>/.venv/bin/python -m pytest tests/test_tui_pilot.py tests/test_agent_rail.py -q -p no:cacheprovider`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add tests/test_persona_switch_ux.py harness/tui/app.py
git commit -m "refactor(tui): extract _persona_display_name shared by rail + header"
```

---

## Task 3: Clear the transcript on switch and write the room header

**Files:**
- Modify: `harness/tui/app.py` (`_apply_persona_switch` ~L1120-1139)
- Test: `tests/test_persona_switch_ux.py`

**Interfaces:**
- Consumes: `_clear_transcript()` (Task 1), `_persona_display_name(pid)` (Task 2).
- Behavior change: after a successful switch, persona A's messages are gone from the transcript; a room header + empty-room line appear; the old terse `now talking to persona: {id}` line is removed.

**Context:** This is the core bug fix. `_apply_persona_switch` (app.py:1120) currently repoints the session and appends `now talking to persona: {id}` into the *existing* transcript (the bleed). We clear first, then write the header. Clearing happens AFTER `self._session_id`/`PersonaResolved` are set is fine — `_clear_transcript` doesn't touch the snapshot — but to keep the room header as the first visible line, clear right before writing it.

Exact copy (Global Constraints — no persistence promise):
- Room header line: `now in {Name}'s conversation`
- Subline: `a separate conversation`
- Empty-room line: `This is {Name}'s conversation — separate from your others. Say hello.`

- [ ] **Step 1: Write the failing test (the bug repro)**

```python
class _FakeConn:
    def __init__(self):
        self.ext_calls = []
        self.set_persona_response = {
            "ok": True, "id": "maya", "session_id": "sess-maya", "model": "mock"}

    async def ext_method(self, method, params):
        self.ext_calls.append((method, params))
        if method == "harness/set_persona":
            return self.set_persona_response
        return {}


def _transcript_text(app):
    parts = []
    for w in (app.query_one("#transcript", VerticalScroll).children):
        if isinstance(w, Markdown):
            parts.append(getattr(w, "source", "") or "")
        elif isinstance(w, Static):
            parts.append(str(w.content))
    return "\n".join(parts)


def test_switch_clears_old_persona_messages_and_shows_room_header():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await _send_first_prompt(pilot, app, "MARKER42")
            for _ in range(60):
                await pilot.pause()
                if "MARKER42" in _transcript_text(app):
                    break
            assert "MARKER42" in _transcript_text(app), "precondition"

            app._conn = _FakeConn()
            app._turn_active = False
            await app.on_persona_selected(PersonaSelected("maya"))
            await pilot.pause()

            text = _transcript_text(app)
        assert "MARKER42" not in text, f"old persona's message bled through:\n{text}"
        assert "now in" in text and "conversation" in text, f"room header missing:\n{text}"
        assert "now talking to persona:" not in text, "old terse line should be gone"

    asyncio.run(go())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `<repo-root>/.venv/bin/python -m pytest tests/test_persona_switch_ux.py::test_switch_clears_old_persona_messages_and_shows_room_header -q -p no:cacheprovider`
Expected: FAIL — `MARKER42` still present AND/OR `now in ... conversation` missing (current code appends `now talking to persona: maya` below the old messages).

- [ ] **Step 3: Rewrite `_apply_persona_switch` to clear + header**

Replace the body of `_apply_persona_switch` (app.py ~L1120-1139). New code:

```python
    def _apply_persona_switch(self, resp: dict, note: str | None = None) -> None:
        """Apply a successful set_persona/create_persona result: repoint the session,
        update the indicator + footer, CLEAR the prior persona's transcript (each
        persona is a separate conversation — Phase 1 shows a fresh room, replay is
        Phase 2), write the room header, close the rail, refocus. `note` overrides
        the default room header (create passes its own)."""
        self._session_id = resp["session_id"]
        self._persona_seen = True
        self._apply(PersonaResolved(resp["id"]))   # updates snapshot + ActivityRegion
        self._refresh_persona()                    # _apply does NOT refresh the chip
        model = resp.get("model")
        if model:
            self._worker_model_id = model
            self._refresh_meta_line()
        # Each persona is its own conversation: clear the previous room so its
        # messages don't bleed into this one, then show whose room this is.
        self._clear_transcript()
        name = self._persona_display_name(resp["id"])
        if note:
            self._append_line(_c("muted", note))
        else:
            self._append_line(_c("accent", f"now in {name}'s conversation"))
            self._append_line(_c("muted", "a separate conversation"))
            self._append_line(
                _c("muted", f"This is {name}'s conversation — separate from your others. Say hello."))
        # close the drawer + refocus the prompt
        self._show_drawer(False)
        self._active_input().focus()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `<repo-root>/.venv/bin/python -m pytest tests/test_persona_switch_ux.py::test_switch_clears_old_persona_messages_and_shows_room_header -q -p no:cacheprovider`
Expected: PASS

- [ ] **Step 5: Update the existing switch-confirmation test if present**

There is an existing test `test_apply_persona_switch_writes_visible_confirmation` (tests/test_tui_pilot.py ~L1849) asserting the old `now talking to persona:` line. Run the pilot suite; if it fails, update that test to assert the new room header (`now in {name}'s conversation`) instead of the removed line.

Run: `<repo-root>/.venv/bin/python -m pytest tests/test_tui_pilot.py -q -p no:cacheprovider`
Expected: PASS after updating the one confirmation test.

- [ ] **Step 6: Commit**

```bash
git add tests/test_persona_switch_ux.py tests/test_tui_pilot.py harness/tui/app.py
git commit -m "fix(tui): clear transcript on persona switch + show room header

Stops the bleed: switching no longer leaves the prior persona's
conversation on screen with a confirmation appended below it. Each
persona now opens its own room (Phase 1; history replay is Phase 2)."
```

---

## Task 4: Mid-turn switch — queue via `_pending_persona`, fire on turn-end before drain

**Files:**
- Modify: `harness/tui/app.py` — instance fields (~L130), `on_persona_selected` (~L1099-1102), turn-end `finally` (~L867)
- Test: `tests/test_persona_switch_ux.py`

**Interfaces:**
- Produces: `self._pending_persona: str | None` instance field (init `None`).
- Behavior: selecting a persona mid-turn writes a "still working" line and sets `_pending_persona` (last-wins); on turn-end the pending switch applies *before* `_drain_queue()`.

**Context:** `on_persona_selected` returns silently when `_turn_active` (app.py:1101). `_queued` is a prompt FIFO only and does not carry switches. The turn `finally` (app.py:867) runs `_drain_queue()` which sends `_queued.pop(0)` on the *current* `_session_id`; the pending switch must apply first so a queued prompt runs in the new room.

- [ ] **Step 1: Write the failing tests**

```python
def test_mid_turn_switch_is_queued_not_immediate():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            app._conn = _FakeConn()
            app._turn_active = True               # simulate a running turn
            before = app._current_persona()
            await app.on_persona_selected(PersonaSelected("maya"))
            await pilot.pause()
            assert app._pending_persona == "maya", "switch should be queued"
            assert app._current_persona() == before, "must NOT switch mid-turn"
            assert ("harness/set_persona", {"id": "maya"}) not in app._conn.ext_calls

    asyncio.run(go())


def test_mid_turn_switch_last_wins():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            app._conn = _FakeConn()
            app._turn_active = True
            await app.on_persona_selected(PersonaSelected("maya"))
            await app.on_persona_selected(PersonaSelected("alex"))
            await pilot.pause()
            assert app._pending_persona == "alex", "later selection overwrites earlier"

    asyncio.run(go())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `<repo-root>/.venv/bin/python -m pytest tests/test_persona_switch_ux.py -k mid_turn -q -p no:cacheprovider`
Expected: FAIL with `AttributeError: ... '_pending_persona'`

- [ ] **Step 3: Add the field, the mid-turn branch, and the turn-end application**

(3a) Add the instance field near the other queue field (app.py ~L132, after `_queued`):

```python
        self._pending_persona: str | None = None   # a switch requested mid-turn; applied on turn-end
```

(3b) Replace the early `_turn_active` guard in `on_persona_selected` (app.py:1100-1102). Current:

```python
        event.stop()
        if self._turn_active:                 # inert mid-turn — full prompt/stream lifecycle
            return
```

New:

```python
        event.stop()
        if self._turn_active:                 # don't switch under a live turn — queue it
            if event.id != self._current_persona():
                self._pending_persona = event.id          # last-wins
                name = self._persona_display_name(self._current_persona())
                self._notify_line(f"{name} is still working — switching when this turn finishes.")
            self._show_drawer(False)
            return
```

(3c) In the turn-end `finally` (app.py ~L867), apply the pending switch BEFORE `_drain_queue()`. Current tail:

```python
                self._active_input().focus()
                self._drain_queue()               # auto-send the next queued message, if any
```

New:

```python
                self._active_input().focus()
                self._apply_pending_persona()     # honor a mid-turn switch request first…
                self._drain_queue()               # …then any queued prompt runs in the NEW room
```

(3d) Add the `_apply_pending_persona` method (near `_drain_queue`, app.py ~L869):

```python
    def _apply_pending_persona(self) -> None:
        """If a persona switch was requested mid-turn, apply it now (turn-end),
        BEFORE draining queued prompts — so a prompt typed during the old turn
        runs in the NEW persona's room, not the old one."""
        pid = self._pending_persona
        if pid is None or self._conn is None or pid == self._current_persona():
            self._pending_persona = None
            return
        self._pending_persona = None
        self.run_worker(self._switch_persona(pid), thread=False)

    async def _switch_persona(self, pid: str) -> None:
        """The async half of a deferred switch: call set_persona, then apply."""
        try:
            resp = await self._conn.ext_method("harness/set_persona", {"id": pid})
        except Exception as e:
            self._notify_line(f"could not switch persona: {e}")
            return
        if not resp.get("ok"):
            self._notify_line(f"persona: {resp.get('error', 'switch failed')}")
            return
        self._apply_persona_switch(resp)
```

> Note: `on_persona_selected`'s idle path already does the `set_persona` call inline; `_switch_persona` factors the same call+apply for the deferred path. Optionally refactor the idle path to call `_switch_persona` too (DRY) — do that only if the pilot suite stays green; otherwise leave the idle path as-is.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `<repo-root>/.venv/bin/python -m pytest tests/test_persona_switch_ux.py -k mid_turn -q -p no:cacheprovider`
Expected: PASS

- [ ] **Step 5: Add a turn-end-applies-pending integration test**

```python
def test_pending_switch_applies_on_turn_end_before_drain():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            # reach conversation state so a transcript exists
            await _send_first_prompt(pilot, app, "hello")
            for _ in range(60):
                await pilot.pause()
                if app._started and app.query("#transcript"):
                    break
            app._conn = _FakeConn()
            app._pending_persona = "maya"
            app._turn_active = False
            app._apply_pending_persona()          # simulate the turn-end call
            for _ in range(20):
                await pilot.pause()
            assert app._current_persona() == "maya", "pending switch did not apply"
            assert app._pending_persona is None, "pending must be cleared"

    asyncio.run(go())
```

Run: `<repo-root>/.venv/bin/python -m pytest tests/test_persona_switch_ux.py::test_pending_switch_applies_on_turn_end_before_drain -q -p no:cacheprovider`
Expected: PASS

- [ ] **Step 6: Run the full pilot suite (regression)**

Run: `<repo-root>/.venv/bin/python -m pytest tests/test_tui_pilot.py -q -p no:cacheprovider`
Expected: PASS. (If `test_persona_selected_inert_mid_turn` exists and asserts the OLD silent-inert behavior, update it: mid-turn now sets `_pending_persona` and writes a line, and still does NOT call `set_persona` immediately.)

- [ ] **Step 7: Commit**

```bash
git add tests/test_persona_switch_ux.py tests/test_tui_pilot.py harness/tui/app.py
git commit -m "feat(tui): queue mid-turn persona switch, apply on turn-end before drain"
```

---

## Task 5: Rail row hint copy + full-suite verification + visual check

**Files:**
- Modify: `harness/tui/widgets/agent_rail.py` (tooltip/hint copy) — only if a hint surface exists; otherwise skip the copy edit and note it.
- Test: `tests/test_persona_switch_ux.py` (only if a testable copy surface exists)

**Interfaces:** none new.

**Context:** Spec §5.5 wants a rail hint `Each persona keeps its own conversation. ↑↓ to choose · enter to switch`. Add it only where a real hint/tooltip surface exists (check `agent_rail.py` and the QUICK KEYS legend container). If there is no such surface, record it as a deferred copy nicety rather than inventing a widget.

- [ ] **Step 1: Inspect the rail for an existing hint/legend surface**

Run: `grep -n "QUICK KEYS\|hint\|tooltip\|legend\|↑↓\|enter to" harness/tui/widgets/agent_rail.py harness/tui/app.py`
If a legend/hint string exists, update/extend it with the copy above. If not, skip to Step 3 and note the deferral in the commit body.

- [ ] **Step 2: (If a surface exists) update the copy and add/adjust its test**

Make the minimal edit to the existing legend/hint string. If a test asserts that string, update it to include "keeps its own conversation".

- [ ] **Step 3: Run the FULL test suite**

Run: `<repo-root>/.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Expected: PASS (all). Record the count in the commit/PR.

- [ ] **Step 4: Visual confirmation (per project rule — never trust tests for layout)**

Write a tiny pilot that boots, sends a prompt, switches persona, and saves an SVG; open it and confirm: (a) the old persona's messages are gone, (b) the room header reads "now in {name}'s conversation", (c) nothing is visually broken. Use `app.save_screenshot(...)` inside a `run_test()` block (see prior art in the agents-drawer work). This is a manual check, not a committed test.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(tui): rail hint copy for per-persona conversations + full-suite green"
```

---

## Self-Review

**Spec coverage (§5):**
- §5.1 clear-on-switch + room header → Tasks 1 + 3 ✓
- §5.1 async seam (extract sync clear, keep `_apply_persona_switch` sync) → Task 1 + Task 3 (no async change) ✓
- §5.1 don't reset `_snapshot` → Task 1 (test asserts `_snapshot is snap_before`) ✓
- §5.2 identity frame via display name; per-persona accent deferred → Tasks 2 + 3 (single `$accent`) ✓
- §5.3 mid-turn queue via `_pending_persona`, last-wins, apply-before-drain → Task 4 ✓
- §5.3 late-delta safety → satisfied structurally: pending switch runs in turn-end `finally` (after the turn) and `_clear_transcript` resets the stream buffer; the existing session-id filter is the backstop. No code needed beyond ordering (Task 4 step 3c). Noted.
- §5.4 empty-room honest copy ("separate", not "remembers") → Task 3 exact strings ✓
- §5.5 rail hint copy → Task 5 (conditional on a real surface) ✓
- §7 create-persona keeps working (sync `_apply_persona_switch`) → Global Constraint + Task 3 preserves sync signature; create path unchanged. Covered by pilot suite (Task 3 step 5, Task 4 step 6). ✓
- §7 unsaved draft kept → `_clear_transcript` touches transcript+stream only, not the composer value; no code clears the input. Implicitly preserved; add an assertion if a draft test is cheap (optional).

**Placeholder scan:** No TBD/TODO; every code step shows real code. ✓

**Type consistency:** `_clear_transcript()`, `_persona_display_name(pid)`, `_pending_persona: str | None`, `_apply_pending_persona()`, `_switch_persona(pid)` used consistently across tasks. `read_name(workspace_dir)` matches the real signature (`persona_config.read_name(ws)`). ✓

**One deliberate gap to flag at review:** Task 4's `_switch_persona` duplicates the idle-path `set_persona` call; the optional DRY refactor (idle path delegates to `_switch_persona`) is left to the implementer's discretion gated on green tests, to avoid forcing a riskier change.
