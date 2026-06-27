# YOLO mode — persistence + footer chip — design

**Date:** 2026-06-26
**Branch:** `worktree-yolo-persist-chip`
**Status:** approved design, ready for implementation plan

## Problem

"YOLO mode" auto-allows every command — the permission gate returns `True`
without prompting (`harness/acp_agent.py:45-48`, `_auto_allow()` returns
`self._yolo`). Today it is a purely transient, launch-only flag:

- set only by the `--yolo` CLI flag (`harness/tui_main.py:70-71`),
- carried to the agent subprocess (`tui_main.py:95-96`) which owns the gate,
- **never persisted** — every launch starts `yolo=False` unless the flag is
  passed again, and
- **invisible in the UI** — nothing in the footer or anywhere else signals that
  commands are auto-running. The TUI process doesn't even *know* its own YOLO
  state: `HarnessTui.__init__` (`app.py:77-84`) takes no `yolo` argument.

Two gaps follow. (1) A user who wants to stay in YOLO must remember `--yolo`
every launch. (2) When YOLO *is* on, there is no on-screen signal that the
permission prompt has been bypassed — a silent, security-sensitive state.

This task delivers, mirroring the existing `done.conf` model-persistence
machinery (`done-conf-model-persistence-design.md`):

1. **Opt-in persistence** of YOLO via a *pinned* flag in `done.conf`.
2. A **clickable footer chip** that shows the mode and toggles it.
3. A **`/yolo`** slash command to toggle / pin / unpin.
4. A **catalog entry** for the new "clickable footer mode chip" interaction
   pattern, so other areas reuse it instead of re-rolling click handling.

## Decisions (locked in brainstorming)

- **Two separate states, kept separate by design** (this is the safety
  argument):
  - `yolo` (**live**, ephemeral) — am I auto-allowing commands *right now*, this
    session? Already exists as `HarnessAgent._yolo`; gains a TUI-side mirror.
  - `yolo_pinned` (**persisted**) — should the *next* launch start in YOLO?
    New; lives in `done.conf` `[agents.default]`.
- **Clicking the chip toggles the live mode only** (on ⇄ off). It never touches
  the pin. Toggling and persisting are decoupled.
- **Pinning is a deliberate, separate act** via `/yolo pin` / `/yolo unpin`
  (not a click), so a stray click can never silently make a permission bypass
  *survive across launches*. A stray click can only flip the *live* mode, which
  is loudly visible and reversible with one more click.
- **CLI overrides persisted**, mirroring `--model` precedence exactly:
  `--yolo` flag (force on) > persisted `yolo_pinned` > default (off).
- **Active YOLO is signalled by the chip only** — one persistent amber chip,
  no per-command markers and no banner. Consistent with the design system's
  restraint principle (`components.md` §Principles 4).

## Scope

**In scope (this task):**

