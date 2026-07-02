# Cancelled-Turn Stream-Misroute Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the TUI's stream painter the moment a turn is cancelled, so a
cancelled turn's stale stream state can no longer race the next turn's
boundary logic and misroute prose into the wrong transcript widget.

**Architecture:** One-line behavioral fix in `harness/tui/app.py`'s
`action_cancel`: call `self._painter.end()` (already exists, already used by
every other turn-ending path) immediately when the cancel is posted, guarded
by the existing `_cancel_posted` de-dupe flag so it fires exactly once per
cancel regardless of the double-ESC-binding-fire quirk documented at that
call site.

**Tech Stack:** Python 3.11, Textual (TUI framework), pytest + Textual's
`run_test()` pilot harness for headless widget-tree assertions.

## Global Constraints

- Test command (run from the worktree root, per project CLAUDE.md):
  `.venv/bin/python -m pytest tests/ -q`
- One source file touched: `harness/tui/app.py`. No changes to
  `harness/tui/stream_painter.py` — `end()` already exists and does exactly
  what's needed.
- No change to `_add_user_message`, `_write_meta`, or the `task_classified`
  boundary logic from PR #81 — this closes a gap in the cancellation path
  only.
- Preserve the existing late-delta-extends-in-place invariant (PR #81's
  protected test) and the double-ESC de-dupe invariant (existing
  `test_esc_cancels_turn_even_when_rail_open`-style coverage) — both must
  stay green, unmodified.

---

### Task 1: Regression test proving the cancelled-turn misroute

**Files:**
- Modify (test only): `tests/test_tui_pilot.py` (add one test function near
  the existing misroute regression tests, e.g. after
  `test_new_turn_prose_opens_fresh_block_after_classify_chip`, which currently
  ends at line 469).

**Interfaces:**
- Consumes: `HarnessTui` (`harness/tui/app.py`), `SessionUpdate`
  (`harness/tui/messages.py`), `update_agent_message_text` /
  `message_chunk` / `with_meta` (from `acp` / `harness.acp_emit`, already
  imported elsewhere in this test file), `_md_source` helper (already
  defined at `tests/test_tui_pilot.py:45`), `FAKE_CMD` / `REPO` module
  constants (already defined at lines 20-23).
- Produces: nothing consumed by later tasks — this is the failing test Task
  2 makes pass.

This test drives a `ControlledConn` (same pattern as the existing
`test_late_prior_turn_delta_...` test at line ~380) whose `prompt()` blocks
until released, so we can inject a trailing delta for the cancelled turn
*after* the next turn has already begun — reproducing the exact race
described in the design doc
(`docs/superpowers/specs/2026-07-01-cancel-stream-misroute-design.md`).

- [ ] **Step 1: Write the failing test**

