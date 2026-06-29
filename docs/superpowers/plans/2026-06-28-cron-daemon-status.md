# Cron daemon-liveness indicator — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show in the `done` jobs panel (`Ctrl+J`) whether the `harness-cron` daemon is running, so an unarmed roster is unmistakable.

**Architecture:** A new pure `harness/jobs/heartbeat.py` writes/reads two epoch files (`ticker_heartbeat`, `ticker_success`) and classifies liveness; the daemon writes them each tick (best-effort); the cron dashboard reads them on open and renders a colored header line above the roster. Heartbeat design borrowed from Hermes Agent.

**Tech Stack:** Python 3.11, Textual (TUI), pytest. No new dependencies.

## Global Constraints

- Test command (from worktree root): `.venv/bin/python -m pytest tests/ -q` (target `tests/` only).
- Heartbeat writes are **best-effort**: every exception in `record_heartbeat` is swallowed; a write failure must never break the daemon tick loop.
- Heartbeat reads never raise into the TUI: a missing/partial/garbled file yields `None`, classified as `stopped`.
- No PID files, no process scanning, no platform-specific syscalls.
- Atomic write mirrors `harness/jobs/store.py`'s `_save`: `path.with_suffix(".tmp")` + `write_text` + `os.replace` (no `mkstemp`).
- Staleness threshold: `STALE_AFTER = interval * 3 + 20` (= 110s at the 30.0s default).
- Single source for the default interval: new `harness.jobs.daemon.DEFAULT_INTERVAL = 30.0`, referenced by `run_forever`'s default arg, `cron_main`'s argparse default, and the panel.

---

## File Structure

- **New:** `harness/jobs/heartbeat.py` — write/read/classify liveness (pure core + isolated I/O).
- **New:** `tests/jobs/test_heartbeat.py` — unit tests for the above.
- **Modify:** `harness/jobs/daemon.py` — add `DEFAULT_INTERVAL`; write heartbeat in `run_forever`.
- **Modify:** `harness/jobs/cron_main.py` — use `DEFAULT_INTERVAL`; write heartbeat on `--once`.
- **Modify:** `harness/tui/widgets/cron_dashboard.py` — header line showing daemon status on `set_rows`.
- **Modify:** `tests/jobs/test_cron_dashboard.py` — assert header reflects status.
- **Modify:** `tests/jobs/test_daemon.py` (or the existing daemon test file) — assert heartbeat written on clean/failing tick + startup seeds success.
- **Docs:** `docs/jobs.md`, `docs/jobs-walkthrough.md` — note the panel now shows daemon status.

---

### Task 1: Heartbeat core — write, read, classify

**Files:**
- Create: `harness/jobs/heartbeat.py`
- Test: `tests/jobs/test_heartbeat.py`

**Interfaces:**
- Consumes: `harness.jobs.paths.cron_dir()`; `harness.jobs.store._ensure_dirs` (or replicate the `mkdir(parents=True, exist_ok=True)`).
- Produces:
  - `record_heartbeat(success: bool = False) -> None`
  - `heartbeat_age(now: float | None = None) -> float | None`
  - `success_age(now: float | None = None) -> float | None`
  - `daemon_status(hb_age: float | None, ok_age: float | None, *, interval: float) -> str` → one of `"stopped"|"stalled"|"failing"|"running"`
  - `status_line(status: str, hb_age: float | None) -> str`
  - `_heartbeat_file() -> Path`, `_success_file() -> Path` (computed at call time via `cron_dir()`, NOT cached module constants — so a test patching `harness.paths.config_dir` redirects them with no extra monkeypatching, matching `paths.py`/`store.py`)

- [ ] **Step 1: Write failing tests for the pure classifier and status line**

