# Persona C2b — AppShell + AgentRail drawer (rail VIEW; switching deferred to C2c)

**Status:** design / spec — **REVISED 2026-06-27: switching removed, see banner.**
**Date:** 2026-06-27
**Author:** Alberto Vasquez (with Claude Opus 4.8)
**Part of:** the C2 drawer arc (`2026-06-27-persona-C2-drawer-arc-design.md`). Sub-project
**C2b** — the rail VIEW. Builds on **C2a** (merged, PR #46 — the persona seam +
status-bar chip) and the design-system component spec (`AppShell`/`AgentRail`/
`SidebarToggle` in `harness/tui/styles/components.md`).
**Tracker:** issue #29.

---

> ## ⚠️ REVISION BANNER (2026-06-27) — switching removed from C2b
>
> This spec originally had C2b **switch** personas by re-execing the agent with a new
> `--persona`. Three Codex review passes found recurring state-leak bugs (the old
> persona's model/yolo/env leaking into the re-exec'd child; backend-vs-model flag
> conflicts) — all rooted in the same fact: **re-exec is the wrong primitive for
> per-persona switching.**
>
> **Research into the standard (OpenClaw, Hermes, OpenCode, Codex #12047) is
> conclusive:** every mature multi-agent harness switches agents **in-process** — it
> routes to an already-loaded session and never restarts the process; per-agent model
> is **resolved at session-start and overrides the base config**. OpenClaw's
> long-lived **Gateway** holds N stateful sessions and ticks them; Codex states
> "session/model switching are deterministic REPL ops handled **without invoking the
> agent**."
>
> **Decision:** C2b ships the rail as a **VIEW + indicator only** — lists personas,
> highlights the active one, `Tab` focuses it. **Real switching moves to C2c**, where
> the long-lived-process engine (the OpenClaw Gateway model our roadmap targets) makes
> it clean and standard-aligned. The re-exec switch machinery is removed.
>
> Sections below describing re-exec switching are **superseded** by this banner and §1.

---

## 1. Purpose (revised)

Surface ALL personas in the TUI: a toggleable right-side **AgentRail** that **lists**
every persona workspace and **highlights the active one** (C2a's `active_id`). `Tab`
moves focus from the prompt into the rail (the in-process focus model the research
validates). The rail is a **view + indicator** — it does NOT switch personas.

C2a answered "what persona am I on?" (a status-bar chip). C2b answers "what personas
exist + which is live." **Switching** (in-process, per the standard) and N concurrent
personas land in **C2c** with the long-lived-process engine.

**Out of scope (now explicitly):** **persona switching** (deferred to C2c — needs the
long-lived-process engine); persona *creation* (Phase D); multiple personas live at
once (C2c); a left rail / crons (Phase E).

**Why switching is deferred, not done badly here:** switching done via re-exec leaks
per-persona state (proven across 3 Codex passes) and contradicts the universal
in-process-switch standard. C2c does it right; C2b ships the clean view it builds on.

---

## 2. Architecture

Three TUI-side pieces, all on C2a's `FleetSnapshot.active_id` seam:

1. **Roster** — `persona_select.list_personas()` (C1, currently unwired into the TUI)
   gives the rail its entries; a new `persona_config.read_name()` gives display names.
   A pure `roster.persona_rows()` composes `[(id, name, is_active)]`.
2. **AgentRail widget** — a right rail, collapsed by default, opened via a key/footer
   toggle; renders one selectable row per persona (name + active marker), active row
   styled with the accent token; emits `PersonaSelected(id)` on click/enter.
3. **Switch by re-exec** — selecting a persona re-execs the agent with the chosen
   `--persona`, REUSING the existing `/reload` re-exec machinery (`self._reexec` +
   `tui_main._relaunch_command`, where `_relaunch_args` already emits `--persona`,
   tui_main.py:62). The app records the chosen id; `tui_main` threads it into the
   relaunch instead of the current persona.

**Switch semantics (honest to C1's one-process-one-persona engine):** switching ends
the current session and starts the selected persona fresh (its own
workspace/memory/model). You return to a persona by switching back (a fresh session).
The rail is a **launcher/switcher**, not a live multi-agent view — that is C2c. At N=1
(only `default` exists today) the rail opens and shows the one persona; creating more
is Phase D.

**Reuse ledger (from the arc spec):** the rail reads `FleetSnapshot.active_id` (C2a)
for highlighting; it ADDS `list_personas()` + `read_name()` for the full roster —
exactly the "C2b still needs `list_personas()` wiring for the non-active entries" the
arc spec called out. C2c later replaces "re-exec one" with "keep N alive".

---

## 3. Components (files & responsibilities)

### New

```
harness/tui/roster.py            PURE roster model (the testable core; no Textual, no I/O)
  @dataclass(frozen=True) class PersonaRow: id: str; name: str; active: bool
  persona_rows(personas: list[str], active_id: str,
               name_of: Callable[[str], str | None]) -> tuple[PersonaRow, ...]
    - one row per persona id: (id, name_of(id) or id, id == active_id)
    - INVARIANT: the active id ALWAYS appears as a row, even if not in `personas`
      (mirrors C2a's "active is never None"); appended if missing, marked active.
    - deterministic order: personas in given order, the active-but-absent id last.

harness/tui/widgets/agent_rail.py   The rail widget (dumb/reactive)
  class AgentRail(Widget): takes tuple[PersonaRow,...]; renders one selectable line
    per persona (active marker glyph + name; active row uses the $accent token);
    emits PersonaSelected(id) (a Textual Message) on click/enter. No business logic.
  class PersonaSelected(Message): id: str
  (SidebarToggle affordance is minimal — a key binding + a footer hint; see app.py.)
```

### Extended

```
harness/persona_config.py        read_name(workspace_dir: Path | None) -> str | None
  - the optional persona.toml `name` field; missing/corrupt/no-key/non-str → None.
  - same tolerant contract as read_skills; gives persona.toml a second consumer.

harness/tui/app.py               Mount + toggle + selection → re-exec:
  - mount AgentRail hidden by default; a `toggle_rail` action bound to **`tab`** (the
    landing hint already advertises "tab agents" at app.py:135 — the UI anticipated
    this; `tab` is otherwise free per the BINDINGS + on_key check). CAVEAT: `tab` is
    Textual's default focus-traversal key, so the binding must intercept it at the app
    level (a BINDINGS entry or on_key handler with `event.stop()`), not rely on a
    widget-level default. A footer hint segment reflects the toggle.
  - build rows via roster.persona_rows(list_personas(), self._snapshot.active_id,
    name_of) and hand to the rail; refresh when active_id changes (the C2a _apply path).
  - on PersonaSelected(id): if id == active → no-op; else set self._switch_persona = id
    and self._reexec = True (mirrors action_reload), then exit run().

harness/tui_main.py              Re-exec with the chosen persona:
  - in the post-run() _reexec block (tui_main.py:117), if app recorded _switch_persona,
    set args.persona = app._switch_persona BEFORE _relaunch_command(args, cwd). The
    existing _relaunch_args emits --persona, so the new process boots as the selection.

harness/tui/commands.py          The /persona keyboard path:
  - /persona (no arg): open the rail (same as the toggle).
  - /persona <id>: switch directly if <id> in list_personas(); else a clear TUI error
    (reusing C1's resolve-existing/error-on-unknown semantics), NO re-exec.
```

### Layout (CSS)

```
harness/tui/app.tcss             Right-rail dock, collapsed-by-default, active-row style.
  Collapses to today's single column when hidden (AppShell "N=1 / narrow" rule).
  Minimal, next to the existing #statusbar-* rules.
```

**Not touched:** the engine (`acp_agent`/`acp_main`), `new_session`, any concurrency.
Switching is re-exec only.

---

## 4. Data flow

```
list_personas() + read_name(ws)
   → roster.persona_rows(personas, active_id, name_of) → tuple[PersonaRow,...]
   → AgentRail renders (active row highlighted via active_id from C2a's FleetSnapshot)
   → user selects a row → PersonaSelected(id)  (Textual message)
   → app: id == active_id ? no-op : (self._switch_persona = id; self._reexec = True; exit)
   → tui_main post-run(): args.persona = app._switch_persona; os.execv(_relaunch_command)
   → new agent boots with --persona id  → C2a's persona chip shows the new id
```

The highlight tracks the active persona automatically: when the re-exec'd agent emits
its `persona` chip (C2a), `reduce` updates `active_id`, and the rail's next refresh
re-marks the active row.

---

## 5. Error handling

| Case | Behavior |
|---|---|
| `list_personas()` empty / `agents/` missing | Rail shows just the active persona (the roster invariant), or a neutral "no other personas" hint. (`list_personas` already returns `[]` on a missing dir.) |
| `persona.toml` missing / no `name` / corrupt | `read_name → None` → row falls back to the id. Never raises. |
| `active_id` not in `list_personas()` | `persona_rows` still includes it as an active row (the invariant) — the active persona always appears + is highlighted. |
| `/persona <unknown-id>` | Clear TUI error (via `list_personas()` membership), NO re-exec — never silently switches to default. |
| Select the already-active persona | No-op — don't re-exec into the same persona. |
| Re-exec (`os.execv`) fails | Existing `tui_main` handling (stderr message); C2b adds no new crash surface. |
| Rail toggled at N=1 | Opens, shows the single persona highlighted — honest, not an error. |

---

## 6. Testing strategy

- **`tests/test_tui_roster.py`** (NEW) — `persona_rows`: composes rows from ids +
  active_id + a name function; active flag correct; name falls back to id when
  `name_of` returns None; the active id always appears even if absent from `personas`
  (the invariant); deterministic order. Pure, no Textual.
- **`tests/test_persona_config.py`** (extend) — `read_name` returns the `name`; `None`
  on missing/corrupt/no-key/non-str (mirrors the `read_skills` tests).
- **`tests/test_tui_pilot.py`** (extend) — toggling the rail shows/hides it; the rail
  lists the personas; the active row is styled active; selecting a row emits
  `PersonaSelected` and sets `_switch_persona` + `_reexec`; selecting the active
  persona is a no-op; the rail is hidden by default (today's view unchanged).
- **`tests/test_tui_main.py`** (extend) — `app._switch_persona` set → the re-exec
  command carries `--persona <new-id>` (not the old one); unset → carries the current
  persona (unchanged). Locks the switch actually changing the launched persona.
- **`tests/test_commands.py`** (or the commands test file) — `/persona` (no arg) opens
  the rail; `/persona fred` (exists) switches; `/persona ghost` (unknown) errors, no
  re-exec.
- **Regression locks:** rail hidden by default; switch-to-same is a no-op; `read_name`
  tolerant.

Full suite stays green; net-new tests cover the roster, the name reader, the rail
widget, the switch wiring, and the command.

---

## 7. Definition of done (C2b)

- A toggleable right AgentRail lists all persona workspaces, the active one highlighted
  (from C2a's `FleetSnapshot.active_id`); hidden by default (today's view unchanged).
- Selecting a persona (rail click/enter, or `/persona <id>`) switches by re-exec with
  the new `--persona`; selecting the active one is a no-op; unknown id errors cleanly.
- Display names come from `persona.toml` `name` (id fallback).
- No engine change; switching is re-exec only (one process = one persona).
- Full suite green; roster/name-reader/rail/switch-wiring/command each tested.
- (C2c — N concurrent personas — remains a separate cycle with its own engine fork +
  Codex review.)
