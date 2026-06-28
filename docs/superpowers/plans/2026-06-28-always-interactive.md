# Always-Interactive TUI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Guarantee the `dn` TUI never presents a state where the user cannot type, focus, cancel, or see liveness — and prove it with a Pilot invariant test.

**Architecture:** Five surgical changes to `harness/tui/app.py` (+ the state reducer + fake-agent), each test-first. The load-bearing piece is a Pilot invariant test asserting interactivity across every turn phase. The highest-risk change (per-chunk render coalescing) is split into its own task with the review findings R1–R4 encoded as explicit failing tests first.

**Tech Stack:** Python 3.10+, Textual (TUI), Textual `Pilot` test harness, pytest, ACP (agent subprocess protocol).

## Global Constraints

- Always work in the worktree `/Users/alberto/Work/Quiubo/harness/.worktrees/repro-input-freeze` (branch `repro-input-freeze`). Never edit the primary checkout.
- Run tests from the worktree root with the primary venv so conftest resolves this worktree's src:
  `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/ -q`
- Interaction model is **type + queue** (FIFO, one prompt per session). Do NOT add concurrent-prompt or interrupt-and-replace behavior.
- Display-only changes (C4) must NOT alter routing, the ACP wire, or timing of the agent.
- The `_boundary_after` / late-delta / new-step widget-routing logic in `_stream_message` is OUT OF SCOPE and must remain behaviorally identical.
- Spec: `docs/superpowers/specs/2026-06-28-always-interactive-design.md` (findings R1–R6 are authoritative).

## File Structure

- Modify `harness/tui/app.py` — all five behaviors (key handling, working indicator, stream render, placeholder).
- Modify `harness/tui/state.py` — phase labels in `_reduce_agent` (C4).
- Modify `tests/fake_agent.py` — already has `SLOW`; add a `MANYCHUNKS` rapid-burst path (C1/C2 tests).
- Create `tests/test_tui_always_interactive.py` — the invariant test (C1).
- Create `tests/test_tui_stream_coalesce.py` — coalescing + R1/R3/R4 tests (C2).
- Create `tests/test_tui_esc_precedence.py` — ESC ladder tests (C3).
- Modify `tests/test_tui_state.py` — phase-label reducer tests (C4).
- (Repro `tests/test_tui_input_freeze_repro.py` already committed; leave as-is.)

---

### Task 1: Phase-labeled liveness (C4)

Replace the static "Thinking" label with phase-aware labels so the pre-stream window stops reading as frozen. Display-only.

**Files:**
- Modify: `harness/tui/state.py:174-209` (`_reduce_agent`)
- Test: `tests/test_tui_state.py`

**Interfaces:**
- Consumes: existing events `TurnStarted`, `ItemReceived` (both defined IN `harness/tui/state.py` — there is no `events.py`), `AgentSnapshot(id, name, ...)`. `ItemReceived` takes a single `item` arg, duck-typed for `.kind`; a message chunk has `kind == "message"`.
- Produces: `activity_label` values `"Classifying…"`, `"Responding"` used by `ActivityRegion`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tui_state.py  (append)
from dataclasses import dataclass
from harness.tui.state import AgentSnapshot, _reduce_agent, AgentState, TurnStarted, ItemReceived

@dataclass
class _FakeItem:
    kind: str

def test_turn_start_label_is_classifying():
    a = AgentSnapshot(id="a", name="x")
    a = _reduce_agent(a, TurnStarted())
    assert a.activity_label == "Classifying…"
    assert a.state == AgentState.THINKING