```python
# tests/jobs/test_heartbeat.py
import time
import pytest
from harness.jobs import heartbeat as hb


I = 30.0  # interval; STALE_AFTER = 30*3+20 = 110

@pytest.mark.parametrize("hb_age, ok_age, expected", [
    (None, None, "stopped"),       # never ran
    (200.0, 5.0, "stalled"),       # heartbeat too old
    (5.0, None, "failing"),        # alive, never a successful tick
    (5.0, 200.0, "failing"),       # alive, success stale
    (5.0, 5.0, "running"),         # both fresh
    (110.0, 5.0, "running"),       # hb_age == STALE_AFTER boundary (not >)
    (110.01, 5.0, "stalled"),      # just over
])
def test_daemon_status(hb_age, ok_age, expected):
    assert hb.daemon_status(hb_age, ok_age, interval=I) == expected


def test_status_line_wording():
    assert hb.status_line("running", 5.0).startswith("✓")
    assert "jobs will fire" in hb.status_line("running", 5.0)
    assert hb.status_line("failing", 5.0).startswith("⚠")
    assert hb.status_line("stalled", 200.0) == "⚠ daemon stalled — no heartbeat for 200s"
    assert hb.status_line("stopped", None).startswith("✗")
    assert "won't fire" in hb.status_line("stopped", None)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/jobs/test_heartbeat.py -q`
Expected: FAIL (`ModuleNotFoundError: harness.jobs.heartbeat` / attribute errors).

- [ ] **Step 3: Implement the pure helpers**

```python
# harness/jobs/heartbeat.py
"""Liveness signal for the harness-cron daemon — heartbeat files + classifier.

Borrowed from Hermes Agent (cron/jobs.py): the daemon writes two epoch files
(alive, last-clean-tick); the panel reads their age and classifies. Best-effort
writes never break the tick loop; reads never raise into the TUI.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from harness.jobs import paths as _paths


def _heartbeat_file() -> Path:
    return _paths.cron_dir() / "ticker_heartbeat"


def _success_file() -> Path:
    return _paths.cron_dir() / "ticker_success"


def _atomic_write_epoch(path: Path, now: float) -> None:
    # Mirrors harness/jobs/store.py _save: tmp + os.replace (atomic for a one-line file).
    _paths.cron_dir().mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(f"{now}\n", encoding="utf-8")
    os.replace(tmp, path)


def record_heartbeat(success: bool = False) -> None:
    """Write the alive marker; if success, also the last-clean-tick marker.
    Best-effort: any failure is swallowed so the tick loop is never disrupted."""
    now = time.time()
    try:
        _atomic_write_epoch(_heartbeat_file(), now)
    except Exception:
        pass
    if success:
        try:
            _atomic_write_epoch(_success_file(), now)
        except Exception:
            pass


def _epoch_file_age(path: Path, now: float | None) -> float | None:
    if now is None:
        now = time.time()
    try:
        raw = path.read_text(encoding="utf-8").strip()
        return max(0.0, now - float(raw))
    except Exception:
        return None


def heartbeat_age(now: float | None = None) -> float | None:
    return _epoch_file_age(_heartbeat_file(), now)


def success_age(now: float | None = None) -> float | None:
    return _epoch_file_age(_success_file(), now)


def daemon_status(hb_age: float | None, ok_age: float | None, *, interval: float) -> str:
    """Classify daemon liveness from two file ages. Pure; no I/O."""
    stale_after = interval * 3 + 20
    if hb_age is None:
        return "stopped"
    if hb_age > stale_after:
        return "stalled"
    if ok_age is None or ok_age > stale_after:
        return "failing"
    return "running"


def status_line(status: str, hb_age: float | None) -> str:
    """One-line header text for the panel. Color is applied by the widget."""
    if status == "running":
        return "✓ daemon running — jobs will fire"
    if status == "failing":
        return "⚠ daemon running but ticks are failing"
    if status == "stalled":
        n = 0 if hb_age is None else int(hb_age)
        return f"⚠ daemon stalled — no heartbeat for {n}s"
    return "✗ daemon not running — scheduled jobs won't fire"
```

- [ ] **Step 4: Run to verify the pure tests pass**

Run: `.venv/bin/python -m pytest tests/jobs/test_heartbeat.py -q`
Expected: PASS (classifier + status_line tests).

- [ ] **Step 5: Add the I/O round-trip tests**

