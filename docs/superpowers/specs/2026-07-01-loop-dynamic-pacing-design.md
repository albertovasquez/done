# Design: `/loop` — dynamic self-pacing for scheduled turns

**Status:** approved (brainstorming) — ready for implementation plan
**Date:** 2026-07-01
**Author:** Claude (with Alberto)
**Related:** [Get Missions Done epic](2026-06-29-get-missions-done-epic.md), `harness/jobs/`

## 1. Summary

Port the distinctive capability of Claude Code's `/loop` skill — **dynamic
self-pacing**, where after each run the agent chooses its *own* next-fire delay
rather than following a fixed cadence — into `done`'s existing `jobs/` cron
subsystem.

`done` already covers fixed-interval loops (`Every`), cron, and one-shots (`At`)
with persona-faithful headless turns, CostGate, Grant, and an OS-service daemon.
The one thing it cannot express is a loop whose cadence the agent steers from
inside the turn. This spec adds exactly that, as a first-class **`Dynamic`
schedule kind**, reusing the entire existing daemon/executor/gate machinery.

This is the generic pacing *engine* only. The autonomous-steward semantics of
`/loop` (no-args "advance my work, stop when quiet") are deliberately **out of
scope** — they belong to the Missions epic Phase 2 as a payload built on top of
this engine. Keeping the two layered avoids building a second, competing
autonomy system.

## 2. Goals / Non-goals

### Goals
- A persona can create a **dynamic loop**: a scheduled job that runs a turn,
  and the turn decides when it next runs.
- The turn steers its own cadence by calling a `set_next_run` tool.
- If a turn ends without rescheduling (model forgot, crash, or timeout), the
  loop **pauses** (`next_run_at = None`) — fail-closed, no silent runaway.
- Reuse 100% of the existing daemon, `ops.run`, CostGate, Grant, store,
  heartbeat, and OS-service. No parallel scheduler.
- Create loops in-chat via a `create_loop` agent tool, persona-bound like
  `create_job`.

### Non-goals (this slice)
- Autonomous-steward mode / no-args "advance my work" behavior (Missions Ph.2).
- A `dn loop` CLI subcommand (later phase; the mini-epic includes it, this
  slice does not).
- Changing `Every`/`Cron`/`At` behavior in any way.
- Mission files, milestone cursors, subagent fan-out (separate epic).

## 3. Why this shape (context)

