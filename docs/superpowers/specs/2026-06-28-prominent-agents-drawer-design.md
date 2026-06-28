# Prominent AGENTS drawer — design

**Status:** design / spec (no implementation). Hand-off to writing-plans.
**Date:** 2026-06-28
**Author:** Alberto Vasquez (with Claude Opus 4.8)
**Scope:** Redesign the persona/agents drawer (the C2b AgentRail) into a prominent
card list per the provided mockup, pre-highlight the active persona on open, and
add a QUICK KEYS legend panel. Built ON the existing Textual client and design
system (`components.md`); no new tokens.

**Decisions baked in (from brainstorming):**
- **Per-agent status is truthful-static:** the ACTIVE card shows its real live
  `AgentState` + real task count; non-active cards show `IDLE` / `idle`. No
  fabricated telemetry. Upgrades for free when a per-seat fleet runtime exists.
- **No icon tile.** The card is name + sub-line (left) and StatusChip + StateDot
  (right). (User explicitly dropped the bordered `≡` mini-box.)
- **Navigate vs. commit:** `↑↓` only highlights; `enter` commits the switch and
  closes the drawer; `esc` closes with no change; `enter` on the already-active
  persona is a no-op switch that still closes.
- **Active pre-highlighted on open.**
- Include BOTH panels: the AGENTS cards AND the QUICK KEYS legend.

