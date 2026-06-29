# TUI cron-daemon auto-start — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `done` auto-starts exactly one detached `harness-cron` daemon on launch, so scheduled jobs fire for any user without a manual step — guaranteed single-instance across multiple `done` windows via an OS-atomic lockfile.

**Architecture:** A new `lock.py` (O_EXCL daemon.lock under cron_dir, daemon-owned) makes `harness-cron` single-instance; `cron_main` acquires it around `run_forever`; a trivial `supervisor.py` spawns a detached daemon when the heartbeat says none is running; `app.on_mount` calls the supervisor best-effort.

**Tech Stack:** Python 3.11, Textual, pytest. No new dependencies.

## Global Constraints

- Test command (from worktree root): `.venv/bin/python -m pytest tests/ -q`.
- POSIX only (macOS/Linux); no Windows paths.
- Lock is OS-atomic (`os.open` with `O_CREAT|O_EXCL|O_WRONLY`) — a true lock, not check-then-act.
- Daemon owns the lock: acquire at startup, release on clean exit / SIGINT via `finally`. SIGTERM/SIGKILL rely on stale-reclaim (dead-pid path) — NO signal handler.
- `--once` does NOT acquire the lock.
- Spawn is detached (`start_new_session=True`, stdout=DEVNULL, stderr→`cron/daemon.log` with the parent fd closed after Popen) and uses `[sys.executable, "-m", "harness.jobs.cron_main"]` (PATH-independent).
- All supervisor/TUI paths are best-effort: a spawn failure logs and never breaks boot.
- No public `owner_pid()` (YAGNI); the stored-pid read lives inside `acquire`'s stale check.
- Tests use the existing `_cron_dir` fixture (`monkeypatch harness.paths.config_dir → tmp_path`); never fork a real subprocess (inject/monkeypatch `spawn`/`Popen`).

---

## File Structure

- **New:** `harness/jobs/lock.py` — single-instance lock (acquire/release + injected pid-liveness).
- **New:** `harness/jobs/supervisor.py` — `ensure_daemon_running` + `_spawn_detached`.
- **New:** `tests/jobs/test_lock.py`, `tests/jobs/test_supervisor.py`.
- **Modify:** `harness/jobs/cron_main.py` — acquire lock around `run_forever`; skip-if-locked.
- **Modify:** `harness/tui/app.py` — `on_mount` best-effort `ensure_daemon_running()`.
- **Modify tests:** `tests/jobs/test_cron_main.py`, the TUI mount test (`tests/jobs/test_cron_drawer_mount.py`).
- **Docs:** `README.md`, `docs/jobs.md`.

---

### Task 1: The single-instance lock

**Files:**
- Create: `harness/jobs/lock.py`
- Test: `tests/jobs/test_lock.py`

**Interfaces:**
- Consumes: `harness.jobs.paths.cron_dir()`.
- Produces:
  - `lock_file() -> Path` (computed at call time, like heartbeat.py)
  - `acquire(*, pid: int | None = None, pid_alive=_pid_alive) -> bool`
  - `release() -> None`
  - `_pid_alive(pid: int) -> bool`

- [ ] **Step 1: Write the failing tests**

