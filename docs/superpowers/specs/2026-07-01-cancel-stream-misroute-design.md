# Fix: cancelled turn's stream never closed, reopening the #81 misroute (TUI)

**Date:** 2026-07-01
**Branch:** fix-cancel-stream-misroute
**File touched:** `harness/tui/app.py` (+ tests)

## Symptom

Reproduced live (screenshot): user cancels turn 2 mid-answer (ESC → `— turn
ended: cancelled —`), then immediately sends turn 3. Turn 2's (or a fragment
of turn 3's) prose renders **above** turn 1's block instead of under its own
prompt — the same visual defect PR #81 ("stream misroute") and #138
("footer-above-answer") already fixed, but via a trigger neither covered.

## Root cause (validated against live code)

PR #81 made every *normal* turn-ending path close the stream painter before
the next turn can begin:

- `_add_user_message` (`app.py:1198`) → `self._painter.end()` +
  `clear_boundary()` on every new user turn.
- Tool calls / thoughts / `stream_reset` → `_end_stream(boundary=True)`
  in-turn.
- A turn's own `task_classified` chip → `_end_stream(boundary=True)`
  (`app.py:1464-1465`), the #81 fix itself.

**Cancellation skips all of this.** `action_cancel` (`app.py:1564-1587`) posts
`self._conn.cancel(...)` and appends a muted "— canceling… —" line, but never
touches `self._painter`. The turn actually ends later, when the awaited
`self._conn.prompt()` call in `_send_prompt` (`app.py:1241`) returns with
`stop_reason="cancelled"` — at which point `_write_meta` mounts the footer
(`app.py:1245`) **unconditionally**, regardless of whether the painter's
stream is still open (`_stream_closed=False`, `_streaming_md` still the live,
mid-answer widget).

Sequence from the screenshot:

1. Turn 2 streams prose normally → `_streaming_md` = turn 2's widget, open.
2. ESC → `action_cancel` sends the cancel RPC. Painter untouched: still open.
3. `_conn.prompt()` unblocks with `stop_reason="cancelled"`; `_write_meta`
   mounts turn 2's footer immediately, **while the stream is still open**.
   The "turn ended: cancelled" line follows.
4. Turn 3 begins. `_add_user_message` runs `_painter.end()` (closes turn 2's
   widget **only now** — footer and cancel-line already mounted after it)
   then `clear_boundary()`.
5. Because the agent subprocess for turn 2 is not guaranteed to have stopped
   producing at the moment of cancel (cancellation is cooperative,
   `esc-cancel-cleanup-pr254`), a trailing delta for the dead turn 2 can
   still arrive after step 4. It hits `StreamPainter.delta()` with
   `_streaming_md` still referencing turn 2's widget and no boundary signal
   distinguishing "stale cancelled-turn straggler" from "genuine late delta
   of a normally-completed turn" — the exact ambiguity #81 solved for the
   normal case, unhandled here.
6. Depending on exact interleaving with turn 3's own `task_classified` chip,
   the straggler either re-extends turn 2's (already footer'd, already
   positionally stale) widget in place — visually stranding turn 2's tail
   above turn 3 — or turn 3's own first delta gets misrouted the same way
   turn N's did in #81, because the painter's bookkeeping was never reset
   for the cancellation that just happened.

The common thread with #81: `_streaming_md` is a single app-lifetime
reference (`StreamPainter` is instantiated once in `App.__init__`, never
per-turn), and every dispatch path that can end a turn must positively close
it. Cancellation is a turn-ending path. It was missed.

## Fix — close the stream the moment cancel is posted

In `action_cancel` (`app.py:1564`), call `self._painter.end()` immediately
when the cancel is posted (guarded by the same `self._cancel_posted` check
that already de-dupes the double-ESC-binding-fire), **before** awaiting
`self._conn.cancel(...)`.

This mirrors the existing pattern exactly — `end()` (not `reset()`) keeps the
widget reference, so if a genuine trailing delta for the cancelled turn
straggles in afterward, it still extends the correct (now-closed) widget in
place rather than being orphaned. What changes is *when* the close happens:
immediately on ESC, not "whenever `prompt()` happens to unblock" — so the
footer mount in `_send_prompt` and the next turn's `_add_user_message` /
`task_classified` boundary logic always see accurate, already-closed painter
state, instead of racing a stream that's still technically open.

No new state, no reset-vs-close semantic change, one call site.

### Resulting routing

| Scenario | Old | New |
|---|---|---|
| Cancel mid-stream, footer mounts while stream still open | painter closed late (in next turn's `_add_user_message`), stale delta ambiguous | painter closed at ESC time; footer mounts onto an already-closed stream |
| Trailing delta for the cancelled turn arrives after ESC | may re-extend/misroute per the race in Root Cause | extends the now-closed widget in place (same as any ordinary late delta — the protected #81 invariant) |
| Next turn's first delta | may misroute if a cancelled-turn straggler confuses the boundary | opens fresh, same as any normal turn (`_add_user_message` + `task_classified` boundary unaffected — this fix doesn't touch either) |

### Invariant preserved

The late-delta-extends-in-place behavior for a genuinely lagging delta is
unchanged: closing early via `end()` (not `reset()`) keeps `_streaming_md` set,
so that code path is identical to today — only the *timing* of the close
moves earlier.

## Testing (TDD)

Following the existing TUI pilot-test pattern (`tests/test_tui_pilot.py`),
same style as the #81 regression test.

1. **Failing regression test (the bug):** drive prompt₁ → prose₁ → footer₁ →
   prompt₂ → prose₂ (partial) → `action_cancel` → footer₂ (cancelled) →
   prompt₃ → chip₃ → a stray trailing delta for turn 2 → prose₃. Assert:
   turn 3's prose lands in its own fresh widget, and the stray turn-2
   straggler extends turn 2's widget (not turn 3's, not a new stray block).
2. **Protected-behavior test (must stay green):** the existing #81 regression
   test (`test_late_prior_turn_delta_*` and the turn-2-misroute test) —
   unaffected, since this fix only changes the cancel path.
3. **Double-ESC de-dupe test (must stay green):** existing
   `test_esc_cancels_turn_even_when_rail_open`-style coverage — confirm
   `self._painter.end()` is called at most once per cancel (guarded by
   `_cancel_posted`, same as the existing RPC/trace-emit guard), not once per
   raw keypress.

Run from the worktree root:
`.venv/bin/python -m pytest tests/ -q`

## Scope / non-goals

- One source file: `harness/tui/app.py`, one added `self._painter.end()`
  call in `action_cancel`, guarded by the existing `_cancel_posted` check.
- No change to `StreamPainter` itself (`stream_painter.py`) — `end()` already
  exists and does exactly what's needed.
- No change to `_add_user_message`, `_write_meta`, or the `task_classified`
  boundary logic from #81 — this closes a gap in the *cancellation* path
  only.
- Not addressing general cancellation-race hardening beyond this one gap
  (see `esc-cancel-cleanup-pr254` for the broader cancel-cleanup work,
  already merged); this is scoped to the stream-painter state specifically.
