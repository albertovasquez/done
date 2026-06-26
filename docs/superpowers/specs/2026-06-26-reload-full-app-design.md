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

**In `tui_main.main()`** — reconstruct the relaunch argv and re-exec after the app
returns:
```python
def _relaunch_argv(args, cwd) -> list[str]:
    """The exact command to re-launch THIS TUI with the same flags."""
    argv = [sys.executable, "-m", "harness.tui_main",
            "--model", args.model, "--cwd", cwd]
    if args.yolo:
        argv.append("--yolo")
    return argv

def main(argv=None) -> None:
    ...                                    # existing parse + paths.load_env + agent_cmd
    app = HarnessTui(agent_cmd=agent_cmd, cwd=cwd, model=args.model)
    app.run()
    if getattr(app, "_reexec", False):
        relaunch = _relaunch_argv(args, cwd)
        try:
            os.execv(relaunch[0], relaunch)   # replaces the process; never returns on success
        except OSError as e:
            print(f"reload failed to re-exec: {e}", file=sys.stderr)
            sys.exit(1)
```

The relaunch argv is reconstructed from the **parsed args**, not raw `sys.argv`
(so it is correct whether launched as `done` or `python -m harness.tui_main`). It
reuses the same shape `tui_main` already builds for the agent command — the TUI is
relaunched with `python -m harness.tui_main` + the same `--model/--cwd/--yolo`.

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

- **`_relaunch_argv` (pure helper)** — unit tests: mock vs vibeproxy; with and
  without `--yolo`; cwd is passed through. Asserts the exact command list.
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

## Out of scope (follow-up)

- Carrying runtime state (the `/models` selection, conversation) across the
  `/reload` re-exec — deliberately rejected: a code reload starts clean from launch
  defaults.
- Hot-reloading TUI modules in place (no process replacement) — not feasible
  cleanly in Python for a running Textual app; re-exec is the mechanism.
- A separate third command (keeping an agent-only reload **and** a full re-exec) —
  the chosen mapping folds agent-respawn into `/clear`, so no third command.