```python
# append to tests/jobs/test_heartbeat.py
#
# Paths are computed at call time via cron_dir(), so patching config_dir to
# tmp_path (the same pattern as tests/jobs/test_daemon.py's _cron_dir fixture)
# redirects every heartbeat file — no module-attribute rebinding needed.

@pytest.fixture(autouse=True)
def _cron_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("harness.paths.config_dir", lambda: tmp_path)
    return tmp_path


def test_record_and_read_roundtrip(_cron_dir):
    hb.record_heartbeat(success=True)
    assert hb.heartbeat_age() is not None and hb.heartbeat_age() < 5
    assert hb.success_age() is not None and hb.success_age() < 5


def test_fresh_install_creates_cron_dir(_cron_dir):
    assert not (_cron_dir / "cron").exists()
    hb.record_heartbeat()                       # must create the dir
    assert (_cron_dir / "cron" / "ticker_heartbeat").is_file()


def test_no_files_reads_none(_cron_dir):
    assert hb.heartbeat_age() is None           # nothing written → stopped
    assert hb.success_age() is None


def test_partial_file_reads_none(_cron_dir):
    p = _cron_dir / "cron"; p.mkdir()
    (p / "ticker_heartbeat").write_text("not-a-number")
    assert hb.heartbeat_age() is None           # parse failure → None, no raise


def test_record_never_raises_on_unwritable(_cron_dir, monkeypatch):
    def boom(*a, **k):
        raise OSError("nope")
    monkeypatch.setattr(hb, "_atomic_write_epoch", boom)
    hb.record_heartbeat(success=True)           # must not raise


def test_stale_heartbeat(_cron_dir):
    p = _cron_dir / "cron"; p.mkdir()
    (p / "ticker_heartbeat").write_text(f"{time.time() - 500}\n")
    age = hb.heartbeat_age()
    assert age is not None and age > 400
```

- [ ] **Step 6: Run all heartbeat tests**

Run: `.venv/bin/python -m pytest tests/jobs/test_heartbeat.py -q`
Expected: PASS (all).

- [ ] **Step 7: Commit**

```bash
git add harness/jobs/heartbeat.py tests/jobs/test_heartbeat.py
git commit -m "feat(jobs): heartbeat liveness signal for harness-cron (write/read/classify)"
```

---

### Task 2: Daemon writes the heartbeat

**Files:**
- Modify: `harness/jobs/daemon.py`
- Modify: `harness/jobs/cron_main.py`
- Test: `tests/jobs/test_daemon.py` (existing) and `tests/jobs/test_cron_main.py` (existing)

**Interfaces:**
- Consumes: `heartbeat.record_heartbeat` (Task 1).
- Produces: `harness.jobs.daemon.DEFAULT_INTERVAL = 30.0`.

- [ ] **Step 1: Write the failing daemon test**

```python
# tests/jobs/test_daemon.py — add

import asyncio
from harness.jobs import daemon, heartbeat


def test_run_forever_seeds_success_at_startup(monkeypatch):
    calls = []
    monkeypatch.setattr(daemon, "record_heartbeat", lambda success=False: calls.append(("hb", success)))
    monkeypatch.setattr(daemon, "tick", lambda now, executor=None: [])
    # stop after the first iteration by raising from sleep
    async def stop_sleep(_):
        raise asyncio.CancelledError
    clock = lambda: 1000.0
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(daemon.run_forever(interval=daemon.DEFAULT_INTERVAL, clock=clock, sleep=stop_sleep))
    # pre-loop seed must be success=True so a fresh daemon reads "running"
    assert calls[0] == ("hb", True)
    # clean tick also records success
    assert ("hb", True) in calls[1:]


def test_run_forever_records_failure_on_bad_tick(monkeypatch):
    calls = []
    monkeypatch.setattr(daemon, "record_heartbeat", lambda success=False: calls.append(success))
    def boom(now, executor=None):
        raise RuntimeError("tick blew up")
    monkeypatch.setattr(daemon, "tick", boom)
    async def stop_sleep(_):
        raise asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(daemon.run_forever(interval=daemon.DEFAULT_INTERVAL, clock=lambda: 1.0, sleep=stop_sleep))
    assert False in calls  # failing tick recorded a non-success heartbeat
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/jobs/test_daemon.py -q`
Expected: FAIL (`AttributeError: DEFAULT_INTERVAL` / `record_heartbeat` not imported in daemon).

- [ ] **Step 3: Implement in `daemon.py`**

Add the import and constant near the top (after existing imports):