```python
def test_cancelled_turn_stream_closed_immediately_on_esc():
    """Regression: action_cancel must close the stream painter the moment
    cancel is posted, not leave it open until prompt() unwinds. Before the
    fix, a trailing delta for the cancelled turn that arrives after the next
    turn has already begun (_add_user_message already ran) has no reliable
    boundary signal — it can misroute into the wrong widget, the same defect
    class as PR #81/#138 via the one path neither covered: cancellation."""
    from harness.acp_emit import with_meta, message_chunk

    def _classify_chip(task_type="chat_question"):
        meta = {"task_type": task_type, "skills": [], "confidence": 0.99}
        return with_meta(message_chunk(""), {"task_classified": meta})

    class ControlledConn:
        def __init__(self):
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.cancelled = False

        async def prompt(self, **kwargs):
            self.started.set()
            await self.release.wait()
            return NS(stop_reason="cancelled" if self.cancelled else "end_turn")

        async def cancel(self, **kwargs):
            self.cancelled = True

    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await app._enter_conversation()
            conn = ControlledConn()
            app._conn = conn
            app._session_id = "fake-session"

            # turn 1: prompt, prose, footer — establishes a KEPT prior widget.
            app._add_user_message("first")
            app.on_session_update(SessionUpdate(update_agent_message_text("first answer")))
            await pilot.pause()
            app._write_meta(0.1)

            # turn 2: prompt, partial prose, then the user cancels mid-stream.
            app._add_user_message("second")
            app._turn_start = time.monotonic()
            task = asyncio.create_task(app._send_prompt("second"))
            await conn.started.wait()
            await pilot.pause()
            app.on_session_update(SessionUpdate(update_agent_message_text("second partial")))
            await pilot.pause()

            await app.action_cancel()
            await pilot.pause()

            # turn 2 actually ends now (prompt() unblocks with stop_reason=cancelled).
            conn.release.set()
            await task
            await pilot.pause()

            # turn 3 begins immediately — mirrors the live repro (screenshot).
            app._add_user_message("third")
            app.on_session_update(SessionUpdate(_classify_chip()))
            await pilot.pause()

            # a STRAGGLER delta for the cancelled turn 2 arrives late (cooperative
            # cancellation: the agent subprocess isn't guaranteed to have stopped
            # producing the instant cancel() was called).
            app.on_session_update(SessionUpdate(update_agent_message_text(" late-straggler")))
            await pilot.pause()

            # turn 3's own prose follows.
            app.on_session_update(SessionUpdate(update_agent_message_text("third answer")))
            await pilot.pause()

            scroll = app.query_one("#transcript", VerticalScroll)
            md_sources = [_md_source(md) for md in scroll.query(Markdown)]

        # turn 2's straggler must extend TURN 2's (already-closed) widget in
        # place — it must NOT land in turn 3's fresh widget, and turn 3's
        # widget must NOT be merged with turn 2's.
        assert md_sources == ["first answer", "second partial late-straggler", "third answer"], (
            "cancelled-turn straggler misrouted: expected the straggler to extend "
            f"turn 2's own (closed) widget in place, got {md_sources!r}"
        )

    asyncio.run(go())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tui_pilot.py::test_cancelled_turn_stream_closed_immediately_on_esc -v`

Expected: FAIL. Before the fix, `action_cancel` never closes the painter, so
the straggler delta's routing is undefined by design — it will either merge
into turn 3's widget (producing `["first answer", "third answersecond partial late-straggler"]`
or similar) or otherwise fail to produce three cleanly separated sources.
Confirm the assertion fails (not an unrelated error/exception) before moving
on.

- [ ] **Step 3: Commit the failing test**

```bash
git add tests/test_tui_pilot.py
git commit -m "test(tui): reproduce cancelled-turn stream misroute (fails pre-fix)"
```

---

### Task 2: Close the stream painter on cancel

**Files:**
- Modify: `harness/tui/app.py:1564-1587` (`action_cancel`).
- Test: `tests/test_tui_pilot.py` (Task 1's test turns green; no new test
  file).

**Interfaces:**
- Consumes: `self._painter.end()` — already defined at
  `harness/tui/stream_painter.py:110-127`, no signature change, called with
  no arguments (default `boundary=False`, matching the "turn ended" close
  semantics used by `_add_user_message`).
- Produces: nothing new consumed by later tasks — this is the final
  behavioral change in this plan.

- [ ] **Step 1: Read the current `action_cancel` body**

Current code (`harness/tui/app.py:1564-1587`):

```python
    async def action_cancel(self) -> None:
        # Gate the ENTIRE body on _cancel_posted: on_key calls action_cancel
        # directly AND Textual's global ("escape","cancel") binding also fires
        # because event.stop() does not suppress binding dispatch.  One ESC press
        # therefore triggers two invocations — gating here means the second is a
        # complete no-op (no extra cancel() RPC, no extra tx.cancel trace, no
        # extra feedback line).  _cancel_posted is reset to False at the top of
        # each new turn in _submit_text, so ESC works again on the next turn.
        if self._cancel_posted:
            return
        if self._conn is not None and self._session_id is not None:
            self._cancel_posted = True
            if self._tracer is not None:
                self._tracer.emit("dn", "tx.cancel", sid=self._session_id)
            # Immediate feedback: stop the spinner NOW rather than waiting for the
            # turn to wind down and prompt() to return. The backend cancel is now
            # prompt (run_interruptible + cooperative gates), but there is still a
            # short teardown window; killing the spinner here acknowledges the ESC
            # at once. _hide_working is idempotent, and _send_prompt's finally
            # calls it again harmlessly on turn-end.
            if self._started:
                self._hide_working()
                self._append_line(_c("muted", "— canceling… —"))
            await self._conn.cancel(session_id=self._session_id)
```