```python
# tests/jobs/test_lock.py
import os
import pytest
from harness.jobs import lock


@pytest.fixture(autouse=True)
def _cron_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("harness.paths.config_dir", lambda: tmp_path)
    return tmp_path


def test_acquire_on_free_path_writes_pid(_cron_dir):
    assert lock.acquire(pid=4242) is True
    assert lock.lock_file().read_text().strip() == "4242"


def test_second_acquire_with_live_owner_fails(_cron_dir):
    assert lock.acquire(pid=4242, pid_alive=lambda p: True) is True
    assert lock.acquire(pid=9999, pid_alive=lambda p: True) is False   # live owner holds it


def test_stale_lock_is_reclaimed(_cron_dir):
    assert lock.acquire(pid=4242, pid_alive=lambda p: False) is True   # writes 4242
    # owner 4242 is "dead" → a new claimant reclaims and overwrites
    assert lock.acquire(pid=5555, pid_alive=lambda p: False) is True
    assert lock.lock_file().read_text().strip() == "5555"


def test_release_removes_file(_cron_dir):
    lock.acquire(pid=4242)
    lock.release()
    assert not lock.lock_file().exists()


def test_release_when_absent_does_not_raise(_cron_dir):
    lock.release()   # no file → no error


def test_garbled_lock_treated_as_reclaimable(_cron_dir):
    _cron_dir.joinpath("cron").mkdir()
    lock.lock_file().write_text("not-a-pid")
    assert lock.acquire(pid=7777, pid_alive=lambda p: True) is True    # unparseable → reclaim
    assert lock.lock_file().read_text().strip() == "7777"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/jobs/test_lock.py -q`
Expected: FAIL (`ModuleNotFoundError: harness.jobs.lock`).

- [ ] **Step 3: Implement `lock.py`**

```python
# harness/jobs/lock.py
"""Single-instance lock for the harness-cron daemon.

The daemon claims cron/daemon.lock atomically (O_CREAT|O_EXCL) at startup and
holds it for its lifetime — so `harness-cron` run twice (by hand, by launchd, by
two `done` windows) yields exactly one live daemon. A crash leaves a stale lock
(dead pid) which the next daemon reclaims. Paths computed at call time via
cron_dir() so tests redirect via the config_dir patch (mirrors heartbeat.py).
"""
from __future__ import annotations

import os
from pathlib import Path

from harness.jobs import paths as _paths


def lock_file() -> Path:
    return _paths.cron_dir() / "daemon.lock"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True          # exists, owned by another user — treat as alive
    return True


def _write_pid(path: Path, pid: int) -> None:
    # atomic replace so a concurrent reader never sees a torn pid
    tmp = path.with_suffix(".lock.tmp")
    tmp.write_text(f"{pid}\n", encoding="utf-8")
    os.replace(tmp, path)


def acquire(*, pid: int | None = None, pid_alive=_pid_alive) -> bool:
    """Claim the lock. Return True if we now own it, False if a live daemon holds it."""
    pid = pid if pid is not None else os.getpid()
    path = lock_file()
    _paths.cron_dir().mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        # Someone holds it. Reclaim only if the stored owner is dead/garbled.
        try:
            owner = int(path.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            owner = None
        if owner is not None and pid_alive(owner):
            return False
        _write_pid(path, pid)     # stale or unparseable → reclaim
        return True
    else:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(f"{pid}\n")
        return True


def release() -> None:
    """Best-effort unlink; no error if already gone."""
    try:
        lock_file().unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/jobs/test_lock.py -q`
Expected: PASS (all 6).

- [ ] **Step 5: Commit**

```bash
git add harness/jobs/lock.py tests/jobs/test_lock.py
git commit -m "feat(jobs): single-instance O_EXCL daemon lock (acquire/release, stale-reclaim)"
```

---

### Task 2: Daemon acquires the lock

**Files:**
- Modify: `harness/jobs/cron_main.py`
- Test: `tests/jobs/test_cron_main.py`

**Interfaces:**
- Consumes: `lock.acquire`, `lock.release` (Task 1).
- Produces: `cron_main.main` exits 0 without running the loop when a live daemon holds the lock; releases the lock after `run_forever` returns.

- [ ] **Step 1: Write failing tests**

**IMPORTANT:** `tests/jobs/test_cron_main.py` has NO `_cron_dir` fixture today, and
its existing `test_default_calls_run_forever` / `test_custom_interval_forwarded`
call `cron_main.main([])`. Once `main` calls `lock.acquire()`, those tests would
write a `daemon.lock` to the REAL `~/.config/harness/cron/`. So FIRST add an
autouse fixture isolating the cron dir to tmp — it protects existing and new tests:

```python
# tests/jobs/test_cron_main.py — add near the top, after imports
import pytest

@pytest.fixture(autouse=True)
def _cron_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("harness.paths.config_dir", lambda: tmp_path)
    return tmp_path
```

Then add the lock tests:

```python
# tests/jobs/test_cron_main.py — add
from harness.jobs import cron_main, lock


def test_main_exits_when_lock_held_by_live_owner(_cron_dir, monkeypatch):
    # a live daemon already holds the lock
    assert lock.acquire(pid=4242, pid_alive=lambda p: True) is True
    monkeypatch.setattr(lock, "_pid_alive", lambda p: True)

    ran = []
    monkeypatch.setattr(cron_main, "run_forever", lambda **kw: ran.append(True))
    # avoid asyncio.run on a non-coroutine
    monkeypatch.setattr(cron_main.asyncio, "run", lambda c: ran.append("asyncio"))

    rc = cron_main.main([])
    assert rc == 0
    assert ran == []                      # loop NOT entered — single-instance working


def test_main_runs_and_releases_lock_when_free(_cron_dir, monkeypatch):
    calls = {}
    async def fake_forever(**kw):
        calls["ran"] = True
    monkeypatch.setattr(cron_main, "run_forever", fake_forever)

    rc = cron_main.main([])
    assert rc == 0
    assert calls.get("ran") is True
    assert not lock.lock_file().exists()  # released in finally


def test_once_does_not_acquire_lock(_cron_dir, monkeypatch):
    monkeypatch.setattr(cron_main, "tick", lambda **kw: [])
    rc = cron_main.main(["--once"])
    assert rc == 0
    assert not lock.lock_file().exists()  # --once never locks
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/jobs/test_cron_main.py -q`
Expected: FAIL (lock not imported / loop runs regardless).

- [ ] **Step 3: Implement in `cron_main.py`**

Add the import (next to the existing daemon/heartbeat imports):

```python
from harness.jobs import lock
```

Replace the run section (the `if args.once: ... return 0` block and the `asyncio.run(...)` that follows) with:

```python
    if args.once:
        tick(now=time.time())
        record_heartbeat(success=True)
        return 0

    # Single-instance: only one harness-cron may run the loop. If a live daemon
    # already holds the lock (another window/launchd/hand-start beat us), exit 0.
    if not lock.acquire():
        logging.getLogger(__name__).info(
            "another harness-cron already holds %s — exiting", lock.lock_file())
        return 0
    try:
        asyncio.run(
            run_forever(
                interval=args.interval,
                clock=time.time,
                sleep=asyncio.sleep,
            )
        )
    finally:
        lock.release()
    return 0
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/jobs/test_cron_main.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/jobs/cron_main.py tests/jobs/test_cron_main.py
git commit -m "feat(jobs): cron_main acquires single-instance lock around run_forever"
```

---

### Task 3: The supervisor

**Files:**
- Create: `harness/jobs/supervisor.py`
- Test: `tests/jobs/test_supervisor.py`