```python
from harness.jobs.heartbeat import record_heartbeat

DEFAULT_INTERVAL = 30.0
```

Change `run_forever`'s signature default and body:

```python
async def run_forever(
    *,
    interval: float = DEFAULT_INTERVAL,
    clock,
    sleep,
    executor=None,
) -> None:
    if executor is None:
        executor = run_headless_turn
    record_heartbeat(success=True)          # seed both files so a fresh daemon reads "running"
    while True:
        now = clock()
        try:
            tick(now, executor=executor)
            record_heartbeat(success=True)  # clean tick
        except Exception:                   # noqa: BLE001 — transient failure must not kill the loop
            logger.exception("cron tick failed at %s; continuing to next interval", now)
            record_heartbeat(success=False) # alive but this tick failed
        await sleep(interval)
```

- [ ] **Step 4: Implement in `cron_main.py`**

Replace the inline `30.0` default and add a heartbeat on the `--once` path:

```python
from harness.jobs.daemon import run_forever, tick, DEFAULT_INTERVAL
from harness.jobs.heartbeat import record_heartbeat
```

```python
    parser.add_argument(
        "--interval", type=float, default=DEFAULT_INTERVAL, metavar="SECONDS",
        help="Seconds between ticks in continuous mode (default: 30).",
    )
```

```python
    if args.once:
        tick(now=time.time())
        record_heartbeat(success=True)
        return 0
```

- [ ] **Step 5: Run the daemon + cron_main tests**

Run: `.venv/bin/python -m pytest tests/jobs/test_daemon.py tests/jobs/test_cron_main.py -q`
Expected: PASS. (If an existing `test_cron_main` asserts the `--interval` default value, it still sees 30.0.)

- [ ] **Step 6: Commit**

```bash
git add harness/jobs/daemon.py harness/jobs/cron_main.py tests/jobs/test_daemon.py
git commit -m "feat(jobs): daemon writes heartbeat each tick; DEFAULT_INTERVAL constant"
```

---

### Task 3: Panel header line shows daemon status

**Files:**
- Modify: `harness/tui/widgets/cron_dashboard.py`
- Test: `tests/jobs/test_cron_dashboard.py`

**Interfaces:**
- Consumes: `heartbeat.heartbeat_age`, `heartbeat.success_age`, `heartbeat.daemon_status`, `heartbeat.status_line` (Task 1); `daemon.DEFAULT_INTERVAL` (Task 2).
- Produces: pure `daemon_header(hb_age, ok_age, *, interval) -> tuple[str, str]` → `(text, color)` where color ∈ `{"green","yellow","red"}`.

- [ ] **Step 1: Write the failing pure-header test**

```python
# tests/jobs/test_cron_dashboard.py — add
from harness.tui.widgets.cron_dashboard import daemon_header
from harness.jobs import daemon as _d

def test_daemon_header_states():
    text, color = daemon_header(5.0, 5.0, interval=_d.DEFAULT_INTERVAL)
    assert color == "green" and text.startswith("✓")
    text, color = daemon_header(None, None, interval=_d.DEFAULT_INTERVAL)
    assert color == "red" and "won't fire" in text
    text, color = daemon_header(5.0, 999.0, interval=_d.DEFAULT_INTERVAL)
    assert color == "yellow"            # failing
    text, color = daemon_header(999.0, 5.0, interval=_d.DEFAULT_INTERVAL)
    assert color == "yellow"            # stalled
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/jobs/test_cron_dashboard.py::test_daemon_header_states -q`
Expected: FAIL (`ImportError: cannot import name 'daemon_header'`).

- [ ] **Step 3: Implement the pure helper + render the header**

Add to `cron_dashboard.py` (after the existing pure render helpers):

```python
from harness.jobs import heartbeat as _hb
from harness.jobs import daemon as _daemon

_STATUS_COLOR = {"running": "green", "failing": "yellow", "stalled": "yellow", "stopped": "red"}


def daemon_header(hb_age: float | None, ok_age: float | None, *, interval: float) -> tuple[str, str]:
    """Pure: (header text, color name) for the current daemon liveness."""
    status = _hb.daemon_status(hb_age, ok_age, interval=interval)
    return _hb.status_line(status, hb_age), _STATUS_COLOR[status]
```

