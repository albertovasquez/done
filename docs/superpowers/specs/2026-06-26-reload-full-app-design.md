# `/reload` = full-app re-exec; `/clear` = fresh conversation + agent respawn — design

**Date:** 2026-06-26
**Branch:** `reload-full-app`
**Status:** approved design, ready for implementation plan

## Problem

`done` runs as **two processes**:

- the **TUI process** (`harness/tui_main.py` → `HarnessTui` in `harness/tui/app.py`),
  which owns all UI code: `harness/tui/header.py`, `app.py`, the slash menu, rendering;
- the **agent subprocess** (`python -m harness.acp_main`, spawned by the TUI via
  `spawn_agent_process`), which owns `acp_main.py`, `acp_agent.py`, `router.py`,
  `skills.py`, the model.

The shipped `/reload` (PR #8) restarts **only the agent subprocess**. So when a
developer edits **TUI** code (e.g. `header.py`) and runs `/reload`, nothing
changes — the edited module is already imported into the running TUI process, and
Python cannot hot-swap an imported module. The only way to pick up TUI code edits
today is to quit and relaunch. That mismatch is the bug being fixed.

## Goal

Make the commands match developer intent:

- **`/reload`** reloads **all** code — TUI *and* agent — by re-exec'ing the whole
  `done` process (`os.execv`). Resets everything to launch defaults, like
  relaunching with the same flags, but without leaving the terminal.
- **`/clear`** becomes "fresh start, same app": reset the conversation **and**
  respawn the agent subprocess (what `/reload` did before PR #8's split). The TUI
  process keeps running.

This is a behavior change to the two commands shipped in PR #8.

## Two processes, one TTY

`os.execv` **replaces the current process image in place**, inheriting the exact
same controlling terminal — so it is the right primitive for "reload the whole
app without leaving the terminal." The hard constraint: it must run **only after
Textual has fully exited and restored the terminal**, and after the agent
subprocess is dead — otherwise the new process inherits a terminal still in raw
mode / alt-screen, or an orphaned agent.

Verified facts the design rests on:
- `App.exit(result=None, return_code=0, message=None)` causes `App.run()` to
  return; Textual restores the terminal on exit.
- `HarnessTui.on_unmount` (already present, `app.py:677-684`) tears down the agent
  subprocess via `self._cm.__aexit__`, so the agent dies when the TUI exits — no
  orphan.
- Therefore the safe re-exec point is **`tui_main.main()`, after
  `HarnessTui(...).run()` returns** — terminal restored, agent dead.

## Design

### 1. Command behaviors

| Command | Conversation | Agent subprocess | TUI process | Model selection |
|---|---|---|---|---|
| **`/clear`** | reset (empty) | **respawned** | kept | re-applied on reconnect |
| **`/reload`** | reset (relaunch) | replaced (re-exec) | **replaced (re-exec)** | reset to launch default |

### 2. `/clear` — fresh conversation + agent respawn (`harness/tui/app.py`)

`/clear` takes over the **current `action_reload` body** (the agent-respawn
logic): guard on `_busy` → `_cancel_inflight()` → `_reset_conversation()` →
(transient "— clearing… —" line when `_started`) → `_teardown()` → try
`_connect()` + re-wipe on success / `except → _fatal` on failure → release `_busy`
in `finally`.

The old lightweight `action_clear` (which only called `_new_session()` on the
live subprocess) is **replaced** by this respawn version. (`_new_session` remains
a method, still used internally by `_connect`.)

Wording: the transient progress line reads "— clearing… —" (was "— reloading
agent… —"). Success ends with an empty transcript (re-wipe); failure leaves the
line + `_fatal` error visible — same shape as the shipped reload.

### 3. `/reload` — full-app re-exec (`harness/tui/app.py` + `harness/tui_main.py`)

**In the app:**
```python
async def action_reload(self) -> None:
    if self._busy:
        return
    self._busy = True
    self._reexec = True          # main() reads this after run() returns
    self.exit()                  # Textual restores the terminal; run() returns
```
No teardown here — `on_unmount` kills the agent. `_busy` is set so a concurrent
lifecycle command can't interleave during the exit; it is never released (the
process is about to be replaced), which is fine. `self._reexec` defaults to
`False` in `__init__`.

`action_reload` deliberately does **not** call `_cancel_inflight()` (unlike
`/clear`). An in-flight `_send_prompt` worker is cancelled by Textual's shutdown
(`workers.cancel_all()`); its `CancelledError` is a `BaseException`, so it bypasses
the worker's `except Exception` (no spurious "agent disconnected" line), and
`on_unmount` force-kills the agent regardless — verified safe by the design review.
It may optionally call `_cancel_inflight()` for symmetry, but it is not
load-bearing.

**In `tui_main.main()`** — reconstruct the relaunch command and re-exec after the
app returns. **The real launcher is the console script `dn`** (`pyproject.toml`
`[project.scripts] dn = "harness.tui_main:main"`), not `python -m
harness.tui_main` — so prefer re-exec'ing the **actual launcher** (`sys.argv[0]`,
i.e. the `dn` executable) when it is a real, executable path, and fall back to the
`-m` form otherwise. This is both faithful (preserves `argv[0]` = `…/bin/dn`) and
robust against any wrapper/shim that injects environment before `main()`:

```python
def _relaunch_args(args, cwd) -> list[str]:
    """The flags to re-launch THIS TUI with, reconstructed from parsed args
    (not raw sys.argv) so they are correct regardless of how it was invoked."""
    flags = ["--model", args.model, "--cwd", cwd]      # always explicit --cwd
    if args.yolo:
        flags.append("--yolo")
    return flags

def _relaunch_command(args, cwd) -> list[str]:
    """argv[0] for execv + the relaunch flags. Prefer the original launcher
    (the `dn` console script at sys.argv[0]); fall back to `python -m`."""
    launcher = sys.argv[0]
    flags = _relaunch_args(args, cwd)
    if launcher and os.path.isfile(launcher) and os.access(launcher, os.X_OK):
        return [launcher, *flags]                      # re-exec `dn` faithfully
    return [sys.executable, "-m", "harness.tui_main", *flags]   # path-equivalent fallback

def main(argv=None) -> None:
    ...                                    # existing parse + paths.load_env + agent_cmd
    app = HarnessTui(agent_cmd=agent_cmd, cwd=cwd, model=args.model)
    app.run()
    if getattr(app, "_reexec", False):
        cmd = _relaunch_command(args, cwd)
        try:
            os.execv(cmd[0], cmd)          # replaces the process; never returns on success
        except OSError as e:
            print(f"reload failed to re-exec: {e}", file=sys.stderr)
            sys.exit(1)
```

`os.execv` preserves the process cwd and the full environment, and the `--cwd`
flag is **always passed explicitly** (the app keys off `self.cwd`/`--cwd`, never
process-cwd, so omitting it could silently switch projects — verified). The flags
are reconstructed from the **parsed args**, not raw `sys.argv`, so they are correct
whichever way `done`/`dn` was launched.

### 4. Registry (`harness/tui/commands.py`)

Descriptions updated to match the new behaviors (handlers already delegate to
`action_reload`/`action_clear`):
- `reload` → "Reload everything (restart the app)"
- `clear` → "Fresh conversation (restart the agent)"

### 5. Error handling

- **`/reload` re-exec fails** (`OSError` from `execv`): the terminal is already
  restored (Textual exited), so `main()` prints a plain error to stderr and exits
  1 — the developer lands at a normal shell, not a broken TUI.
- **`/clear` agent respawn fails**: `_fatal` (app stays alive, input disabled) —
  identical to the shipped reload-failure path.
- Both guarded by `_busy`. `_reexec` is only set under the guard.

### 6. Testing

`/reload`'s full path cannot be exercised end-to-end (a pilot test cannot
`os.execv` itself). The logic is split into independently testable pieces:

- **`_relaunch_args` / `_relaunch_command` (pure helpers)** — unit tests: mock vs
  vibeproxy; with and without `--yolo`; `--cwd` always passed through; and the
  launcher selection — when `sys.argv[0]` is an executable file it is used as
  `argv[0]`, else the `python -m harness.tui_main` fallback. Asserts the exact
  command list for each case (monkeypatch `sys.argv[0]` / `os.access`).
- **`action_reload` sets the intent** — pilot test: calling `action_reload()` sets
  `app._reexec is True` and the app exits (no `os.execv` is invoked from the test).
- **`main()` re-exec branch** — test with `os.execv` monkeypatched to record its
  call: after a run where `app._reexec` is True, `main()` calls `os.execv` with the
  reconstructed argv; and the `OSError` path prints an error + exits 1 (assert via
  `SystemExit`).
- **`/clear` respawns the agent** — repurpose the shipped reload tests: `/clear`
  now bumps the generation and spawns a new OS process (fake-agent start marker),
  and resets the conversation; `_busy` released; failure path shows `_fatal`.
- The existing reload/clear/lifecycle tests are updated to the new semantics; the
  full suite stays green.

## Design review (2026-06-26)

Adversarial review against Textual 8.2.7 and the real code confirmed the load-bearing
sequencing and surfaced one gap (now folded in above):

- **Shutdown ordering is safe.** `app.exit()` is non-blocking (posts `ExitApp`).
  On shutdown Textual restores the terminal (`driver.stop_application_mode`) and
  then awaits `_shutdown` → `on_unmount` (which kills the agent via
  `_cm.__aexit__`) — **both** complete before `run()` returns. So the re-exec point
  (`main()` after `run()`) has a restored terminal AND a dead agent. No raw-mode
  inheritance, no orphaned agent.
- **In-flight worker on `/reload`** — safe without `_cancel_inflight` (CancelledError
  bypasses `except Exception`; `on_unmount` kills the agent).
- **`_busy` guard is atomic** (no `await` between check and set) → double-`/reload`
  and `/reload`-during-`/clear` are safe no-ops; `app.exit()` is idempotent.
- **`/reload` from the landing screen** is safe (the trivial body touches no
  started-only widget).
- **cwd is consistent** across `execv` (app keys off `self.cwd`/`--cwd`, recomputed
  identically; explicit `--cwd` pass-through retained).
- **Gap fixed:** the real launcher is the `dn` console script, not `python -m`;
  the design now prefers re-exec'ing `sys.argv[0]` (the launcher) with a `-m`
  fallback (§3).

## Out of scope (follow-up)

- Carrying runtime state (the `/models` selection, conversation) across the
  `/reload` re-exec — deliberately rejected: a code reload starts clean from launch
  defaults.
- Hot-reloading TUI modules in place (no process replacement) — not feasible
  cleanly in Python for a running Textual app; re-exec is the mechanism.
- A separate third command (keeping an agent-only reload **and** a full re-exec) —
  the chosen mapping folds agent-respawn into `/clear`, so no third command.