`done`'s daemon (`harness/jobs/daemon.py`) is a long-lived OS service that fires
**fresh, stateless** headless turns; each turn process exits when done. There is
no persistent agent process to "wake up," so Claude Code's `ScheduleWakeup`
(which wakes a still-alive agent) has no direct analogue. The port therefore
persists the chosen next-fire time into `JobState` and lets the daemon read it on
the next tick — a **self-rescheduling job**. This preserves `done`'s
crash-isolation and reboot-survival (the daemon's whole point) and reuses every
existing safety gate.

## 4. Architecture

One new schedule kind slots into the existing tagged union. Everything else is
already built.

```
create_loop tool ──► handle_create_job ──► Job(schedule=Dynamic(...))  [store]
                                                  │
                            daemon.tick(now) reads JobState.next_run_at
                                                  │  (due? → ops.run)
                                                  ▼
                        run_headless_turn(job)  — persona-faithful turn
                                                  │
                    turn calls set_next_run(delay_seconds=…) ──► stamps
                                                  │              _next_run_override
                                                  ▼              on env
                        ops.run computes next_run_at via m.next_run_at(...)
                                                  │
           Dynamic: fresh→now; override→now+delay; ran+no override→None (pause)
```

### 4.1 New `Dynamic` schedule kind — `harness/jobs/model.py`

```python
@dataclass(frozen=True)
class Dynamic:
    # No fixed cadence. The turn stamps its chosen next-fire delay; the daemon
    # reads it. min_cadence_s (CostGate) still floors the effective delay.
    pass  # marker type; all state lives in JobState + the per-run override
```

- Add `Dynamic` to the `Schedule` union.
- `schedule_to_dict` / `schedule_from_dict`: `{"kind": "dynamic"}`.
- `next_run_at(schedule, now, state)` gains a `Dynamic` branch. **Signature
  change:** it needs the per-run override the turn chose. Rather than thread a
  new positional arg through every caller, the override is carried on `JobState`
  as a transient field the executor sets and `ops.run` reads (see 4.3). The
  `Dynamic` branch returns:
  - `now` when the state is fresh (`last_run_at is None`) → **arm on next tick**.
    The loop's first run is always immediate; a user who wants a delayed first
    run gets it by having that first turn call `set_next_run`.
  - `now + max(override, min_cadence_s)` when an override was set this run.
  - `None` when the state has already run (`last_run_at is not None`) and no
    override was set → **pause**.

### 4.2 `set_next_run` agent tool — `harness/tools/set_next_run.py`

- Schema: one required arg `delay_seconds: int` (> 0). Description tells the
  model this reschedules the current loop; omitting the call pauses the loop.
- `execute(args, env)` stamps `env._next_run_override = int(delay_seconds)`.
  It does **not** touch the store directly — the store write happens in
  `ops.run` after the turn returns, so a crash mid-turn can't leave a
  half-written schedule. The tool only records intent on the env.
- Reuses the existing env-stamping pattern (`env._active_persona`,
  `env._remaining_secs`).

### 4.3 Executor + ops wiring

- **Chosen mechanism:** `run_headless_turn` returns the override
  (`int | None`), read off `env._next_run_override` after the turn loop.
  `ops.run` already calls `executor(job)` in two places — the timeout path via a
  thread (`fut.result()`) and the inline path directly — and both can now
  capture that return value. `ops.run` passes the captured override into
  `m.next_run_at(...)` for the `Dynamic` branch. This keeps the store write in
  one place (`ops.run`, already the sole writer of `next_run_at`) and needs no
  side channel: the tool stamps intent on the env, the executor reads it, the
  return value carries it to `ops.run`.

  *Note:* today `ops.run` calls `m.next_run_at(job.schedule, now, replace(...))`.
  The `Dynamic` branch needs the override; we pass it as a new optional kwarg
  `override: int | None = None` to `next_run_at`, ignored by At/Every/Cron.
  Existing callers are unaffected (kwarg defaults to `None`).

- **Timeout/crash:** if the turn times out or raises, `ops.run` never receives an
  override → it stays `None` → `Dynamic` returns `None` → loop pauses. This is
  the fail-closed default, and it falls out of the existing control flow with no
  extra branch. (The existing `max_consecutive_failures` auto-disable still
  applies on top.)

### 4.4 `create_loop` agent tool — `harness/tools/create_loop.py`

- Sibling to `create_job`. Same persona binding (`env._active_persona`), same
  `handle_create_job` door, same CostGate/Grant gates.
- Schema args: `message` (the turn prompt — loops are always `AgentTurn`, never
  Reminder), `description`, `cost`, `grant`. No initial-delay arg (YAGNI): the
  first run is always immediate, and the first turn can push out its own next
  run via `set_next_run` if a delay is wanted.
- Builds a spec with `schedule = {"kind": "dynamic"}` and
  `payload = {"kind": "agent_turn", "message": ...}`.
- **Initial arming falls out for free:** `ops.add` already calls
  `m.next_run_at(schedule, now, state)` on the fresh state at creation. The
  `Dynamic` fresh-state branch returns `now`, so the loop is armed for the next
  tick with no special-casing in `create_loop`. No `override` is needed at
  creation time.

### 4.5 Registration

- Register `SetNextRunTool` and `CreateLoopTool` in `harness/tools/registry.py`
  following the existing pattern.
- `set_next_run` must be available to headless turns (it's how the loop steers).
  Verify it's in the toolset the executor's `build_persona_agent` exposes.

## 5. Data flow (one loop lifecycle)

1. User (in chat, as persona A): "keep checking the deploy and pace yourself."
2. Agent calls `create_loop(message="check the deploy…", cost=…, grant=…)`.
3. `handle_create_job` runs the four gates → `Job(schedule=Dynamic())` stored,
   `next_run_at` armed for the next tick.
4. Daemon tick: job is due → `ops.run` → `run_headless_turn`.
5. The turn runs as persona A (model/workspace/memory faithful). It inspects the
   deploy, decides "check again in 5 min," and calls
   `set_next_run(delay_seconds=300)`.
6. Turn ends. `ops.run` reads override=300, computes
   `next_run_at = now + max(300, min_cadence_s)`, stores it.
7. Daemon fires again ~5 min later. Repeat.
8. Eventually the turn decides work is done and simply **does not** call
   `set_next_run`. Override is `None` → `next_run_at = None` → loop pauses.
9. User can resume (re-arm) via the existing job ops / a future `dn` verb.

## 6. Error handling

| Situation | Behavior |
|---|---|
| Turn forgets `set_next_run` | override `None` → `next_run_at None` → **pause** |
| Turn crashes / raises | `ops.run` records error, override never set → **pause**; `consecutive_errors++`; existing `max_consecutive_failures` disables after N |
| Turn times out (CostGate) | same as crash → pause + error recorded |
| `delay_seconds` below `min_cadence_s` | floored to `min_cadence_s` |
| `delay_seconds` ≤ 0 or non-int | tool rejects (returncode 1), no override set → pause |
| Orphan persona | existing `OrphanPersona` path disables the job (unchanged) |

Everything reuses paths that already exist and are tested; the only genuinely
new behavior is "override present → arm; absent → pause," localized to the
`Dynamic` branch of `next_run_at` + the override capture in `ops.run`.

## 7. Testing

- **model.py:** `next_run_at` for `Dynamic` — override present (arms
  `now+override`), override floored by `min_cadence`, override absent (→ None),
  fresh state with/without initial delay. Round-trip `schedule_to_dict` /
  `schedule_from_dict` for `dynamic`.
- **set_next_run tool:** stamps `env._next_run_override`; rejects ≤0 / non-int;
  does not write the store.
- **ops.run integration:** a fake executor that sets an override → `next_run_at`
  advances; one that sets none → pauses; one that raises → pauses + error
  recorded + consecutive_errors increments; override below min_cadence floored.
- **create_loop tool:** builds a `Dynamic` `AgentTurn` job bound to
  `env._active_persona`; passes through the create-job gates; rejects when gates
  unsatisfied (reuses create_job gate tests' shape).
- **daemon:** a `Dynamic` job with `next_run_at` in the past is due and fires;
  after a no-override run it is no longer due (paused).
- All new tests live under `tests/jobs/` and `tests/tools/` mirroring existing
  layout. Run: `.venv/bin/python -m pytest tests/ -q`.

## 8. Rollout / compatibility

- Purely additive. `Dynamic` is a new union member; the three existing schedule
  kinds and all their serialization are untouched. Old stored jobs deserialize
  unchanged. `next_run_at`'s new `override` kwarg defaults to `None`, so every
  existing caller is behavior-preserving.
- No migration. No config flag needed — a loop only exists if a `create_loop`
  call made one.

## 8a. Review hardening (added after adversarial review)

Three finder passes surfaced a coupled critical bug and two footguns; all fixed
before merge, at the create gate (`handle_create_job`) so every schedule kind
benefits:

- **Poison-pill crash-loop (critical, two finders reproduced).** A normalized
  cost missing `min_cadence_s` yields `None`; `max(override, None)` in
  `next_run_at` raises `TypeError` — and that call runs OUTSIDE `ops.run`'s
  try/except, so the state write is skipped, `next_run_at` never advances, and
  the job re-fires + re-crashes every tick without ever incrementing the failure
  counter (undisableable). **Fix (defense in depth):** (1) `next_run_at`'s
  Dynamic branch uses `min_cadence_s or 0` so the call site never raises; (2) the
  gate rejects any cost with a `None` field fail-closed, so `None` never persists.
- **Tick-hammer footgun.** A Dynamic loop with `min_cadence_s=0` re-fires every
  30 s daemon tick. **Fix:** the gate requires a *positive* `min_cadence_s` for
  Dynamic schedules (parallels the existing Every cadence-floor check).
- **Dynamic + Reminder = one-shot "loop".** A Reminder never runs an LLM turn, so
  it can never call `set_next_run` → the loop pauses after one fire. Reachable via
  the raw `create_job` door (not `create_loop`). **Fix:** the gate rejects a
  Dynamic schedule paired with a Reminder payload.
- **Fractional delay.** `set_next_run(30.5)` now floors to 30 rather than
  rejecting (a rejection would pause the loop). Sub-second delays (`<1`) still
  reject as ≤0.

Explicitly kept as designed (not bugs): a paused loop does not auto-resume
(pause-when-done is the contract; resume is a manual op), and an "ok" turn that
omits `set_next_run` pauses without counting as an error.

## 9. Open questions (non-blocking)

- Whether `set_next_run` should also allow an absolute ISO time (like
  ScheduleWakeup's flexibility). **Default: no** — delay-seconds only, YAGNI;
  absolute time can be added later without breaking the interface.
- Resume UX (how a user un-pauses a paused loop) — out of scope for the engine;
  the existing enable/run ops cover it mechanically.
```