def test_first_message_chunk_flips_to_responding():
    a = _reduce_agent(AgentSnapshot(id="a", name="x"), TurnStarted())
    a = _reduce_agent(a, ItemReceived(item=_FakeItem(kind="message")))
    assert a.activity_label == "Responding"
    assert a.state == AgentState.RESPONDING
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_tui_state.py -k "classifying or responding" -q`
Expected: FAIL — `activity_label == "Thinking"`, not `"Classifying…"`.

- [ ] **Step 3: Write minimal implementation**

In `harness/tui/state.py`, change the `TurnStarted` branch label and ensure the first `AgentMessageChunk` sets `RESPONDING`:

```python
    if isinstance(event, TurnStarted):
        return replace(a, state=AgentState.THINKING, activity_label="Classifying…",
                       tool=None, decision=None, tasks=(), tools=(), plan=(),
                       elapsed=0.0)
```

The existing `ItemReceived` branch (state.py:204-209) already transitions a `kind == "message"` item to `RESPONDING` with `activity_label="Responding"` — so `test_first_message_chunk_flips_to_responding` passes once the `TurnStarted` label is changed. Do NOT modify the `ItemReceived` branch (it carries the #99 terminal-state guard). The only edit is the `TurnStarted` label.

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_tui_state.py -q`
Expected: PASS (all state tests, including pre-existing).

- [ ] **Step 5: Commit**

```bash
git add harness/tui/state.py tests/test_tui_state.py
git commit -m "feat(tui): phase-labeled liveness — Classifying… before first token (C4)"
```

---

### Task 2: Queue-visibility placeholder (C5)

Make it obvious the composer is live during a turn and that Enter queues.

**Files:**
- Modify: `harness/tui/app.py` (`_submit_text` ~534, `_send_prompt` finally ~874)
- Test: `tests/test_tui_always_interactive.py` (created here; extended in Task 5)

**Interfaces:**
- Consumes: `self._active_input()` (`PromptArea`, has live `placeholder`), `self._turn_active`.
- Produces: placeholder string `"Type to queue your next message…"` during a turn; `"Reply…"` when idle.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tui_always_interactive.py  (new file)
import sys
sys.path.insert(0, "upstream/src"); sys.path.insert(0, ".")
import asyncio
from pathlib import Path
from harness.tui.app import HarnessTui
from harness.tui.widgets.prompt_area import PromptArea

REPO = Path(__file__).resolve().parent.parent
FAKE_CMD = [sys.executable, str(REPO / "tests/fake_agent.py")]

def test_placeholder_shows_queue_hint_during_turn():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#landing-input", PromptArea).focus()
            app.query_one("#landing-input", PromptArea).value = "SLOW look at file"
            await pilot.press("enter")
            seen = False
            for _ in range(50):
                await pilot.pause()
                if app._turn_active and "queue" in app._active_input().placeholder.lower():
                    seen = True
                    break
            assert seen, f"placeholder never showed queue hint (was {app._active_input().placeholder!r})"
            for _ in range(50):
                await pilot.pause()
                if not app._turn_active:
                    break
            assert "queue" not in app._active_input().placeholder.lower(), \
                "placeholder stuck on queue hint after turn ended"
    asyncio.run(go())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_tui_always_interactive.py::test_placeholder_shows_queue_hint_during_turn -q`
Expected: FAIL — placeholder stays "Reply…".

- [ ] **Step 3: Write minimal implementation**

In `_submit_text` (after `self._turn_active = True`, app.py:534):

```python
        self._active_input().placeholder = "Type to queue your next message…"
```

In `_send_prompt`'s `finally` (where `self._active_input().disabled = False`, app.py:877), restore the idle placeholder:

```python
                self._active_input().placeholder = "Reply…"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_tui_always_interactive.py::test_placeholder_shows_queue_hint_during_turn -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add harness/tui/app.py tests/test_tui_always_interactive.py
