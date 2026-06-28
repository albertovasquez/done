# TUI turn spacing & visual hierarchy — design spec

**Date:** 2026-06-28
**Status:** Approved (brainstorming) — ready for implementation plan
**Worktree/branch:** `worktree-tui-turn-spacing`

## Problem

In the TUI transcript, a single conversation turn renders three kinds of content
with **no margin or visual separation**:

1. The user's prompt (`▌ …` accent-bordered block — already styled).
2. The router classification chip: `[classified: chat_question · skills: clarify-before-acting · conf: 0.96]`.
3. The run status line `▣ Build bypass on · claude-opus-4-8 · 4.3s` plus the agent's response markdown.

The chip and the `Build …` status line are bare, marginless `Static` widgets, so
the turn reads as one undifferentiated wall of text. There is no visual cue for
where the prompt ends, what is metadata, and where the answer begins.

## Reference: OpenCode (Go/Lipgloss TUI, v0.1.140)

Researched faithfully. Relevant lessons borrowed:

- **Role = a single colored side-border + whitespace**, never full-width
  background stripes or horizontal-rule dividers.
- **Separation = one blank line between blocks** plus a small internal padding
  rhythm (1 row vertical / 2 cols horizontal). No `─────` rules between turns.
- **Metadata = a dimmed caption that rides *with* its block** (model + time), in
  the muted foreground color, separated only by a newline.

We adapt these, not copy them: OpenCode renders metadata as a *footer* under the
message; we place the run metadata as a *header* above the response (decision
below), because our two metadata lines describe different things and we want each
to sit immediately before/with the thing it describes.

## Decisions (locked in brainstorming)

| Decision | Choice |
|---|---|
| Where metadata lives | **Split**: the classification chip rides under the **prompt** (it describes how the prompt was routed); the `Build · model · time` line rides with the **response** (it describes the run). |
| Response visual treatment | **Border on prompt only.** The prompt stays the single bordered anchor (accent left-border, already exists). The response is borderless markdown, separated by whitespace + a dimmed metadata caption above it. No second card. |
| `Build · model · time` placement | **Header** — above the response (not a footer). Requires a placeholder-then-patch in the streaming path (see Implementation). |
| Turn separation | **Whitespace only** — a blank line + the prompt's accent border as the turn anchor. No horizontal-rule dividers. |
| Spacing scale | **Tune inline values** in `app.tcss`. No new named-token abstraction; the existing `0/1/2` vocabulary is the de-facto scale. |

## Target structure (per turn)

```
▌ My prompt text                        ← .user-msg card (accent left-border) — EXISTS, unchanged
  classified: chat · skills · conf 0.96  ← .turn-meta caption (muted, indent 2) — hugs the prompt
                                          ← blank line = the turn break (margin-top on the run caption)
  ▣ Build bypass · opus · 4.3s           ← .turn-meta-run caption (muted, indent 2) — HEADS the response
  Agent response markdown…                ← borderless, indent 2 to align under its caption
```

Two caption classes, because the chip and the run line want different top
spacing: `.turn-meta` (chip) hugs the prompt above it; `.turn-meta-run` (Build)
carries the `margin-top: 1` turn break so it heads the response group below.

Next turn repeats the same shape. Separation between turns is the blank line
above the next `▌` prompt plus the accent border itself.

## Components touched

All changes are in two files: `harness/tui/app.py` and `harness/tui/app.tcss`.
The chip text source (`harness/tui/render.py::harness_chips`) is unchanged.

### 1. `.turn-meta` CSS class (`app.tcss`)

New class for the two metadata captions (chip + Build line):

```css
.turn-meta { color: $muted; margin: 0 0 0 2; height: auto; }
```

- `color: $muted` — dimmed caption (chip already uses muted markup; this makes
  the Build line match and removes reliance on inline color only).