In `CronDashboard.set_rows`, after building the roster, compute and store the header, and surface it. The dashboard is a `ListView`; render the header as the first non-selectable `ListItem` (disabled) so it sits above the rows:

```python
    def set_rows(self, jobs: list[m.Job]) -> None:
        self._jobs = list(jobs)
        self.clear()
        text, color = daemon_header(
            _hb.heartbeat_age(), _hb.success_age(), interval=_daemon.DEFAULT_INTERVAL
        )
        header = ListItem(Static(text, markup=False))
        header.disabled = True                      # non-selectable header row
        header.add_class("cron-daemon-status")
        header.styles.color = color
        self.append(header)
        for row_text, job in zip(render_rows(jobs), jobs):
            item = ListItem(Static(row_text, markup=False))
            item.data = job.id
            self.append(item)
```

Note: `_focused_job_id` already returns `getattr(highlighted, "data", None)`; the header has no `.data`, so focusing it yields `None` and the existing `if job_id is None: return` guards in every action already no-op. No action changes needed.

- [ ] **Step 4: Run the dashboard tests**

Run: `.venv/bin/python -m pytest tests/jobs/test_cron_dashboard.py -q`
Expected: PASS (new header test + existing `render_rows` tests unchanged).

- [ ] **Step 5: Verify the header-row guard with a focus test**

```python
# tests/jobs/test_cron_dashboard.py — add (only if a CronDashboard instance is constructible in tests;
# the existing tests show how. If they exercise render_rows only, skip — Step 3's note covers the guard.)
```

If the existing test file only unit-tests `render_rows` (no widget instance), skip this step — the guard is covered by inspection (header has no `.data`). Otherwise, assert the first item is disabled and carries no `.data`.

- [ ] **Step 6: Commit**

```bash
git add harness/tui/widgets/cron_dashboard.py tests/jobs/test_cron_dashboard.py
git commit -m "feat(tui): cron dashboard shows daemon-liveness header above roster"
```

---

### Task 4: Docs

**Files:**
- Modify: `docs/jobs.md`
- Modify: `docs/jobs-walkthrough.md`

- [ ] **Step 1: Update `docs/jobs.md`**

In the "Viewing your jobs" section, add that the dashboard now shows a daemon-status header (running / failing / stalled / not-running). In the "Not yet" `#146` entry, narrow it: the panel now *reports* daemon status, but still doesn't *start/stop* it.

- [ ] **Step 2: Update `docs/jobs-walkthrough.md`**

In Step 6 (the daemon section), note that the dashboard header tells you whether the daemon is running, so you can tell at a glance whether scheduled jobs are armed.

- [ ] **Step 3: Commit**

```bash
git add docs/jobs.md docs/jobs-walkthrough.md
git commit -m "docs(jobs): note the panel now shows daemon-liveness status"
```

---

## Self-Review

**Spec coverage:**
- Heartbeat file + two signals → Task 1 ✓
- Atomic write mirroring store._save → Task 1 (`_atomic_write_epoch`) ✓
- ensure-dirs before first write → Task 1 (`test_fresh_install_creates_cron_dir`) ✓
- parse-guard / partial-file → None → Task 1 (`test_partial_file_reads_none`) ✓
- `daemon_status` classifier + thresholds → Task 1 ✓
- `status_line` wording → Task 1 ✓
- Daemon writes on clean/failing tick + startup seeds success → Task 2 ✓
- `DEFAULT_INTERVAL` single source → Task 2 (daemon, cron_main, panel) ✓
- `--once` writes heartbeat → Task 2 ✓
- Header line above roster, colored, read on open → Task 3 ✓
- Header-row action guard → Task 3 (note + Step 5) ✓
- Docs → Task 4 ✓

**Placeholder scan:** none — every code step has full code. (Task 3 Step 5 is conditional-by-design, with the fallback stated, not a placeholder.)

**Type consistency:** `daemon_status(hb_age, ok_age, *, interval)`, `status_line(status, hb_age)`, `daemon_header(hb_age, ok_age, *, interval) -> (text, color)`, `record_heartbeat(success=False)`, `heartbeat_age/success_age(now=None)`, `DEFAULT_INTERVAL` — names consistent across Tasks 1→3.
