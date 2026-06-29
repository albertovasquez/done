# Cron daemon-liveness indicator in the jobs panel — design

**Status:** approved (brainstorming) — ready for plan
**Issue:** part of [#146](https://github.com/albertovasquez/done/issues/146)
**Date:** 2026-06-28

## Problem

The jobs subsystem (Phase 1, #140) lets a user create scheduled jobs entirely
inside the `done` TUI, but the `harness-cron` daemon that actually fires them on
schedule runs as a **separate process**. The TUI neither starts it nor reports
whether it is running. A user can create a job, see it in the roster with a
future `next-run`, and reasonably believe it is armed — when in fact nothing is
watching the clock. The roster looks identical whether or not the daemon exists.

**Goal:** when the user opens the jobs panel (`Ctrl+J`), show whether the
`harness-cron` daemon is running, so an unarmed roster is unmistakable.

Out of scope (stays in #146): starting/stopping the daemon from the TUI,
auto-start, and live auto-refresh while the panel is open. This change only makes
the gap *visible*.

## Prior art — Hermes Agent

We base this on Nous Research's **Hermes Agent** (local checkout at
`~/.hermes/hermes-agent`), which solved the same problem for its cron ticker. Key
borrowed decisions:

1. **Heartbeat file, not a PID/process scan.** The ticker writes a liveness file;
   the reader judges staleness by age. Cross-platform, survives crashes (a dead
   daemon goes stale), no process matching. (`cron/jobs.py`,
   `cron/scheduler_provider.py`.)
2. **Two signals, not one** — a `heartbeat` (alive and looping) *and* a `success`
   marker (last tick that completed cleanly). This distinguishes a daemon that is
   *alive but failing every tick* from a *healthy* one. Hermes added the second
   signal after real incidents (#32612, #32895) where a stuck-failing ticker kept
   the plain heartbeat fresh and falsely reported healthy.
3. **Atomic write, best-effort.** tmpfile + replace so a concurrent reader never
   sees a torn file; a write failure is swallowed and never disrupts the tick
   loop. (`_atomic_write_epoch`.)
4. **Staleness threshold derived from the tick interval:**
   `STALE_AFTER = interval * 3 + 20`. (`hermes_cli/cron.py:177`.)

Where we differ: Hermes runs the ticker as a thread inside a long-lived
"gateway", so its status also checks whether the gateway *process* is up. We have
no gateway — our daemon (`harness-cron`) **is** the loop — so there is only one
liveness question (is the loop ticking?), answered entirely by the two files. No
process/PID layer at all.

## Architecture

Three layers, matching how the existing jobs code is split (pure core →
daemon → TUI), each independently testable.

### 1. Liveness signal — `harness/jobs/heartbeat.py` (new)

Pure, dependency-light, I/O isolated to two small files under `cron_dir()`.

Files (epoch seconds as text, one per file):

| File | Meaning |
|---|---|
| `cron/ticker_heartbeat` | last loop iteration (alive) |
| `cron/ticker_success` | last tick that completed without raising |

Functions:

- `record_heartbeat(success: bool = False) -> None`
  Atomically writes the current epoch to `ticker_heartbeat`; if `success`, also
  to `ticker_success`. **Must ensure `cron_dir()` exists first** — on a fresh
  install the daemon's first heartbeat runs before any job save has created the
  dir, so without an `_ensure_dirs()`/`mkdir(parents=True, exist_ok=True)` call
  the write raises, gets swallowed (best-effort), and the panel reads `stopped`
  forever. Reuse `store._ensure_dirs()` (or the same `mkdir`) at the top.
  Atomic-write pattern **mirrors `store._save`**: `path.with_suffix(".tmp")` +
  `write_text` + `os.replace` (already atomic for a one-line epoch — no `mkstemp`
  fd dance). **Best-effort:** all exceptions swallowed — a heartbeat write must
  never break the caller.
- `heartbeat_age() -> float | None`
  Seconds since `ticker_heartbeat`, or `None` if missing/unreadable. **The reader
  parses the epoch from the file contents** (not file mtime — mtime lies across
  copies/restores) and returns `None` on any read/`float()` parse failure, so a
  partial/empty file caught mid-write reads as unknown rather than raising.
- `success_age() -> float | None`
  Same for `ticker_success`.
- `daemon_status(hb_age: float | None, ok_age: float | None, *, interval: float) -> str`
  **Pure classifier** (no I/O — ages passed in), unit-tested in isolation. Mirrors
  the existing `render_rows` pure-function pattern. Returns one of:
  `"stopped" | "stalled" | "failing" | "running"`.

  `STALE_AFTER = interval * 3 + 20` (= 110s at our 30s default).

  | Condition (checked in order) | Result |
  |---|---|
  | `hb_age is None` | `"stopped"` |
  | `hb_age > STALE_AFTER` | `"stalled"` |
  | `ok_age is None or ok_age > STALE_AFTER` | `"failing"` |
  | otherwise | `"running"` |

- `status_line(status: str, hb_age: float | None) -> str`
  **Pure** presentation helper → the header string the panel renders. Wording:

  | status | line |
  |---|---|
  | `running` | `✓ daemon running — jobs will fire` |
  | `failing` | `⚠ daemon running but ticks are failing` |
  | `stalled` | `⚠ daemon stalled — no heartbeat for {n}s` |
  | `stopped` | `✗ daemon not running — scheduled jobs won't fire` |

  (`{n}` from `hb_age`. Color is applied by the widget, not baked into the string,
  so the helper stays pure and testable.)

`daemon_status` takes `interval` as a parameter (no module-level coupling, keeps
it pure). Callers pass the real cadence. To avoid a magic `30.0` drifting between
sites, introduce **`daemon.DEFAULT_INTERVAL = 30.0`** (new named constant) and
reference it from `run_forever`'s default arg, `cron_main`'s argparse default,
and the panel's `daemon_status(..., interval=...)` call — one source of truth.

### 2. Daemon writes the signal — `harness/jobs/daemon.py`, `cron_main.py`

`run_forever` (daemon.py) — minimal additions to the existing loop:

```
record_heartbeat(success=True)          # once, before first sleep — see startup note
while True:
    now = clock()
    try:
        tick(now, executor=executor)
        record_heartbeat(success=True)  # clean tick
    except Exception:
        logger.exception(...)           # existing behavior, loop survives
        record_heartbeat(success=False) # alive but this tick failed
    await sleep(interval)
```

**Startup-window decision (review finding).** The pre-loop write uses
`success=True`, seeding *both* `ticker_heartbeat` and `ticker_success`. If it
seeded only the plain heartbeat, then for up to one `interval` (until the first
tick completes) `ok_age` would be `None` and the classifier would report
`failing` on a daemon that just started cleanly — a false alarm every launch.
Seeding success at startup means a freshly-started daemon reads `running`
immediately; a *genuinely* failing daemon flips to `failing` only once
`ticker_success` goes stale (`> STALE_AFTER`), which is the correct signal. The
empty-job-roster case is unaffected — a tick with no due jobs still counts as a
clean (`success=True`) tick.

`cron_main.main` — the `--once` path writes `record_heartbeat(success=True)` after
its single `tick()` (so `harness-cron --once` also leaves a fresh signal).

Heartbeat writes are best-effort; they do not change tick/await semantics or the
loop's existing exception handling (KeyboardInterrupt/CancelledError still
propagate and stop the loop).

### 3. Panel surfaces it — `harness/tui/widgets/cron_dashboard.py`, `app.py`

- Add a non-selectable **header line** above the roster rows showing
  `status_line(...)`, colored by status (green = running, yellow = failing/stalled,
  red = stopped). The roster `ListView` rows are unchanged.
- The status is read **on open**: `set_rows()` already runs every time the drawer
  opens (`app.py:1437`) and on every roster action reload — extend it (or the
  open path) to also read the two ages and refresh the header. No timer, no
  background polling; open-time read matches the existing refresh model.
- The read is wrapped so a missing/garbled file yields `stopped`/unknown and never
  raises into the TUI.

## Data flow

```
harness-cron loop ──record_heartbeat(success=?)──▶ cron/ticker_heartbeat
                                                    cron/ticker_success
user presses Ctrl+J ──▶ set_rows() ──reads ages──▶ daemon_status() ──▶ status_line() ──▶ header
```

## Error handling

- **Writes** (daemon): best-effort, all exceptions swallowed; never alter the tick
  loop.
- **Reads** (panel): missing/garbled/old-build file → `None` age → `stopped`/unknown;
  never an exception in the render path.
- **No** PID, process scan, or platform-specific syscalls — identical behavior on
  macOS and Linux.

## Testing

- `daemon_status()` — table test across all four states (pure, no I/O), incl. the
  `ok_age is None` (never-succeeded) edge.
- `status_line()` — one assertion per status, incl. `{n}` interpolation for stalled.
- `record_heartbeat` + `heartbeat_age`/`success_age` — round-trip in a tmp
  `cron_dir`; **fresh-install case: `cron_dir` does not exist yet → first
  `record_heartbeat` creates it and the file is written** (guards the swallowed-
  mkdir-failure regression); stale case by writing a backdated epoch; partial-file
  case by writing a non-numeric/empty file → age is `None`, no raise; best-effort
  case by pointing at an unwritable dir and asserting no raise.
- Daemon — assert `run_forever` writes a heartbeat on the clean-tick path and a
  (non-success) heartbeat on the failing-tick path; **assert the pre-loop write
  seeds `ticker_success` so a just-started daemon classifies as `running`, not
  `failing`** (startup-window regression); extends existing injected clock/sleep
  daemon tests.
- Panel — inject ages and assert the rendered header matches the expected status
  line (mirrors existing `render_rows` tests; no live daemon needed).

## Files

- **New:** `harness/jobs/heartbeat.py`, `tests/jobs/test_heartbeat.py`.
- **Modify:** `harness/jobs/daemon.py` (write on tick), `harness/jobs/cron_main.py`
  (write on `--once`), `harness/tui/widgets/cron_dashboard.py` (header line +
  status read), `harness/tui/app.py` (wire header refresh on open if not fully
  covered by `set_rows`). Extend `tests/jobs/test_cron_dashboard.py` and the daemon
  test.
- **Docs:** update `docs/jobs.md` (daemon status now surfaced in the panel) and the
  "Not yet" entry referencing #146; note the indicator in
  `docs/jobs-walkthrough.md` Step 6.

## YAGNI / deferred (still #146)

- Start/stop the daemon from the TUI.
- Auto-start the daemon on first job creation.
- Live auto-refresh of the header while the panel stays open (today: refreshes on
  open and on each roster action).
