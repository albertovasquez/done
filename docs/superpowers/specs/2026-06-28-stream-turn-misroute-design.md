# Fix: agent prose misrouted into the previous turn's widget (TUI)

**Date:** 2026-06-28
**Branch:** worktree-fix-stream-turn-misroute
**File touched:** `harness/tui/app.py` (+ tests)

## Symptom

In the TUI, from the **second** user turn onward, the agent's prose answer does
not appear under its own prompt. The transcript shows the prompt, the
`[classified: …]` chip, and the completion footer (`▣ … · gpt-5.4 · 8.6s`), but
the answer body is missing — and the *previous* turn's answer appears to cover
both questions. Visually the answer reads as being "above" the question.

## Root cause (validated against live code)

`HarnessTui._stream_message` (`harness/tui/app.py`) routes each agent message
delta into one of three cases. The dangerous one is the **late-delta** branch
(today at `app.py:868-872`):

```python
if self._stream_closed and self._streaming_md is not None \
        and not prior_is_last and not boundary_after:
    pass   # extend the PRIOR answer's widget in place
```

The streaming widget reference `self._streaming_md` is intentionally **kept (not
nulled)** at turn end (`_end_stream`), so a delta that lags after `prompt()`
returns can extend the just-finished answer in place rather than spawning a stray
block under the next prompt.

The branch decides "new answer vs. late delta" from `_stream_closed`,
`_boundary_after`, and `prior_is_last` (is the kept widget still the last child
of the transcript). None of these distinguishes **a new user turn** from **a
lagging delta of the prior turn**.

On the first delta of turn N's answer (N ≥ 2):

| Condition | Value | Why |
|---|---|---|
| `_stream_closed` | True | `_add_user_message` → `_end_stream()` |
| `_streaming_md is not None` | True | reference kept by design |
| `not boundary_after` | True | `_add_user_message` clears `_boundary_after` |
| `not prior_is_last` | **True** | the prior turn's footer, this turn's prompt, and the classification chip were all appended after the prior Markdown widget, so it is no longer last |

→ the late-delta branch fires → **turn N's prose is appended to turn N-1's
widget** (which sits up under the earlier prompt). That is the bug.

Turn 1 is unaffected: there is no prior `_streaming_md`, so the `elif`
(`_streaming_md is None`) opens a fresh widget correctly.

The core flaw: position (`prior_is_last`) + `_boundary_after` cannot tell "a new
answer whose prior widget is no longer last" apart from "a lagging delta of the
just-closed answer." On a new user turn both collapse to the same state.

## Fix — treat the classification chip as a turn boundary

> **Design pivot (validated empirically — see below).** The initial design was a
> per-turn widget stamp bumped in `_add_user_message`. A probe test proved this
> **breaks the protected invariant**: `test_late_prior_turn_delta_…` delivers the
> turn-1 late delta *after* `_add_user_message("second")`, so a prompt-keyed stamp
> would force that late delta into a fresh widget — exactly the behaviour the test
> forbids. The prompt is therefore NOT the right boundary signal.

The signal that genuinely separates the two cases is **whether the next turn has
begun producing output** — and on every dispatch path the agent emits a
`task_classified` chip (`acp_agent.py:299`, an empty-body `message_chunk` carrying
`field_meta["harness"]["task_classified"]`) *before* that turn's first prose. A
lagging delta of the prior turn carries no such chip.

Probe result on unmodified code (`tests/test_probe_misroute.py`):
- bug sequence (chip₂ before prose₂) → `['first answersecond answer']` (prose
  misrouted into turn-1 widget) — **reproduces the screenshot**;
- protected sequence (no chip, just a late delta) → `['first complete late']`
  (extends in place) — **already correct**.

So: **when a `task_classified` chip is seen, close the current block as an in-turn
boundary** (`self._end_stream(boundary=True)`), mirroring the existing
`stream_reset` handling (`app.py:913-915`). This sets `_boundary_after=True`, so
the next prose's `boundary_after` check (`app.py:866`) is true → the late-delta
branch is skipped → a fresh widget opens. The chip update has an empty body, so it
renders only the chip line and never itself opens a widget.

### Implementation (one site, `harness/tui/app.py`)

In `on_session_update`, where the `task_classified` chip is detected, call
`self._end_stream(boundary=True)`. Concretely: read `meta["harness"]["task_classified"]`
(the meta dict is already extracted for `stream_reset` at `app.py:912`) and, when
present, set the boundary before the chip is appended. No new state, no counter, no
change to `_stream_message` or `_add_user_message`.

### Resulting routing for the first delta of turn N's answer

| State | Old | New |
|---|---|---|
| Same turn, closed, not last (true late delta — no chip) | extend in place ✓ | `_boundary_after` stays clear → extend in place ✓ (unchanged) |
| New turn, closed, chip seen first (**the bug**) | extend in place ✗ | chip sets `_boundary_after` → **fresh widget ✓** |
| In-turn new step (tool/thought/stream_reset) | fresh widget ✓ | unchanged (same boundary mechanism) |

### Invariant preserved

The late-delta-extends-in-place behavior (a lagging delta after `prompt()`
returns within the same turn) is preserved: such a delta arrives with **no
`task_classified` chip**, so `_boundary_after` is never set and the delta still
extends the prior widget in place. Only prose **preceded by a fresh
classification** (a genuinely new turn) is forced into a new widget.

### Known coupling (documented, acceptable)

This couples stream routing to the `task_classified` chip firing before a turn's
prose. That holds for every current dispatch path: `task_classified` is emitted
unconditionally (`acp_agent.py:297-300`) before the chat/agent branch. If a future
path ever streams prose with no preceding `task_classified`, the boundary would not
fire and the bug could return — call this out in a code comment at the fix site.

## Testing (TDD)

Driven against `_stream_message` / the relevant TUI seam, following the existing
TUI pilot-test pattern.

1. **Failing regression test (the bug):** drive the sequence
   prompt₁ → prose₁ → footer₁ → prompt₂ → chip₂ → prose₂, then assert turn 2's
   prose lands in a **new** Markdown widget (a distinct child appended at/after
   prompt 2), NOT appended to turn 1's widget.
2. **Protected-behavior test (must stay green):** a same-turn late delta (closed
   stream, prior widget no longer last, no new prompt) still **extends in place**.
3. **In-turn boundary test (must stay green):** after a tool/thought boundary,
   the next prose opens a fresh widget (existing behavior unchanged).

Run from the worktree root:
`.venv/bin/python -m pytest tests/ -q`
(run with the worktree as cwd to avoid editable-install shadowing).

## Scope / non-goals

- One source file: `harness/tui/app.py` (one added boundary call in
  `on_session_update` + a comment). No new state; `_stream_message` and
  `_add_user_message` untouched.
- No changes to `render.py` or the ACP path.
- Not addressing the unrelated `mount()`-not-awaited observation (the author
  already handles it via `call_after_refresh`; it is not the cause here).