git commit -m "feat(tui): queue-hint placeholder during active turn (C5)"
```

---

### Task 3: ESC precedence ladder (C3, findings R5/R6)

Make ESC always cancel an in-flight turn, with explicit precedence so it never swallows the slash-menu ESC.

**Files:**
- Modify: `harness/tui/app.py:609-645` (`on_key`), `action_cancel` (~1108)
- Test: `tests/test_tui_esc_precedence.py` (new)

**Interfaces:**
- Consumes: `self._slash`, `self._turn_active`, `self._active_input().value`, `self.action_cancel()`.
- Produces: a `tx.cancel` trace + `conn.cancel()` call when ESC pressed during a turn; an `— canceling… —` muted line.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tui_esc_precedence.py  (new file)
import sys
sys.path.insert(0, "upstream/src"); sys.path.insert(0, ".")
import asyncio
from pathlib import Path
from harness.tui.app import HarnessTui
from harness.tui.widgets.prompt_area import PromptArea

REPO = Path(__file__).resolve().parent.parent
FAKE_CMD = [sys.executable, str(REPO / "tests/fake_agent.py")]

def test_esc_during_turn_cancels_even_with_text_in_box():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        cancelled = []
        async with app.run_test() as pilot:
            await pilot.pause()
            # stub the connection's cancel to record the call
            orig = app.action_cancel
            async def spy():
                cancelled.append(True)
                await orig()
            app.action_cancel = spy
            app.query_one("#landing-input", PromptArea).focus()
            app.query_one("#landing-input", PromptArea).value = "SLOW look at file"
            await pilot.press("enter")
            for _ in range(50):
                await pilot.pause()
                if app._turn_active and app._streaming_md is None:
                    break
            # type into the box, then ESC — must cancel the turn, not just clear text
            app._active_input().value = "half typed next prompt"
            await pilot.press("escape")
            await pilot.pause()
            assert cancelled, "ESC during turn did not trigger action_cancel"
            assert app._active_input().value == "half typed next prompt", \
                "ESC during turn wrongly cleared the typed text (R6: first ESC cancels, text stays)"
    asyncio.run(go())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_tui_esc_precedence.py -q`
Expected: FAIL — current `on_key` clears the text (app.py:614) and never reaches cancel.

- [ ] **Step 3: Write minimal implementation**

In `on_key`, in the `self._slash is None` branch, insert the turn-active cancel rung BEFORE the clear-text rung. Replace the block starting at app.py:614:

```python
        if self._slash is None:
            # ESC precedence ladder (spec R5): slash already handled below
            # (this branch is the slash-closed case). Turn active → cancel FIRST,
            # before clear-text and rail-close, so a slow turn is always escapable.
            if event.key == "escape" and self._turn_active:
                event.stop()
                await self.action_cancel()
                return
            # menu closed, no turn: esc with text clears it; empty box falls
            # through to action_cancel (the global "Cancel turn" binding).
            if event.key == "escape" and self._active_input().value:
                self._active_input().value = ""
                event.stop()
                return
```

In `action_cancel` (app.py:1108), after the `conn.cancel()` call, add the feedback line:

```python
            self._append_line(_c("muted", "— canceling… —"))
```

(The slash-open ESC at app.py:643 is untouched — it is in the `else` branch and still has top priority because the `self._slash is None` guard fails when the menu is open.)

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_tui_esc_precedence.py -q`
Expected: PASS

- [ ] **Step 5: Run the slash-menu ESC regression**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_tui_commands.py -q`
Expected: PASS (slash-menu ESC still closes the menu — proves R5 precedence preserved).

- [ ] **Step 6: Commit**

```bash
git add harness/tui/app.py tests/test_tui_esc_precedence.py
git commit -m "feat(tui): ESC always cancels in-flight turn; explicit precedence ladder (C3, R5/R6)"
```

---

### Task 4: Render coalescing (C2, findings R1–R4) — HIGHEST RISK

Coalesce per-chunk markdown re-renders behind a 12 Hz flusher, with sync flush on close (R1), start/stop interval lifecycle (R2), capture md+buf at schedule time (R3), and mount-safe first render (R4).

**Files:**
- Modify: `harness/tui/app.py:936-987` (`_stream_message`), `__init__` (~125), `_end_stream` (~834), `_reset_conversation` (~772)
- Test: `tests/test_tui_stream_coalesce.py` (new), `tests/fake_agent.py` (add `MANYCHUNKS`)