- `done.conf` schema: add `yolo_pinned: bool` to the `default` agent table.
- `harness/config.py`: round-trip `yolo_pinned`; **fix `save_default` to merge**
  (preserve fields the caller didn't set — see "correctness trap" below).
- Launch resolution: a `_resolve_yolo()` in `tui_main.py` mirroring
  `_resolve_model()`'s precedence; pass the resolved live `yolo` to both the
  agent subprocess (existing) **and** `HarnessTui` (new) so the footer can show
  it; carry it through `/reload` re-exec.
- Runtime: a new `harness/set_yolo` ext-method (sibling of `harness/set_model`)
  that sets live `_yolo` and, on pin/unpin, persists `yolo_pinned`.
- TUI: a clickable footer **mode chip** (reuses/extends `StatusChip`), updated
  in place; a `/yolo` slash command (`toggle` | `pin` | `unpin`).
- Catalog: document the clickable-footer-mode-chip pattern in
  `harness/tui/styles/components.md` (and a one-line note in the design-system
  spec §4), with rationale.
- Tests: config round-trip + merge; launch precedence; chip rendering for the
  three states; `/yolo` subcommand dispatch.

**Out of scope:**

- A `--no-yolo` flag to *force off* over a pin (could be added later; not
  needed — `/yolo` unpins from inside).
- Per-named-agent YOLO (the schema is per-`default` only, like model today).
- Per-command "auto-allowed" markers / banners (explicitly rejected).
- Changing the permission gate's *semantics* — `_auto_allow()` still just
  returns `self._yolo`. We only change how `_yolo` is *set* and *shown*.

## File format & location

Same file as model persistence: `paths.config_dir() / "done.conf"`
(`$XDG_CONFIG_HOME/harness/done.conf`). One new optional key on the `default`
table:

```toml
schema_version = 1

[agents.default]
backend     = "vibeproxy"   # "mock" | "vibeproxy"
model       = "gpt-5.4"     # the model string (VIBEPROXY_MODEL value)
yolo_pinned = true          # NEW — start in YOLO on next launch (default: absent/false)
```

Schema notes:

- `yolo_pinned` is **optional**; absent ⇒ `False`. No `schema_version` bump:
  adding an optional key that defaults false is forward/backward compatible
  (old harness ignores it; new harness treats its absence as `False`).
- It is a property of the **`default` agent table**, alongside `backend`/`model`.
  Future uuid-keyed agents could carry their own; not wired this task.

## Component changes

### `harness/config.py` — round-trip + the merge fix

`AgentConfig` gains a field:

```python
@dataclass(frozen=True)
class AgentConfig:
    backend: str
    model: str
    name: str | None = None   # (unchanged; None for default)
    yolo_pinned: bool = False  # NEW — persisted "always launch in YOLO"
```

- **`load()`** reads `yolo_pinned` from each agent table: `bool` when present
  and a bool, else `False`. A non-bool value (hand-edit error) ⇒ `False`, never
  fatal (consistent with the corrupt-tolerant philosophy).
- **`_serialize()`** writes `yolo_pinned = true|false` (TOML bool literal) for
  the default table. It is written **only when `True`** to keep clean diffs and
  inert files minimal — an absent key already means `False`. (Decision: omit
  when false rather than always emit, matching the "minimal file" intent of the
  model-persistence design.)

#### The correctness trap — `save_default` must MERGE

`save_default` today (`config.py:110-111`) **rebuilds the default from scratch**:

```python
agents[RESERVED_KEY] = AgentConfig(backend=cfg.backend, model=cfg.model)  # drops everything else
```

Left as-is, `harness/set_model` would **silently wipe a pin** every time the
user changes the model (and a naive `set_yolo` would wipe the model/backend).
Fix: `save_default` reads the **existing** default and merges, preserving fields
the caller did not intend to change. Concretely, introduce a small merge so
that:

- `set_model` updates `backend`+`model`, preserving an existing `yolo_pinned`.
- `set_yolo` updates `yolo_pinned`, preserving existing `backend`+`model`.

Two clean options for the plan to choose between (functionally equivalent):

1. **`save_default` merges:** read `load().get("default")`, overlay the
   provided `AgentConfig`'s set fields onto it, write the union. (Requires
   distinguishing "field not provided" from "field is its default" — use an
   `_unset` sentinel or a dedicated `update_default(**fields)` helper.)
2. **A focused `update_default(*, backend=..., model=..., yolo_pinned=...)`**
   helper that loads the existing default, applies only the kwargs passed, and
   writes. `save_default` becomes a thin wrapper (`backend`+`model`). `set_yolo`
   calls `update_default(yolo_pinned=...)`.

**Recommended: option 2** — `update_default(**fields)` reads cleanest, makes the
"only touch what you pass" contract explicit at the call sites, and avoids a
sentinel dance inside `AgentConfig`. The plan finalizes the signature.

New read helper:

```python
def yolo_pinned() -> bool:           # convenience: load_default().yolo_pinned, or False
```

### `harness/acp_agent.py` — `harness/set_yolo` ext-method

Sibling of `harness/set_model` (`acp_agent.py:53-67`). The ACP process owns the
gate, so it owns both the live flip and the persist:

```python
if method == "harness/set_yolo":
    params = params or {}
    if "active" in params:               # live toggle (from a chip click)
        self._yolo = bool(params["active"])
    pin = params.get("pin")              # None = don't touch persistence
    if pin is not None:
        try:                             # best-effort, mirrors set_model
            config.update_default(yolo_pinned=bool(pin))
        except Exception:
            pass
    return {"ok": True, "active": self._yolo, "pinned": _read_pinned_best_effort()}
```