- `margin: 0 0 0 2` — left-indent 2 cells so captions sit under, and slightly
  inset from, the prompt/response. No top/bottom margin (the blank-line break
  comes from the response group's top margin, below).

### 2. `_append_line` gains an optional CSS class (`app.py`)

Today (`app.py:792`):

```python
def _append_line(self, markup: str) -> None:
    self._append(Static(markup, markup=True))
```

Add an optional `classes` parameter so the chip and Build lines can carry
`.turn-meta` without affecting any other caller (default `None` = byte-identical
behaviour for the ~8 existing call sites):

```python
def _append_line(self, markup: str, *, classes: str | None = None) -> None:
    self._append(Static(markup, markup=True, classes=classes))
```

### 3. Chip rides with the prompt, styled (`app.py:993`)

Change the chip emission to pass the class:

```python
for chip in harness_chips(getattr(msg.update, "field_meta", None)):
    self._append_line(_c("muted", f"\\[{chip}]"), classes="turn-meta")
```

### 4. `Build · model · time` becomes a header caption above the response

This is the only non-trivial change. The constraint: elapsed time is only known
at **turn end** (`_send_prompt` computes `elapsed` at `:854` *after* `prompt()`
returns), but we want the line to render **above** the response, which streams
*during* `prompt()`. Resolution — **placeholder then patch**:

- **Emit a placeholder** `▣ Build … · model · …s` caption widget at the moment
  the **first response delta opens its block** — i.e. in `_stream_message`'s
  "new answer" branch, immediately before the new `Markdown` widget is mounted.
  Keep a reference to it on `self._meta_widget` (alongside the existing markdown
  widget reference). The placeholder shows the model label and an em-dash / `…`
  in place of the not-yet-known elapsed time.
- **Patch on turn end.** Replace today's `_write_meta` *append* (`:855`) with a
  call that **updates the existing placeholder widget's text** to the final
  `· {elapsed:.1f}s`. If no placeholder exists (turn produced no message — e.g. a
  pure tool turn or an error before any delta), fall back to appending the line
  as before so metadata is never silently lost.
- The placeholder gets `classes="turn-meta"`. The response markdown widget keeps
  its existing class; CSS change #5 adds its indent and the blank-line gap.

Sequencing guard: the placeholder must be created **only** in the genuine
"new answer opens a block" path of `_stream_message`, NOT in the late-delta /
in-place-extend path (PR #81 / `stream-misroute-fixed`). A new STEP within a turn
(after a tool call/thought, `_boundary_after=True`) opens a fresh markdown block
but is the *same* run — it must **not** emit a second Build caption. So: emit the
placeholder only on the **first** answer block of a turn (track with a per-turn
`self._meta_emitted` flag reset in `_add_user_message`).

### 5. Response indent + turn-break gap (`app.tcss`)

Today (`app.tcss:55`):

```css
#transcript Markdown { height: auto; margin: 0 0 1 0; padding: 0; background: $background; }
```

Change to indent the response under its caption and add the top-gap that creates
the blank-line turn break after the chip:

```css
#transcript Markdown { height: auto; margin: 1 0 1 2; padding: 0; background: $background; }
```

- `margin-top: 1` — the blank line separating the prompt+chip group from the
  response+Build group (the visual turn break).
- `margin-left: 2` — align the response prose under the Build caption.
- `margin-bottom: 1` — unchanged; spaces this turn from the next prompt.

The Build caption itself sits in the gap created by the markdown's `margin-top`
because the caption is a separate `Static` mounted just above the markdown; its
own `.turn-meta` has no top margin, so it hugs the response (caption + response
read as one group), while the markdown's `margin-top: 1` pushes the whole group
down off the chip.

## Data flow

```
on_session_update(msg)
  ├─ harness_chips(field_meta) → _append_line(chip, classes="turn-meta")   # under prompt
  └─ render_update → item.kind == "message" → _stream_message(text)
        └─ [first answer block of turn] mount Build placeholder (.turn-meta) then Markdown
_send_prompt: prompt() returns
  └─ elapsed computed → _write_meta(elapsed)  # PATCHES the placeholder, or appends if none
```

## Error handling / edge cases

- **No message in the turn** (pure tool turn, or error before first delta): no
  placeholder was emitted → `_write_meta` falls back to appending the Build line
  as today. Metadata is never lost.
- **Multi-step turn** (tool call → second answer block): only the first answer
  block emits the Build caption (`_meta_emitted` flag); later blocks are styled
  response markdown with no extra caption.
- **Late delta of a just-closed answer**: extends its existing markdown widget in
  place (unchanged PR #81 behaviour); no new caption, no new gap.
- **Turn ended non-`end_turn`**: the existing `— turn ended: … —` muted line
  still appends after the patch; unaffected.
- **Persona switch / transcript clear**: `_meta_widget`/`_meta_emitted` reset on
  the same path that clears the transcript and on each new user message.

## Testing

TDD — write these first:

1. **Ordering test** (pilot-style, drives a real session update sequence): assert
   the emitted transcript widgets for one turn are, in order:
   `user (▌) → chip Static(.turn-meta) → Build Static(.turn-meta) → Markdown`.
2. **Build-is-header, not footer**: assert the Build `.turn-meta` widget is
   mounted *before* the response `Markdown` widget in `#transcript`'s child order.
3. **Elapsed patched**: after turn end, the Build caption text contains the final
   `· N.Ns` elapsed, not the placeholder `…`.
4. **Fallback append**: a turn that produces a tool item but no message still
   renders a Build line (appended).
5. **Multi-step turn**: a turn with `tool` between two message blocks emits
   exactly **one** Build caption.
6. **CSS smoke**: `.turn-meta` resolves muted color + left margin 2; the
   `#transcript Markdown` rule has `margin-top: 1` and `margin-left: 2`.
7. **Regression guard**: the protected late-delta / stream-boundary tests
   (`test_pilot_streams_deltas_into_one_markdown_widget`, the PR #81 misroute
   tests) stay green — the placeholder must not perturb the late-delta branch.

Run from the worktree root: `.venv/bin/python -m pytest tests/ -q`.

## Out of scope (YAGNI)

- No named spacing-token scale / design-token refactor (decided: inline values).
- No horizontal-rule dividers (decided: whitespace only).
- No second bordered card for the response (decided: border on prompt only).
- No right-alignment of the prompt (decided against; costs horizontal space).
- The landing-screen `compose-meta` "Build · model · provider" line is a
  different surface and is untouched.

## Verification

- Typecheck + full `pytest tests/ -q` green from the worktree.
- Visual check: launch the TUI (or render a snapshot via `app.save_screenshot`)
  and confirm the three-element turn reads as prompt / dimmed chip / gap / dimmed
  Build caption / response — per `model-display-vs-agent-split` and the
  `prominent-agents-drawer` lesson that green tests do not prove layout; verify
  placement visually.