**Interfaces:**
- Consumes: `self._stream_buf`, `self._streaming_md`, `self._stream_closed`, `Markdown.update`.
- Produces: `self._stream_dirty: bool`, `self._stream_timer` (Textual `Timer` or `None`), `self._flush_stream()` method, `self._schedule_flush()` helper.

- [ ] **Step 1: Add a rapid-chunk path to the fake agent**

In `tests/fake_agent.py`, before the `STREAM` block, add:

```python
        # MANYCHUNKS: emit many small deltas with NO awaits between them, to
        # stress the client's per-chunk render path (coalescing test C2).
        if "MANYCHUNKS" in text:
            for i in range(60):
                await self._conn.session_update(
                    session_id, update_agent_message_text(f"word{i} "))
            return acp.PromptResponse(stop_reason="end_turn")
```

- [ ] **Step 2: Write the failing tests (coalescing + R1 no-loss)**

```python
# tests/test_tui_stream_coalesce.py  (new file)
import sys
sys.path.insert(0, "upstream/src"); sys.path.insert(0, ".")
import asyncio
from pathlib import Path
from harness.tui.app import HarnessTui
from harness.tui.widgets.prompt_area import PromptArea

REPO = Path(__file__).resolve().parent.parent
FAKE_CMD = [sys.executable, str(REPO / "tests/fake_agent.py")]

def _drive(prompt_text, after):
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        calls = {"n": 0}
        async with app.run_test() as pilot:
            await pilot.pause()
            # wrap Markdown.update to count renders on the live widget
            import harness.tui.app as appmod
            orig_update = appmod.Markdown.update
            def counting_update(self, *a, **k):
                calls["n"] += 1
                return orig_update(self, *a, **k)
            appmod.Markdown.update = counting_update
            try:
                app.query_one("#landing-input", PromptArea).focus()
                app.query_one("#landing-input", PromptArea).value = prompt_text
                await pilot.press("enter")
                for _ in range(200):
                    await pilot.pause()
                    if not app._turn_active:
                        break
                await pilot.pause()
                await after(app, calls)
            finally:
                appmod.Markdown.update = orig_update
    asyncio.run(go())

def test_manychunks_coalesces_renders():
    async def after(app, calls):
        # 60 chunks must NOT cause ~60 full re-renders; coalesced << chunk count
        assert calls["n"] < 30, f"expected coalesced renders, got {calls['n']} for 60 chunks"
    _drive("MANYCHUNKS", after)

def test_manychunks_no_text_lost():
    async def after(app, calls):
        # the final buffer must contain all 60 words (R1: nothing dropped)
        assert "word0 " in app._stream_buf and "word59 " in app._stream_buf, \
            f"text lost: tail={app._stream_buf[-40:]!r}"
    _drive("MANYCHUNKS", after)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_tui_stream_coalesce.py -q`
Expected: `test_manychunks_coalesces_renders` FAILS (today ~60 renders, one per chunk). `test_manychunks_no_text_lost` may already pass (sets the no-regression bar for R1).

- [ ] **Step 4: Implement coalescing**

In `__init__` (near app.py:126), add state:

```python
        self._stream_dirty = False            # buffer changed since last paint
        self._stream_timer = None             # Textual Timer while a stream is open
```

Replace the tail of `_stream_message` (app.py:984-987) — the `self._stream_buf += text` onward — with:

```python
        self._stream_buf += text
        self._stream_dirty = True
        if self._stream_closed:
            # R1: a late delta after close cannot rely on the interval (it is
            # stopped on close). Flush SYNCHRONOUSLY so trailing text is never lost.
            self._flush_stream()
        else:
            self._ensure_stream_timer()
        self._transcript.scroll_end(animate=False)
```

Add these methods near `_stream_message`:

```python
    def _ensure_stream_timer(self) -> None:
        # R2: start a 12Hz flusher on stream-open; it is stopped on close/reset.
        if self._stream_timer is None:
            self._stream_timer = self.set_interval(1 / 12, self._flush_stream)

    def _stop_stream_timer(self) -> None:
        if self._stream_timer is not None:
            self._stream_timer.stop()
            self._stream_timer = None

    def _flush_stream(self) -> None:
        # R2/R3: no-op when nothing to paint or the widget is gone (teardown);
        # capture the CURRENT widget+buffer so a flush can't paint a stale buffer
        # into a new widget after a reset.
        if not self._stream_dirty or self._streaming_md is None:
            return
        md, buf = self._streaming_md, self._stream_buf
        self._stream_dirty = False
        # R4: md.update is a no-op until the widget mounts; call_after_refresh
        # guarantees the FIRST paint lands post-mount, matching prior behavior.
        self.call_after_refresh(md.update, buf)
```

In the new-widget branch of `_stream_message` (app.py:978-982, where `self._stream_buf = ""`), the first render path now goes through `_ensure_stream_timer()` (non-closed) — the `call_after_refresh` inside `_flush_stream` preserves R4. No change needed there beyond the tail replacement above.

In `_end_stream` (app.py:834), stop the timer and force a final paint of whatever is buffered:

```python
        self._stream_closed = True
        self._flush_stream()          # R1: paint any unpainted tail before close
        self._stop_stream_timer()     # R2: no free-running timer between turns
        if boundary:
            self._boundary_after = True
```

In `_reset_conversation` (app.py:772-775, where `_stream_buf` is reset), stop the timer and clear dirty so a stale flush can't fire:

```python
        self._stop_stream_timer()
        self._stream_dirty = False
        self._streaming_md = None
        self._stream_buf = ""
        self._stream_closed = True
        self._boundary_after = False
```