- [ ] **Step 2: Add the painter close, guarded by the same `_cancel_posted` gate**

Edit `harness/tui/app.py` — insert `self._painter.end()` right after
`self._cancel_posted = True` (so it runs exactly once per cancel, same
guarantee as the trace emit and the "— canceling… —" line). This is an
insertion only — every other line in `action_cancel` (the trace emit, the
spinner/"— canceling… —" feedback, and the `await self._conn.cancel(...)`
call) is unchanged and must stay:

```python
        if self._conn is not None and self._session_id is not None:
            self._cancel_posted = True
            # Close the stream the instant cancel is posted — do not wait for
            # prompt() to unwind. Every other turn-ending path (a new user turn
            # via _add_user_message, an in-turn tool/thought boundary, the
            # task_classified chip from PR #81) explicitly closes the painter;
            # cancellation was the one path that didn't, leaving _stream_closed
            # False and _streaming_md pointing at a mid-answer widget until
            # _add_user_message ran on the NEXT turn — late enough that a
            # straggler delta for the cancelled turn could race the next turn's
            # boundary logic and misroute (see
            # docs/superpowers/specs/2026-07-01-cancel-stream-misroute-design.md).
            # end() (not reset()) keeps the widget reference, so a genuine
            # straggler still extends THIS (now-closed) widget in place.
            self._painter.end()
            if self._tracer is not None:
                self._tracer.emit("dn", "tx.cancel", sid=self._session_id)
            # Immediate feedback: stop the spinner NOW rather than waiting for the
            # turn to wind down and prompt() to return. The backend cancel is now
            # prompt (run_interruptible + cooperative gates), but there is still a
            # short teardown window; killing the spinner here acknowledges the ESC
            # at once. _hide_working is idempotent, and _send_prompt's finally
            # calls it again harmlessly on turn-end.
            if self._started:
                self._hide_working()
                self._append_line(_c("muted", "— canceling… —"))
            await self._conn.cancel(session_id=self._session_id)
```

- [ ] **Step 3: Run the new regression test**

Run: `.venv/bin/python -m pytest tests/test_tui_pilot.py::test_cancelled_turn_stream_closed_immediately_on_esc -v`

Expected: PASS.

- [ ] **Step 4: Run the full test suite to confirm no regressions**

Run: `.venv/bin/python -m pytest tests/ -q`

Expected: all tests pass, including the pre-existing PR #81 protected tests
(`test_late_prior_turn_delta_...`, `test_new_turn_prose_opens_fresh_block_after_classify_chip`)
and the double-ESC de-dupe test (`test_esc_cancels_turn_even_when_rail_open`).
If any of those regress, STOP — do not add a second fix on top; re-open
Phase 1 root-cause investigation (per `superpowers:systematic-debugging`)
rather than layering patches.

- [ ] **Step 5: Commit**

```bash
git add harness/tui/app.py
git commit -m "fix(tui): close stream painter immediately on cancel

action_cancel never closed the stream painter, unlike every other
turn-ending path (_add_user_message, in-turn tool/thought boundaries, the
task_classified chip from #81). A cancelled turn's stream stayed open until
_add_user_message ran on the NEXT turn, late enough that a trailing delta
for the cancelled turn could race the next turn's boundary logic and
misroute — the same defect class as #81/#138 via a path neither covered.

Fixes the cancelled-turn variant of the stream-misroute bug."
```

---

## Verification

After both tasks are committed, do a final full-suite run from the worktree
root (per project CLAUDE.md) before moving to
`superpowers:finishing-a-development-branch`:

```bash
cd /Users/alberto/Work/Quiubo/harness/.worktrees/fix-cancel-stream-misroute
.venv/bin/python -m pytest tests/ -q
```

Expected: all tests green, including the new regression test and every
pre-existing misroute/cancel test.