- `active` (bool, optional) — set the live gate. Omitted ⇒ live unchanged.
- `pin` (bool, optional) — `True` pins, `False` unpins, omitted leaves
  persistence untouched. Best-effort write (a failed write never breaks the
  call), exactly like `set_model`.
- Returns the resulting `active`/`pinned` so the TUI can reflect truth.

`_auto_allow()` is **unchanged** (`return self._yolo`); only the setter is new.

### `harness/tui_main.py` — launch resolution + wiring the TUI

A `_resolve_yolo()` mirroring `_resolve_model()`:

```python
def _resolve_yolo(flag: bool) -> bool:
    """--yolo flag forces on; else the persisted pin; else off."""
    if flag:
        return True
    return config.yolo_pinned()
```

Then in `main()`:

- compute `yolo = _resolve_yolo(args.yolo)`,
- normalize `args.yolo = yolo` so `_relaunch_args` carries the resolved value
  through `/reload` (today it only re-passes a literal `--yolo`; after this it
  re-passes the *resolved* live state, so a pinned launch stays YOLO across
  reload, and a clicked-off session does **not** silently come back on after
  reload — see "reload semantics" below),
- pass `yolo` into the agent command (existing `agent_cmd.append("--yolo")`,
  now gated on the resolved value) **and** into `HarnessTui(... yolo=yolo)` so
  the footer renders correctly from the first frame.

`HarnessTui.__init__` gains `yolo: bool = False`, stored as `self._yolo` (the
TUI-side mirror of the gate, for display + click handling). It also tracks
`self._yolo_pinned` for the chip's "· pin" marker, seeded from
`config.yolo_pinned()` at construction.

#### Reload semantics (decided)

`/reload` re-execs the whole TUI (`_relaunch_command`). The re-exec should
reflect the user's **live** intent at reload time, not the launch flag:

