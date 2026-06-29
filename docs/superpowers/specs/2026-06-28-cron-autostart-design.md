# TUI auto-starts a single-instance harness-cron daemon — design

**Status:** approved (brainstorming) — ready for plan
**Issue:** completes the auto-start half of [#146](https://github.com/albertovasquez/done/issues/146)
**Date:** 2026-06-28
**Builds on:** the daemon-liveness heartbeat shipped in #152 (`harness/jobs/heartbeat.py`).

## Problem

The `harness-cron` daemon fires scheduled jobs, but a user must start it by hand;
if they don't, jobs silently never run. #152 made the *state* visible (the
`Ctrl+J` header). This makes it **automatic**: every `done` launch ensures a
daemon is running, so jobs fire for any user without a manual step.

The hard requirement is **single-instance**: a user commonly opens more than one
`done` window, and naive auto-start would spawn one daemon per window. Two
daemons is not merely wasteful — it is a **correctness bug**: `ops.run`
(`harness/jobs/ops.py:42`) does read (`get`) → execute → write (`store.mutate`)
*without holding the store lock across the whole sequence*, so two daemons can
both read the same due job before either advances `next_run_at` and **both
execute it** (double LLM turn, double side effects). Auto-start is therefore only
safe if exactly one daemon can ever run.

## Decisions (from brainstorming)

- **Always on, no config switch.** Simplest path; documented loudly instead of
  gated. Headless/CI/server concerns are explicitly deferred (see YAGNI).
- **Single-instance via an OS-atomic lockfile**, owned by the daemon itself.
- **Detached spawn** so the daemon outlives the `done` window that started it
  (and every other window).

## Architecture

Three small units, each independently testable, plus two integration edits.

### 1. The lock — `harness/jobs/lock.py` (new)

A single-instance lock under `cron_dir()`, claimed atomically.

```
LOCK_FILE -> cron_dir() / "daemon.lock"     # computed at call time, like heartbeat.py

acquire(*, pid=None, pid_alive=_pid_alive) -> bool
    # Atomic claim via os.open(LOCK_FILE, O_CREAT | O_EXCL | O_WRONLY).
    #  - create succeeds  -> write str(pid or os.getpid()); return True (we own it)
    #  - EEXIST           -> read the stored pid:
    #       pid_alive(stored) True  -> return False (another live owner)
    #       pid_alive(stored) False -> stale (crash); os.replace a fresh claim, return True
    # pid_alive is injected so tests never depend on real process liveness.

release() -> None
    # Best-effort unlink; swallow if already gone. Called by the lock holder on exit.
```

The stored-pid read lives **inside** `acquire`'s stale check — there is no public
`owner_pid()` in this PR (YAGNI: nothing here consumes it; #146's future
"stop the daemon" can add it when it needs to target the owner).

`O_CREAT|O_EXCL` is atomic at the OS level: when two processes race the create,
exactly one wins and the other gets `EEXIST`. This is a true lock, not a
check-then-act. The stale-reclaim path (only reached when a prior owner crashed)
writes via `os.replace` (atomic) so a reader never sees a torn pid, then
**re-reads and confirms our pid is the stored one** — so if two daemons recover
from the same crash simultaneously, the last `os.replace` wins and the other's
re-read returns False. This closes the post-crash double-reclaim window noted in
review; the fast (uncontended) path was already atomic via `O_EXCL`.

`_pid_alive(pid)`: `os.kill(pid, 0)` → `True`; `ProcessLookupError` → `False`;
`PermissionError` → `True` (pid exists, owned by another user — treat as alive).

### 2. The daemon owns the lock — `harness/jobs/cron_main.py` (modify)

`harness-cron` (the continuous path) wraps `run_forever` in the lock:

```
if args.once:
    tick(now=time.time())
    record_heartbeat(success=True)
    return 0

if not lock.acquire():
    logger.info("another harness-cron already holds %s — exiting", lock.LOCK_FILE)
    return 0                      # not an error: single-instance working as intended
try:
    asyncio.run(run_forever(interval=args.interval, clock=time.time, sleep=asyncio.sleep))
finally:
    lock.release()
```

Consequences:
- `harness-cron` is single-instance **by itself** — run twice by hand, by a
  launchd unit, or by two TUIs → exactly one survives, the rest exit 0.
- `--once` does **not** take the lock (no loop to guard; it is a one-shot fire).
- A clean exit and SIGINT (KeyboardInterrupt propagates through `asyncio.run` →
  `finally` runs → `release()`). **SIGTERM and SIGKILL do NOT run `finally`** —
  Python does not turn a default SIGTERM into an exception, so the lock is left
  behind; the next daemon reclaims it via the dead-pid stale path. We deliberately
  rely on stale-reclaim for SIGTERM/SIGKILL rather than installing a signal
  handler (simpler; reclaim is already needed for crashes/power-loss anyway).

### 3. The supervisor — `harness/jobs/supervisor.py` (new)

Trivial, because the daemon guarantees singleness; the supervisor only avoids
spawning a doomed-to-self-exit process in the common case.