**Hard constraint:** zero `upstream/` edits (AGENTS.md #4); work in a git
worktree (AGENTS.md #1). File:line refs verified against the worktree at authoring
time — re-verify before acting (AGENTS.md #6).

---

## 0. Current state vs. the gaps (code-grounded)

Verified in the worktree:

- **Rail is a one-liner today.** `AgentRail` (`harness/tui/widgets/agent_rail.py`)
  renders each persona as `_row_label(r) -> "● name"` (line 33) in a `ListItem`
  with a `Label`. CSS: `#agent-rail { dock: right; width: 28; ... }`
  (`app.tcss:147`). The mockup wants a boxed CARD per agent.
- **Navigate-vs-commit is ALREADY correct.** `_on_selected` listens to
  `ListView.Selected` (agent_rail.py:65), which Textual posts only on `enter`/click
  (`action_select_cursor`), NOT on cursor move (`Highlighted`). Nothing in
  `app.py` listens to `Highlighted`. So `↑↓` already only highlights. **This is
  NOT a gap — preserve it.**
- **Active is NOT pre-highlighted on open.** `set_rows` clears + appends; the
  ListView highlight defaults to index 0 (agent_rail.py:46-52). Opening the rail
  (`app.py:579` on tab, `:1036` on toggle) does not set `.index` to the active
  row. **This IS the gap.**
- **No QUICK KEYS panel exists** — only a one-line statusbar hint
  (`"tab agents · ctrl+p commands"`, app.py:157). The mockup adds a legend panel.
- **Design system already has the atoms:** `StatusChip(label, color_token)` and
  `StateDot(state)` (`status_chip.py`), `state_color_token(state)`
  (status_chip.py:54), the glyph map + `RUNNING/IDLE/SCHEDULED` status tokens with
  the right colors (`tokens.py`, `theme.py`). `components.md` already specs the
  target ("AgentRail row = StateDot + name + status-word + sub-line") and tags the
  live per-row state "designed-only, no per-agent source yet" (line 407) — which
  is exactly why status is truthful-static here.

---

## 1. Architecture & component boundaries

The drawer becomes a container docked right, holding two stacked panels:

```
#agent-drawer  (Vertical, dock:right, width ~34)
├── ≡ AGENTS                      (SectionLabel — a styled Static)
├── #agent-rail  (AgentRail = ListView)
│     └── ListItem → PersonaCard per row:
│           ┌────────────────────────────────────┐
│           │  Fred                  RUNNING ●    │   name (left) + StatusChip+StateDot (right)
│           │  2 tasks                            │   sub-line (muted)
│           └────────────────────────────────────┘
├── (hairline rule)
└── #quick-keys  (QuickKeysPanel — ≡ QUICK KEYS + key rows)
```

**Units (each pure/dumb, snapshot-in → renderable-out, unit-testable like render.py):**

1. **`PersonaRow` gains `status: AgentState`** (`harness/tui/roster.py`) — default
   `AgentState.IDLE`. The roster sets the ACTIVE row's real state; others stay IDLE.
2. **`card_markup(row) -> str`** (new pure fn in `agent_rail.py`) — composes the
   card's inner markup: name (`$accent` bold if active, else `$foreground`),
   sub-line (`$muted`), and the status label + dot. No icon tile. Returns a markup
   string a `Static` renders (the `ListItem` holds this `Static`; the card BORDER
   is CSS, not markup).
3. **`AgentRail.set_rows(rows)`** changed — each `ListItem` wraps a `Static(card_markup(r))`
   with class `persona-card` (+ `active` class on the active row); AND sets
   `self.index` to the active row's index (pre-highlight).
4. **`QuickKeysPanel`** (new tiny widget) — renders `≡ QUICK KEYS` + a fixed list
   of `(key, label)` rows from a module constant.

**Boundaries:** `card_markup` and `QuickKeysPanel`'s row rendering are pure
functions — no Textual app needed to test. The app composes the drawer and feeds
rows; widgets never compute state (components.md principle #1).

---

## 2. The card visual spec (design-system tokens only — NO new tokens)

```
┌──────────────────────────────────────────┐   border: $accent (active row) / $surface (idle)
│   Fred                       RUNNING ●     │   name: $accent bold (active) / $foreground (idle)
│   2 tasks                                  │   StatusChip(label, token) + StateDot ; sub-line $muted
└──────────────────────────────────────────┘
```

- **Name:** active → `[$accent][b]name[/b][/]`; idle → `[$foreground]name[/]`.
- **Status (right):** label + token from the row's `AgentState` via the existing
  maps. Active card → its real state (e.g. `RUNNING`/`$accent`,
  `RESPONDING`/`$accent`, `SCHEDULED`/`$scheduled`). Idle cards → `IDLE`/`$muted`.
  The trailing dot is the same glyph/colour `StateDot` uses (reuse `_STATE_GLYPH` +
  `state_color_token`). Rendered inline in `card_markup` (not a separate StatusChip
  widget) so the whole card is one `Static` — simpler mounting; the chip's VISUAL
  grammar (uppercase, token colour, bold) is reproduced from the same maps.
- **Sub-line:** `$muted`. Active → real `f"{n} tasks"`/`"1 task"` (n from
  `snapshot.active.tasks`); others → `idle`.
- **Active/highlight treatment (CSS, `app.tcss`):**
  - `.persona-card` — padding, `border: round $surface`, `background: $surface`.
  - `.persona-card.active` — `border: round $accent`, `background: $accent 10%`
    (the glowing Fred card).
  - `#agent-rail ListItem.--highlight .persona-card` — `border: round $accent`
    (the keyboard-highlighted row reads as accented too). Since the active row is
    pre-highlighted on open, it shows BOTH treatments — matching the mockup.
- **Drawer width:** widen `#agent-drawer` to `34` (from today's `28`) so name +
  status fit without clipping.

**QUICK KEYS panel:** `≡ QUICK KEYS` SectionLabel, then rows from the constant:
```
QUICK_KEYS = [("tab","switch panel"), ("↑↓","navigate"), ("enter","open details"),
              ("/","focus prompt"), ("?","show help"), ("q","quit DoneDone")]
```
Each row: a key-cap (`$surface` bg, `$muted` border, the key text) + a `$muted`
label. Static — a keybinding reference, not wired to behavior. (Reuses the chip /
border grammar; no new tokens.)

> NOTE: the `?`/`q`/`/` keys are a REFERENCE legend; this slice does not implement
> any new key behavior (e.g. `?` help) — it documents existing/intended keys. If a
> listed key isn't actually bound, that's acceptable for a legend, but prefer
> listing only keys that work today (tab/↑↓/enter/esc//) unless asked otherwise.

---

## 3. Interaction: pre-select on open + commit

- **Open (tab / `action_toggle_rail`):** after `set_rows`, the rail's `.index` is
  the active persona's row index (computed from `PersonaRow.active`), so the active
  card is **pre-highlighted**. Focus the rail. (Two open sites: `app.py:577-581`
  tab path, `app.py:1033-1041` toggle. `set_rows` itself sets the index, so both
  sites get it for free.)
- **↑↓:** moves the ListView highlight only. Already correct (nothing listens to
  `Highlighted`); the card rebuild must not add a `Highlighted` listener. Verified
  by test.
- **enter:** `ListView.Selected` → `PersonaSelected(highlighted id)` →
  `on_persona_selected` → `set_persona` → `_apply_persona_switch` (closes drawer +
  refocuses prompt — existing, app.py:1043-1082). **No-op guard:** if the chosen
  id == the current active id, skip the `ext_method` call and just close the drawer
  (add the guard in `on_persona_selected`).
- **esc:** closes with no change (existing, app.py:588-594).

The only behavior changes: **set initial index on open** (the gap) + the **no-op
guard** on enter-active. Everything else is the existing flow.

---

## 4. Test plan

- **`card_markup(row)`** pure: active row → name in `$accent` + bold, real status
  label, real sub-line; idle row → `$foreground` name, `IDLE`, `idle` sub-line; no
  `≡`/icon-tile glyph present.
- **`PersonaRow.status`** defaults to `AgentState.IDLE`; roster sets the active
  row's state when supplied.
- **`AgentRail.set_rows`**: `.index` lands on the active row (pre-select); items
  carry the `persona-card` class and the `active` class on the active row.
- **Navigate-vs-commit pilots** (`test_tui_pilot.py`): opening the drawer
  pre-highlights the active row; `↑↓` moves highlight WITHOUT posting
  `PersonaSelected`; `enter` posts `PersonaSelected(highlighted id)`; `enter` on the
  active row does NOT call `set_persona` (no-op) but still closes.
- **`QuickKeysPanel`**: renders all listed key rows.
- Full suite green (`.venv/bin/python -m pytest tests/ -q`, worktree cwd);
  `upstream/` untouched; primary checkout clean.

---

## 5. Deferred / out of scope (tracked)

1. **Real per-agent telemetry** (live status + task counts for non-active
   personas) — needs the N-concurrent fleet runtime (multiple live seats). The
   truthful-static design upgrades to it for free when the per-seat snapshot source
   exists.
2. **The icon tile** — explicitly dropped by the user.
3. **New key behaviors** the legend references (`?` help) — the panel is a
   reference, not new bindings.

---

## 6. Provenance

Verified against the worktree (2026-06-28): `agent_rail.py:33` (`_row_label`
one-liner), `:46-52` (`set_rows` no initial index), `:65-70` (`_on_selected` on
`ListView.Selected`); `app.py:157` (statusbar hint), `:577-581` (tab open),
`:1033-1041` (`action_toggle_rail`), `:1043-1082` (`on_persona_selected` +
`_apply_persona_switch`), `:1023-1031` (`_persona_rows`); `app.tcss:147`
(`#agent-rail` CSS); `status_chip.py:54` (`state_color_token`), `:82-86`
(`StateDot`), `_STATE_GLYPH`/`_STATE_TOKEN` maps; `roster.py` (`PersonaRow`);
`tokens.py`/`theme.py` (glyphs + status colours). Textual `ListView` posts
`Selected` on enter (`action_select_cursor`) and `Highlighted` on move — verified.
Re-verify before acting (AGENTS.md #6).
