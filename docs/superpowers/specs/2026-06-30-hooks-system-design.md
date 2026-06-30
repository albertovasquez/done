# Internal hook system + session-end auto-regen — design

**Date:** 2026-06-30
**Issue:** #188 (compress-aware Phase 2) — this delivers the auto-regen item
surfaced during verify-for-real (#188 comment, 2026-06-30).
**Branch/worktree:** `hooks-system` off `origin/main`.

## Problem

`dn compress` is **entirely manual** today (`tui_main.py` intercepts the
`compress` arg → `compress_cli.run`; nothing else invokes it — no cron, no
on-launch, no on-stale). So after you edit a source file (`SOUL.md`,
`AGENTS.md`, …), its `.compressed.md` sibling goes **stale**, the read path
silently reverts to the verbose original (correct, but the savings are lost),
and it stays that way until you remember to re-run `dn compress` by hand.

The original compress-aware brainstorm always assumed "cron/offline regen"; it
was never built. This spec builds the automatic-regen piece — but does it
through a **general internal hook system** (a reusable lifecycle seam), with
session-end auto-regen as that system's first consumer. A follow-on issue will
add a user-configurable shell-hook layer on top.

## Goals / non-goals

**Goals**
- A small, internal Python **hook registry** with two lifecycle events
  (`session_start`, `session_end`) fired from the TUI.
- A first consumer, `auto_regen`, that on `session_end` **refreshes stale
  compressed siblings that already exist** — never creating new ones — off the
  hot path, never blocking quit.
- Documentation of the hook system (`docs/hooks.md`) as a first-class
  deliverable.

**Non-goals (explicitly deferred)**
- **User-configurable shell hooks** (Claude-Code-style commands in config). The
  event names + payload dict are designed so this bolts on later; a follow-on
  issue tracks it.
- **Turn-level events** (`turn_start`/`turn_end`). Add when a consumer needs
  them.
- **Creating siblings that don't exist** (auto-compress-everything). Auto-regen
  keeps opted-in siblings fresh; creating a sibling stays an explicit
  `dn compress <path>` action ("presence = opt-in").
- A new on/off setting. Auto-regen is gated on the existing `compress_aware`
  flag.

## Decisions (resolved during brainstorm)

| Question | Decision |
| --- | --- |
| Who registers hooks? | **Internal Python registry** now; user-config shell hooks deferred to a follow-on issue. |
| Which events? | **`session_start` + `session_end`** only. |
| Registry process? | **TUI process.** Regen runs **in-process → spawns a detached worker**; no cross-process RPC. |
| Scan scope? | **Existing siblings only** — regen the stale ones, never create new. |
| Background behavior? | **Detached, fire-and-forget, logged.** Quit is always instant. |
| Error policy? | A raising hook is **caught + logged, never breaks** the session or other hooks. |
| Toggle? | **No new knob** — gated on the existing `compress_aware` flag. |
| Registry shape? | **Module-level registry** (Option A): self-registering consumers; `app.py` stays ignorant of compression. |

## Architecture

```
harness/hooks.py                  NEW — generic registry: register / on / dispatch / clear
harness/compress/auto_regen.py    NEW — first consumer: regen stale existing siblings on session_end
harness/compress/regen_worker.py  NEW — detached child entrypoint (python -m harness.compress.regen_worker <paths…>)
harness/compress/targets.py       NEW — per-persona/cwd source walk + "existing-sibling, stale?" selection
harness/compress_cli.py           EDIT — reuse targets.py for a shared regen helper (and item-B groundwork)
harness/tui/app.py                EDIT — dispatch("session_start") in on_mount, dispatch("session_end") in on_unmount
docs/hooks.md                     NEW — hook-system reference (events, payloads, how to add a consumer)
docs/compress-aware.md            EDIT — note siblings auto-refresh on session end
```

**Responsibilities & boundaries**

- **`hooks.py`** — generic pub/sub. Knows nothing about compression. Pure
  registry + isolated dispatch. This is the reusable seam.
- **`targets.py`** — the file-discovery unit. Walks persona workspaces
  (`persona_select.resolve_workspace` / `paths.default_workspace_dir`) for
  SOUL/IDENTITY/USER/MEMORY plus cwd AGENTS/CLAUDE, and selects the subset that
  (a) **already has a sibling** and (b) is **stale**. Pure file I/O, no model,
  never raises on a bad file. Shared by `auto_regen`, `regen_worker`, and (as
  groundwork for handoff item B) `compress_cli`.
- **`auto_regen.py`** — the policy/consumer. Registers for `session_end`;
  gates on `compress_aware`; uses `targets.py` to find stale existing siblings;
  if any, spawns `regen_worker` detached; logs; never raises, never blocks.
- **`regen_worker.py`** — the detached child. Resolves the compress model and
  rebuilds exactly the source paths handed to it (via the existing
  `compress_cli.rebuild_one`), then exits. Lives in its own module so the
  detached invocation is `python -m harness.compress.regen_worker <paths…>`
  (clean — does NOT shell `dn compress`, which would relaunch the TUI argparse).
- **`app.py`** — learns only the two `hooks.dispatch(...)` lines. Never imports
  compression; the consumer self-wires via import.

**Self-registration.** `auto_regen` registers itself for `session_end` at
import. The TUI startup imports `harness.compress.auto_regen` once, at a single
deterministic point (specified in the plan — co-located with the existing TUI
startup wiring, not scattered import side-effects), so registration is
one-time and order-deterministic.

## The registry (`harness/hooks.py`)

```python
def register(event: str, handler, *, label: str | None = None) -> None
def on(event: str, *, label: str | None = None)            # decorator form of register
def dispatch(event: str, *, tracer=None, **payload) -> None  # fire-and-forget, never raises
def clear(event: str | None = None) -> None                  # test-only reset
```

**Dispatch semantics (load-bearing):**
- Calls every handler registered for `event`, **in registration order**.
- Each handler runs in `try/except Exception`: on error, log via
  `tracer.emit("dn", "hook.error", event=event, label=label, error=str(e))`
  when a `tracer` is passed, then **skip** to the next handler. A raising
  handler never breaks the session or the other handlers.
- `dispatch` itself **never raises**. An event with no handlers is a silent
  no-op.
- `**payload` is the forward-compat contract — exactly what a future shell-hook
  layer serializes to a subprocess as JSON.

**Module-global state** is correct for this single process. `clear()` exists so
tests start from a clean registry.

## Events

| Event | Fired at | Payload (besides `tracer`) |
| --- | --- | --- |
| `session_start` | TUI `on_mount`, after agent connect succeeds | `cwd: str`, `persona_id: str \| None` |
| `session_end` | TUI `on_unmount`, before the tracer is closed | `cwd: str`, `persona_id: str \| None` |

- `session_start` has **no consumer today** — it is the symmetric anchor. Both
  payloads are identical so any file-touching consumer (and the future shell
  layer) can resolve workspace paths from either.
- `persona_id` is sourced from the TUI's tracked persona if cleanly available
  at unmount; otherwise `None`. Auto-regen does **not** depend on it (it walks
  all persona workspaces with siblings), so `None` is safe — it's robustness,
  not a precondition.
- `session_end` is dispatched **before** `self._tracer.close()` so the tracer
  is still live to record `hook.error` / regen-spawn breadcrumbs.

## The `auto_regen` consumer

On `session_end(tracer, cwd, persona_id)`:

1. **Gate.** If compress-aware is off, return immediately. (`compress_aware` is
   per-persona, read via the same `_compress_on(workspace_dir)` logic the read
   path uses — `persona.py:29` / `agents.py:20`. Auto-regen reuses that, so a
   persona with compression off is skipped.)
2. **Discover (via `targets.py`).** Collect candidate sources across persona
   workspaces + cwd; keep only those with an **existing** `.compressed.md`
   sibling. Files without a sibling are never touched.
3. **Filter to stale.** For each existing sibling run `sibling.freshness(src,
   sib)` (pure file I/O, no model). Keep the non-`fresh` ones (`stale` /
   `corrupt` / source-edited). If none → return **without importing litellm or
   building a model client** (the common, zero-cost case).
4. **Regen detached.** If there are stale targets, spawn a detached child
   (`subprocess.Popen([sys.executable, "-m", "harness.compress.regen_worker",
   *paths], start_new_session=True, stdout=DEVNULL, stderr=<log fd>,
   close_fds=True)` — mirroring `jobs/supervisor.py:28`). The TUI returns
   immediately; quit is always instant.
5. **Log, never surface.** Spawn success/failure → trace/log only. A failed or
   interrupted regen leaves the sibling stale (self-heals next session). Never
   user-facing, never blocks quit.

**Correctness guards (hard-won, from the handoff):**
- **Read path never raises** — `targets.py` treats `OSError` /
  `UnicodeDecodeError` on a sibling as "needs rebuild / skip", never crashes.
- **No new siblings ever** — step 2 is the guard; this is the one behavior that
  distinguishes auto-regen from `dn compress <path>`.
- **Cheap when clean** — step 3 short-circuits before any model import when
  nothing is stale.
- **`RULES_VERSION`** unchanged here (no prompt edits), so existing siblings'
  freshness is unaffected by this change.

## The detached worker (`harness/compress/regen_worker.py`)

```
python -m harness.compress.regen_worker <source-path> [<source-path> …]
```

- Loads `.env` (`paths.load_env(os.getcwd())`) so the model resolves, exactly
  like `compress_cli.run` does.
- Resolves the model (`compress_cli._build_call_model()`); if unavailable,
  logs and exits 0 (compression unavailable is not an error here).
- For each path, calls `compress_cli.rebuild_one(path, call_model=…,
  today=…)`; logs the per-file result.
- Exits 0 regardless of per-file failures (best-effort; the child is detached
  and unobserved).

## Shared regen helper / item-B groundwork (`compress_cli.py`)

`targets.py` provides the per-persona/cwd walk that handoff **item B** wants for
`_default_targets()`. This spec builds the walk in `targets.py` and has
`auto_regen` + `regen_worker` use it. Wiring it into `compress_cli`'s
`_default_targets()` (so `dn compress` with no args covers persona files) is a
small additional edit included in this work — it reuses the same function, no
new machinery.

## Error handling (consolidated)

- `hooks.dispatch` never raises; handlers isolated + logged.
- `auto_regen` never surfaces a user error, never blocks quit; failures leave a
  stale sibling (self-heals).
- `targets.py` never raises on a bad file.
- `regen_worker` is best-effort, exits 0.
- All of this matches the existing "never let this break boot/exit" discipline
  in `on_mount` (`app.py:341`, cron autostart wrapped in try/except) and
  `on_unmount` (`app.py:1680`, teardown logged not raised).

## Testing

- **`tests/test_hooks.py`** — registration order preserved; a raising handler
  is isolated (later handlers still run; dispatch returns normally);
  `tracer.emit("dn","hook.error",…)` called on handler error; unknown event =
  no-op; `clear()` resets one event / all events.
- **`tests/compress/test_targets.py`** — selects a source that has a stale
  sibling; **ignores a source with no sibling** (opt-in guard); **skips a fresh
  sibling**; a corrupt sibling is selected (treated as needs-rebuild) and does
  not crash; respects per-persona `compress_aware` off.
- **`tests/compress/test_auto_regen.py`** — gated off when `compress_aware`
  false (no spawn); **no stale → model client never built** (assert a fake
  resolver is NOT called) and **no spawn**; **stale present → detached spawn
  called with exactly the stale paths** (mock `subprocess.Popen`, assert argv);
  a handler exception is contained (does not propagate to dispatch).
- **`tests/compress/test_regen_worker.py`** — given paths, calls `rebuild_one`
  per path; model-unavailable → exits 0, no crash; one path failing does not
  stop the others.
- **TUI dispatch** (fold into existing TUI tests or new
  `tests/test_tui_hooks.py`) — `on_mount` dispatches `session_start` with
  `cwd`/`persona_id`; `on_unmount` dispatches `session_end` before tracer
  close; a raising hook does **not** break unmount/teardown.
- **Run the FULL suite before final review** — new modules can trip exact
  inventory tests (handoff lesson). Known-green baseline: the pre-existing
  `test_service_launchd` failure + flaky Textual-Pilot cluster are not
  regressions.

## Documentation (first-class deliverable)

`docs/hooks.md` must cover, and is only "done" when it covers, all four:
1. **What it is** (internal Python registry, single TUI process) and what it is
   **not yet** (no user-config shell hooks — link the follow-on issue).
2. **Event catalog** — table of `session_start` / `session_end` with fire-point
   and payload fields, kept in sync with the code.
3. **How to add a consumer** — the `hooks.register("session_end", fn)` pattern,
   the never-raise/isolation contract a handler must honor, and the
   self-register-at-import convention.
4. **Worked example** — `auto_regen` documented as the reference consumer.

`docs/compress-aware.md` gains a short note: siblings auto-refresh on session
end (cross-ref `docs/hooks.md`); manual `dn compress` still works and is needed
to **create** a sibling.

## Follow-on issue (file at the end)

**"User-configurable shell hooks"** — let `dn` users declare shell commands
against these lifecycle events (config schema, shell exec, matchers, timeouts,
security model). The internal registry's event names + payload dict are
designed to be exactly what that layer serializes to JSON.

## Rollout / safety

- Additive: no behavior changes when `compress_aware` is off or no siblings
  exist. A session with zero stale siblings does zero extra work (and never
  imports litellm).
- Reversible: deleting a sibling opts out of regen for that file; turning off
  `compress_aware` disables regen entirely.
- Quit latency unaffected (detached spawn returns immediately).