**Interfaces:**
- Consumes: `heartbeat.heartbeat_age/success_age/daemon_status` (#152), `daemon.DEFAULT_INTERVAL` (#152).
- Produces: `ensure_daemon_running(*, spawn=_spawn_detached, now=None) -> str` (`"already-running"|"spawned"|"failed"`); `_spawn_detached() -> None`.

- [ ] **Step 1: Write failing tests**

```python
# tests/jobs/test_supervisor.py
import sys
import pytest
from harness.jobs import supervisor, heartbeat


@pytest.fixture(autouse=True)
def _cron_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("harness.paths.config_dir", lambda: tmp_path)
    return tmp_path


def test_already_running_skips_spawn(_cron_dir):
    heartbeat.record_heartbeat(success=True)          # fresh → "running"
    calls = []
    assert supervisor.ensure_daemon_running(spawn=lambda: calls.append(1)) == "already-running"
    assert calls == []


def test_spawns_when_no_heartbeat(_cron_dir):
    calls = []
    assert supervisor.ensure_daemon_running(spawn=lambda: calls.append(1)) == "spawned"
    assert calls == [1]


def test_spawn_failure_is_swallowed(_cron_dir):
    def boom():
        raise OSError("no python")
    assert supervisor.ensure_daemon_running(spawn=boom) == "failed"   # no raise


def test_spawn_detached_argv_and_flags(_cron_dir, monkeypatch):
    seen = {}
    class FakePopen:
        def __init__(self, argv, **kw):
            seen["argv"] = argv
            seen["kw"] = kw
    monkeypatch.setattr(supervisor.subprocess, "Popen", FakePopen)
    supervisor._spawn_detached()
    assert seen["argv"] == [sys.executable, "-m", "harness.jobs.cron_main"]
    assert seen["kw"]["start_new_session"] is True
    assert seen["kw"]["stdout"] is supervisor.subprocess.DEVNULL
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/jobs/test_supervisor.py -q`
Expected: FAIL (`ModuleNotFoundError: harness.jobs.supervisor`).

- [ ] **Step 3: Implement `supervisor.py`**

```python
# harness/jobs/supervisor.py
"""Ensure a single harness-cron daemon is running — called from the TUI on boot.

DELIBERATE BEHAVIOR: this spawns a DETACHED background process that OUTLIVES the
`done` window that started it (and every other window). Always-on, no config
switch — documented here and in the README. If a headless/server use-case appears
(running `done` on a box where a per-user background daemon is unwanted), that is
the first thing to revisit (a [jobs] autostart config key). The daemon itself is
single-instance via harness/jobs/lock.py, so a race between two windows can never
produce two daemons.
"""
from __future__ import annotations

import logging
import subprocess
import sys

from harness.jobs import heartbeat
from harness.jobs import daemon
from harness.jobs.paths import cron_dir

logger = logging.getLogger(__name__)


def _spawn_detached() -> None:
    cron_dir().mkdir(parents=True, exist_ok=True)
    # Open the log fd, hand it to the child, then CLOSE it in the parent — Popen
    # dups it into the child, so the parent must not leak its own handle.
    log_fd = open(cron_dir() / "daemon.log", "a")
    try:
        subprocess.Popen(
            [sys.executable, "-m", "harness.jobs.cron_main"],
            start_new_session=True,           # POSIX setsid → survives parent exit
            stdout=subprocess.DEVNULL,
            stderr=log_fd,
            close_fds=True,
        )
    finally:
        log_fd.close()


def ensure_daemon_running(*, spawn=_spawn_detached, now=None) -> str:
    """Spawn a detached daemon unless the heartbeat shows one already running.
    Best-effort: a spawn failure is logged, never raised. Returns a status word."""
    status = heartbeat.daemon_status(
        heartbeat.heartbeat_age(now), heartbeat.success_age(now),
        interval=daemon.DEFAULT_INTERVAL,
    )
    if status == "running":
        return "already-running"
    try:
        spawn()
        return "spawned"
    except Exception:
        logger.exception("cron autostart spawn failed")
        return "failed"
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/jobs/test_supervisor.py -q`
Expected: PASS (4).

- [ ] **Step 5: Commit**

```bash
git add harness/jobs/supervisor.py tests/jobs/test_supervisor.py
git commit -m "feat(jobs): supervisor spawns detached daemon when none running"
```

---

### Task 4: TUI hook + docs

**Files:**
- Modify: `harness/tui/app.py` (`on_mount`, ~line 342-350)
- Modify: `tests/jobs/test_cron_drawer_mount.py`
- Modify: `README.md`, `docs/jobs.md`

**Interfaces:**
- Consumes: `supervisor.ensure_daemon_running` (Task 3).

- [ ] **Step 1: Write the failing TUI test**

```python
# tests/jobs/test_cron_drawer_mount.py — add
def test_on_mount_calls_ensure_daemon_running(monkeypatch):
    import harness.jobs.supervisor as sup
    called = []
    monkeypatch.setattr(sup, "ensure_daemon_running", lambda *a, **k: called.append(True) or "spawned")

    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            assert called == [True]    # autostart attempted exactly once on boot

    asyncio.run(go())


def test_on_mount_survives_autostart_failure(monkeypatch):
    import harness.jobs.supervisor as sup
    def boom(*a, **k):
        raise RuntimeError("spawn exploded")
    monkeypatch.setattr(sup, "ensure_daemon_running", boom)

    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            # boot completed despite the autostart raising
            assert app.query_one("#cron-drawer") is not None

    asyncio.run(go())
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/jobs/test_cron_drawer_mount.py -q`
Expected: FAIL (autostart not wired into on_mount).

- [ ] **Step 3: Wire `on_mount` in `app.py`**

After the existing `await self._connect()` try/except block in `on_mount` (the block ending at the `self._fatal(...)` line ~350), add:

```python
        # Auto-start the cron daemon (best-effort, once per window). DELIBERATE:
        # this spawns a DETACHED background process that OUTLIVES done — see
        # harness/jobs/supervisor.py. Never let it break boot.
        try:
            from harness.jobs.supervisor import ensure_daemon_running
            ensure_daemon_running()
        except Exception as e:
            self.log(f"cron autostart skipped: {e!r}")
            if self._tracer is not None:
                self._tracer.emit("dn", "cron.autostart.failed", error=str(e))
```

Note: the `import` is local to avoid pulling `subprocess`/jobs into the TUI import path at module load; the call must be OUTSIDE the `_connect` try/except (a `_connect` failure calls `self._fatal` and should not also run autostart).

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/jobs/test_cron_drawer_mount.py -q`
Expected: PASS.

- [ ] **Step 5: Update docs**

In `docs/jobs.md` "Running the daemon" section, add a note at the top:

> **`done` starts the daemon for you.** When you launch `done`, it ensures one
> `harness-cron` is running and **leaves it running in the background after you
> close `done`** — so scheduled jobs keep firing. It's single-instance: opening
> several `done` windows never starts more than one daemon. You only need to run
> `harness-cron` by hand for headless use (no `done` TUI), or to control the
> cadence with `--interval`.

In the `docs/jobs.md` "Not yet" `#146` entry, narrow it again: the TUI now both
*reports* and *auto-starts* the daemon; *stopping* it from the TUI is still
deferred.

In `README.md` Jobs section, add one line: "`done` auto-starts the daemon on
launch (single-instance; it keeps running in the background after you close
`done`)."

- [ ] **Step 6: Commit**

```bash
git add harness/tui/app.py tests/jobs/test_cron_drawer_mount.py README.md docs/jobs.md
git commit -m "feat(tui): auto-start cron daemon on boot (best-effort, single-instance) + docs"
```

---

## Self-Review

**Spec coverage:**
- O_EXCL lock, daemon-owned, stale-reclaim → Task 1 ✓
- `--once` skips lock; release in finally; exit-0-when-held → Task 2 ✓
- supervisor heartbeat-gate + detached spawn (argv, start_new_session, stderr→log, parent fd closed) → Task 3 ✓
- TUI on_mount best-effort, once per window, never breaks boot → Task 4 ✓
- No public `owner_pid` (YAGNI) → not implemented ✓
- SIGTERM relies on stale-reclaim (no signal handler) → Task 2 (no handler added) ✓
- Docs + code comments on deliberate detach/always-on/future-concern → Task 3 (module docstring) + Task 4 (on_mount comment + docs) ✓

**Placeholder scan:** none — every step has full code.

**Type consistency:** `acquire(*, pid=None, pid_alive=...)`, `release()`, `lock_file()`, `ensure_daemon_running(*, spawn=..., now=None) -> str`, `_spawn_detached()`, `daemon.DEFAULT_INTERVAL`, `heartbeat.daemon_status/heartbeat_age/success_age` — consistent across Tasks 1→4 and match #152's shipped names.