- [ ] **Step 5: Run the coalescing tests to verify they pass**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_tui_stream_coalesce.py -q`
Expected: PASS (renders coalesced AND no text lost).

- [ ] **Step 6: Write + run the R3 stale-flush and R1 late-delta tests**

```python
# tests/test_tui_stream_coalesce.py  (append)
def test_late_delta_after_close_renders():
    """R1: a delta arriving after _end_stream still paints (sync flush)."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#landing-input", PromptArea).focus()
            app.query_one("#landing-input", PromptArea).value = "STREAM hi"
            await pilot.press("enter")
            for _ in range(100):
                await pilot.pause()
                if not app._turn_active:
                    break
            # stream is now closed; simulate a late delta and assert it lands
            assert app._stream_closed
            before = app._stream_buf
            app._stream_message("LATE")
            await pilot.pause()
            assert app._stream_buf == before + "LATE"
            assert not app._stream_dirty, "late delta not flushed (R1 sync flush failed)"
    asyncio.run(go())
```

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_tui_stream_coalesce.py -q`
Expected: PASS.

- [ ] **Step 7: Run the FULL existing TUI suite (no regression in stream routing / #99 family)**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_tui_pilot.py tests/test_tui_responding_stuck_stress.py tests/test_tui_state.py -q`
Expected: PASS — proves the `_boundary_after` late-delta / new-step routing and the #99 terminal-state guard are behaviorally unchanged.

- [ ] **Step 8: Commit**

```bash
git add harness/tui/app.py tests/test_tui_stream_coalesce.py tests/fake_agent.py
git commit -m "perf(tui): coalesce per-chunk render behind 12Hz flusher; sync flush on close (C2, R1-R4)"
```

---

### Task 5: The invariant test (C1) — the durable guarantee

A single test that asserts the four-part predicate across pre-stream gap, mid-burst render, and cancel.

**Files:**
- Modify: `tests/test_tui_always_interactive.py` (add the invariant cases)

**Interfaces:**
- Consumes: `HarnessTui`, `pilot.click`, `pilot.press`, `app.action_cancel`, all behaviors from Tasks 1–4.
- Produces: nothing (test-only capstone).

- [ ] **Step 1: Write the invariant test**

```python
# tests/test_tui_always_interactive.py  (append)
def _assert_interactive(app):
    inp = app._active_input()
    assert not inp.disabled, "composer disabled"
    return inp

def test_composer_interactive_in_every_phase():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#landing-input", PromptArea).focus()
            app.query_one("#landing-input", PromptArea).value = "SLOW look"
            await pilot.press("enter")

            # phase A: pre-stream gap (turn active, no chunk) — click + type
            for _ in range(50):
                await pilot.pause()
                if app._turn_active and app._streaming_md is None:
                    break
            inp = _assert_interactive(app)
            app.set_focus(None); await pilot.pause()
            await pilot.click("#conversation-input"); await pilot.pause()
            assert app.focused is inp, "click did not focus composer in pre-stream gap"
            before = inp.value
            await pilot.press("a"); await pilot.pause()
            assert inp.value == before + "a", "keystroke lost in pre-stream gap"

            # phase B: cancel is always reachable
            await pilot.press("escape"); await pilot.pause()
            for _ in range(50):
                await pilot.pause()
                if not app._turn_active:
                    break

        # phase C: mid-burst render — separate run, MANYCHUNKS
        app2 = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app2.run_test() as pilot:
            await pilot.pause()
            app2.query_one("#landing-input", PromptArea).focus()
            app2.query_one("#landing-input", PromptArea).value = "MANYCHUNKS"
            await pilot.press("enter")
            probed = False
            for _ in range(200):
                await pilot.pause()
                if app2._streaming_md is not None and app2._turn_active:
                    inp2 = _assert_interactive(app2)
                    inp2.focus(); await pilot.pause()
                    before2 = inp2.value
                    await pilot.press("b"); await pilot.pause()
                    assert inp2.value == before2 + "b", "keystroke lost mid-burst render"
                    probed = True
                    break
            assert probed, "never caught the mid-burst window"
    asyncio.run(go())
```

- [ ] **Step 2: Run the invariant test**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_tui_always_interactive.py -q`
Expected: PASS (depends on Tasks 1–4 being in place).

- [ ] **Step 3: Run the FULL suite green**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/ -q`
Expected: PASS (entire suite).

- [ ] **Step 4: Manual acceptance (perceived-freeze fix)**

Run `dn --debug` in a repo, ask it to read a large file ("look at <big file> what do you think"). During the pre-stream spinner and the streaming burst: click the composer and type — confirm focus + keystrokes appear, the label reads "Classifying…" then "Responding", and ESC cancels. (Pilot can't prove real-terminal repaint; this is the acceptance check.)

- [ ] **Step 5: Commit**

```bash
git add tests/test_tui_always_interactive.py
git commit -m "test(tui): always-interactive invariant across pre-stream/burst/cancel (C1)"
```

---

## Self-Review

**1. Spec coverage:**
- C1 invariant test → Task 5 ✅
- C2 render coalescing + R1/R2/R3/R4 → Task 4 (R1 sync flush, R2 timer lifecycle, R3 capture+reset clear, R4 call_after_refresh in flusher) ✅
- C3 ESC ladder + R5/R6 → Task 3 (R5 slash-stays-priority via `_slash is None` guard + regression step; R6 text-stays assertion) ✅
- C4 phase labels → Task 1 ✅
- C5 queue placeholder → Task 2 ✅

**2. Placeholder scan:** No TBD/TODO; every code step shows actual code; commands have expected output. ✅

**3. Type consistency:** `_flush_stream`, `_ensure_stream_timer`, `_stop_stream_timer`, `_stream_dirty`, `_stream_timer` named identically across Task 4 steps and referenced consistently in `_end_stream`/`_reset_conversation`. `action_cancel` spied (not renamed) in Task 3. `activity_label` strings ("Classifying…", "Responding") match between Task 1 impl and test. ✅

**Note for implementer:** Task 1 step 3 says to read state.py:194-209 before adding the `RESPONDING` label — verify the existing chunk branch rather than assume. Task 4 is the risk concentration; run step 7 (full TUI suite) before committing — a green there is the proof the `_boundary_after` routing is untouched.