```
ensure_daemon_running(*, spawn=_spawn_detached, now=None) -> str
    age_hb = heartbeat.heartbeat_age(now)
    age_ok = heartbeat.success_age(now)
    if heartbeat.daemon_status(age_hb, age_ok, interval=daemon.DEFAULT_INTERVAL) == "running":
        return "already-running"          # fresh heartbeat → a daemon is alive; do nothing
    try:
        spawn()
        return "spawned"
    except Exception:
        logger.exception("cron autostart spawn failed")
        return "failed"                   # best-effort: never propagate

_spawn_detached() -> None
    # Detached so the daemon outlives this done window (and every other).
    # -m harness.jobs.cron_main (NOT the 'harness-cron' script) → PATH-independent,
    # mirrors how the TUI already spawns the agent (sys.executable -m harness.acp_main).
    cron_dir().mkdir(parents=True, exist_ok=True)
    # Open the log fd, hand it to the child, then CLOSE it in the parent — Popen
    # dups it into the child, so the parent must not leak its own handle.
    log_fd = open(cron_dir() / "daemon.log", "a")
    try:
        subprocess.Popen(
            [sys.executable, "-m", "harness.jobs.cron_main"],
            start_new_session=True,       # POSIX setsid → survives parent exit
            stdout=subprocess.DEVNULL,
            stderr=log_fd,                # crash output is post-mortem-able
            close_fds=True,
        )
    finally:
        log_fd.close()                    # child keeps its dup; parent must not leak
```

If two windows both see "stopped" and both spawn, both spawned daemons race the
lock; exactly one wins, the other exits 0. The heartbeat check is an
optimization; the lock is the guarantee.

### 4. TUI hook — `harness/tui/app.py` `on_mount` (modify)

One best-effort call after `_connect()`, in its own try/except so a spawn failure
logs (`cron.autostart.failed` via the tracer, when present) and never breaks
boot — mirrors the existing `spawn.failed` handling at `app.py:349`. `on_mount`
runs once per `done` process (verified: reconnect calls `_connect`, not
`on_mount`), so this fires once per window.

```
try:
    from harness.jobs.supervisor import ensure_daemon_running
    ensure_daemon_running()
except Exception as e:
    self.log(f"cron autostart skipped: {e!r}")
    if self._tracer is not None:
        self._tracer.emit("dn", "cron.autostart.failed", error=str(e))
```

## Data flow

```
done boot → on_mount → ensure_daemon_running()
   ├─ heartbeat fresh → "already-running" (stop)
   └─ else → spawn detached `python -m harness.jobs.cron_main`
                └─ daemon: lock.acquire() ?
                     ├─ won  → record_heartbeat + run_forever (… release on exit)
                     └─ lost → log + exit 0
Ctrl+J header reads the heartbeat → ✓ running
```

## Error handling

- **Spawn** (no python / sandbox / fs): caught in supervisor → `"failed"` +
  logged; TUI boot unaffected; header still shows `✗ not running` so the user is
  not misled.
- **Lock contention**: `EEXIST` is normal, not an error — the loser exits 0.
- **Stale lock** (crash): reclaimed by the next daemon via the dead-pid path.
- **Lock fs failure** (unwritable `cron_dir`): `acquire` lets the OSError
  propagate to `cron_main`, which already runs under the daemon process; it logs
  and exits non-zero. The TUI is unaffected (it only spawned).
- Best-effort everywhere on the TUI side: the TUI must boot even if no daemon can
  start.

## Testing

- `lock.py`:
  - `acquire` on a free path → True, file holds our pid.
  - second `acquire` with `pid_alive=lambda _: True` → False (live owner).
  - `acquire` over a stale lock (`pid_alive=lambda _: False`) → True, pid replaced.
  - `release` removes the file; `release` when already gone → no raise.
  - `owner_pid` parses a good file; returns None on missing/garbled.
- `cron_main`:
  - lock held by a live pid → `main([])` returns 0 WITHOUT entering `run_forever`
    (monkeypatch `run_forever` to assert-not-called).
  - free lock → `run_forever` invoked, lock released after (monkeypatch
    `run_forever` to return immediately; assert `lock.owner_pid()` is None after).
  - `--once` path does NOT acquire the lock (assert lock file absent after).
- `supervisor.py`:
  - heartbeat fresh → `"already-running"`, `spawn` not called.
  - heartbeat stale/missing → `"spawned"`, `spawn` called once.
  - `spawn` raises → `"failed"`, no exception.
  - `_spawn_detached`: monkeypatch `subprocess.Popen`; assert argv ==
    `[sys.executable, "-m", "harness.jobs.cron_main"]`, `start_new_session=True`,
    DEVNULL stdout. Never forks a real process.
- TUI (`tests/jobs/test_cron_drawer_mount.py` or the app mount test):
  - `on_mount` calls `ensure_daemon_running` once (monkeypatch it to record).
  - a raising `ensure_daemon_running` does not break boot (app still mounts).

All tests use the existing `_cron_dir` fixture (`monkeypatch harness.paths.config_dir
→ tmp_path`) so locks/heartbeats/logs land in tmp, never the real config dir, and
never a real subprocess.

## Files

- **New:** `harness/jobs/lock.py`, `harness/jobs/supervisor.py`,
  `tests/jobs/test_lock.py`, `tests/jobs/test_supervisor.py`.
- **Modify:** `harness/jobs/cron_main.py` (lock around `run_forever`),
  `harness/tui/app.py` (`on_mount` call), `tests/jobs/test_cron_main.py` +
  the TUI mount test.
- **Docs:** README **Jobs** section + `docs/jobs.md` —
  "`done` auto-starts the daemon; it runs in the background and keeps running
  after you close `done`," with the headless/server caveat. A code comment in
  `supervisor.py` and at the `on_mount` call stating the deliberate
  detach-and-outlive + single-instance behavior and the future concern.

## YAGNI / deferred (still #146)

- **No config off-switch** — always on, documented. (If headless/server use
  appears, add a `[jobs] autostart` key then.)
- **No stop-from-TUI** — the lock now records the owner pid, which a future
  "stop the daemon" can target.
- **No headless auto-detect** — a server/CI run of `done` will still spawn a
  daemon; flagged as the first thing to revisit.
- **No cross-host locking** — the lock is local-fs only (correct: jobs and the
  daemon are per-machine).