- If they clicked YOLO **off** during the session, reload should come back
  **off** (don't resurrect it from the original `--yolo`).
- If they clicked it **on**, reload should stay **on**.

So `_relaunch_args` carries `args.yolo` after it's been normalized to the live
state. The TUI updates `args.yolo`-equivalent state on each click. (Mechanism:
the app exposes its final live `yolo` for the relaunch builder, the same way it
already drives `_reexec`. The plan picks the cleanest seam — likely the app
stores the live value and `_relaunch_args` reads it, or the app writes it onto
`args` before `run()` returns.)

Persistence (`yolo_pinned`) is independent of reload: a pin survives reload
because reload reloads `done.conf` like any launch.

### TUI footer chip — reuse `StatusChip`, add a clickable wrapper

**Reuse, don't reinvent.** The *pill* is the existing
`StatusChip(label, color_token)` (`status_chip.py:55-65`) — bold caps, themed
color. Add a factory beside `from_state`:

```python
@classmethod
def for_yolo(cls, active: bool, pinned: bool) -> "StatusChip":
    # StatusChip renders "[$token][b]{label}[/b][/]" — no separate glyph slot,
    # so the leading glyph is baked into the label (it's bold+colored too,
    # which is fine: glyph carries state alongside color+weight).
    if not active:
        return cls(f"{GLYPH['idle']} ask", "muted")        # "· ask"  (off, muted)
    suffix = " · pin" if pinned else ""
    return cls(f"{GLYPH['bypass']} YOLO{suffix}", "scheduled")  # "! YOLO[ · pin]"  amber
```

**Glyphs (box-safe, no emoji — per `components.md`: color+glyph+weight).**
The brainstorming mockups used `⚡`, which is an emoji and renders inconsistently
across terminals — rejected. The existing glyph vocabulary (`tokens.py`) has no
"danger/bypass" glyph: `•` (idle) and `⏱` (scheduled/clock) are wrong
semantics, and `▌` is already bound to the *responding* state — reusing it would
overload an existing meaning, which the catalog forbids. So we **add one new
glyph** to `tokens.py`: `"bypass": "!"` (an ASCII bang — maximally portable,
reads as "attention/override", monochrome-safe). This is the sanctioned, minimal
catalog addition.

| State | Chip text | Token | Glyph |
|---|---|---|---|
| off | `· ask` | `$muted` | `•` (idle dot) |
| on (session) | `! YOLO` | `$scheduled` (amber, bold) | `!` (new `bypass` glyph) |
| on + pinned | `! YOLO · pin` | `$scheduled` (amber, bold) | `!` + ` · pin` suffix |

The constraint that drives this: amber + bold + leading glyph for "on", muted +
dot for "off", so the state survives a monochrome terminal. The exact suffix
spelling (`· pin` vs `· pinned`) is finalized in the plan; `· pin` keeps the
chip short.

**Clickability — the genuinely new pattern.** Nothing in the footer is
clickable today; nothing in the catalog defines a clickable status element. We
add a thin clickable wrapper:

- The chip widget handles `on_click` → posts a message / calls an app action
  `action_toggle_yolo()`.
- `action_toggle_yolo()` flips `self._yolo`, calls
  `ext_method("harness/set_yolo", {"active": <new>})`, and refreshes the chip in
  place via a `_refresh_yolo_chip()` method (mirroring `_refresh_status()` at
  `app.py:228-232`).
- Mount: a third child of `#statusbar`, right-aligned (its own `id`, e.g.
  `#statusbar-mode`), mounted in `_mount_status_contents()` alongside the
  existing left/right statics. CSS in `app.tcss` next to `#statusbar-right`.

**Design-system catalog entry.** Add to `components.md` under §A Primitives an
entry documenting the **clickable footer mode chip** built on `StatusChip` —
"a `StatusChip` mounted in the status bar with an `on_click` → app-action seam,
for toggling a session mode (YOLO today; backend/fleet-mode tomorrow)." Include:
the click→action→ext-method→refresh data flow, the amber-attention convention
for a security-sensitive on-state, and the rule that *persisting* a mode is a
separate deliberate gesture, never the click. Add a one-line cross-reference in
the design-system spec §4. This satisfies the goal's "if we need an extra
pattern, document it so other areas can use it."

### `harness/tui/commands.py` — `/yolo`

Add to `build_registry()` (sibling of `/models`). One command with subcommand
parsing in its handler:

- `/yolo` (no arg) → toggle live (same as a chip click).
- `/yolo pin` → `set_yolo {active: true, pin: true}` (pin implies turning it
  on — pinning a mode you're not in is incoherent; decided).
- `/yolo unpin` → `set_yolo {pin: false}` (leaves live state alone).
- `/yolo on` / `/yolo off` → explicit live set (nice-to-have aliases; the plan
  may include if cheap).

Handler refreshes the chip after the ext-method returns. Appears in the
`ctrl+p` palette and `/help`.

## Data flow

```
launch (TUI)
  └─ paths.load_env(cwd)
  └─ yolo = _resolve_yolo(args.yolo):
        --yolo flag ──true──> yolo = True              (force on; pin ignored)
                    ──false─> config.yolo_pinned()      (persisted intent, else False)
  └─ args.yolo = yolo                                   (so /reload carries live state)
  └─ agent_cmd += ["--yolo"] if yolo                    (agent owns the gate)
  └─ HarnessTui(..., yolo=yolo)                          (NEW: TUI shows the chip)

click chip / "/yolo"  (TUI)
  └─ self._yolo = not self._yolo
  └─ ext_method("harness/set_yolo", {"active": self._yolo})
  └─ _refresh_yolo_chip()                                (in place, amber/ muted)

"/yolo pin" (TUI)
  └─ ext_method("harness/set_yolo", {"active": true, "pin": true})
        └─ (ACP) self._yolo = true
        └─ (ACP) config.update_default(yolo_pinned=true)   best-effort
  └─ _refresh_yolo_chip()                                  shows "! YOLO · pin"

runtime gate (ACP, unchanged)
  request_permission → _auto_allow() → return self._yolo
```

## Error handling

- **Corrupt / unreadable `done.conf`** → `load()` returns `{}`,
  `yolo_pinned()` returns `False`; launch falls through to "ask" unless `--yolo`.
  Never fatal (inherits the existing corrupt-tolerant `load`).
- **Non-bool `yolo_pinned` (hand-edit)** → treated as `False`.
- **Unwritable config on pin** → caught, ignored; the live toggle still
  succeeds and the chip still updates (the pin just didn't persist this time),
  exactly like `set_model`.
- **Agent without `harness/set_yolo`** (older agent) → `ext_method` raises;
  the TUI swallows it (the click still flips the *displayed* state but the gate
  won't change — acceptable degradation; in practice TUI and agent ship
  together). The plan wraps the call in the same try/except `_reapply_model`
  uses.
- **Click while a turn is busy** → toggling the gate mid-turn affects
  *subsequent* permission checks only (the gate is read per request); no
  in-flight permission Future is disturbed. No special handling needed.

## Testing

Unit — `harness/config.py` (no process spawn):

- `load()` reads `yolo_pinned` true/false/absent → bool; non-bool → `False`.
- `update_default(yolo_pinned=True)` then `load_default().yolo_pinned` is True.
- **Merge:** `update_default(yolo_pinned=True)` preserves existing
  `backend`/`model`; `save_default`/`update_default(model=...)` preserves an
  existing `yolo_pinned`. (The regression the trap warns about.)
- `_serialize` omits `yolo_pinned` when False, emits `true` when True; the
  written file round-trips and preserves other agent tables + `schema_version`.
- `yolo_pinned()` convenience returns the default's value, or `False`.

Unit — resolution:

- `_resolve_yolo(True)` → True regardless of pin.
- `_resolve_yolo(False)` → `config.yolo_pinned()` (pinned→True, absent→False).

Unit — TUI chip (pilot/snapshot, matching `tests/test_tui_widgets.py`):

- `StatusChip.for_yolo(False, _)` → muted "ask".
- `for_yolo(True, False)` → amber "YOLO" (no pin marker).
- `for_yolo(True, True)` → amber "YOLO · pin".
- Clicking the chip invokes `action_toggle_yolo` and the chip text flips
  (pilot click test).

Unit — `set_yolo` handler:

- `{"active": true}` sets `_yolo`, doesn't touch persistence.
- `{"pin": true}` calls `update_default(yolo_pinned=True)`; `{"pin": false}`
  calls it with False; omitted `pin` doesn't call it.
- Returns `{"ok": True}` even when the persist write raises (monkeypatch
  `update_default` to raise).

Unit — `/yolo` command dispatch: bare toggles; `pin`/`unpin` send the right
params (handler tested against a fake conn capturing ext_method calls).

## Files touched

- **edit** `harness/config.py` — `AgentConfig.yolo_pinned`, `load` parse,
  `_serialize` emit, `update_default` (merge), `yolo_pinned()` helper.
- **edit** `harness/acp_agent.py` — `harness/set_yolo` ext-method.
- **edit** `harness/tui_main.py` — `_resolve_yolo`, wire `yolo` into
  `HarnessTui`, carry live state through `/reload`.
- **edit** `harness/tui/widgets/status_chip.py` — `StatusChip.for_yolo`.
- **edit** `harness/tui/app.py` — `__init__(yolo=...)`, mount `#statusbar-mode`
  chip, `action_toggle_yolo`, `_refresh_yolo_chip`.
- **edit** `harness/tui/app.tcss` — `#statusbar-mode` styling.
- **edit** `harness/tui/commands.py` — `/yolo` command + handler.
- **edit** `harness/tui/tokens.py` — add `"bypass": "!"` to `GLYPH` (the one
  new glyph; the existing vocabulary has no danger/bypass icon and `▌` is taken).
- **edit** `harness/tui/styles/components.md` — document the clickable footer
  mode chip pattern.
- **edit** `docs/superpowers/specs/2026-06-26-tui-design-system-design.md` —
  one-line cross-reference (§4).
- **new/edit** `tests/test_config.py`, `tests/test_tui_widgets.py`,
  `tests/` for `set_yolo` + `/yolo` + resolution — per the test plan above.
