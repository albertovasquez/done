# Agent Jobs Dashboard (Phase 1) — Design

**Date:** 2026-07-01
**Status:** Approved design, pre-implementation
**Branch/worktree:** `feat/agent-jobs-dashboard` @ `.worktrees/agent-jobs-dashboard`

## Vision

Make **visualizing your long-running work** a primary use case for Done. From the
agents rail ("N agents online"), select an agent and press **J** to open a
first-class **AgentDashboard** screen for that agent: a rich jobs table
(TASK · STATUS · PROGRESS · ELAPSED) plus a chat rail scoped to that agent. This
turns Done from "a chat with an agent" into "a control room for your agents'
durable work" — cron jobs, scheduled tasks, self-paced loops, and goals.

Reference mockup: an agents panel on the right with per-agent status, an ACTIVE
AGENT header, a CURRENT TASKS table, and a MESSAGE / COMMAND chat box. (Names
Fred/Sam/Robbie and "DoneDone" in the mockup are illustrative; the real screen
renders live persona names.)

### Honesty caveats (from caveman-review; verified against live code)

The mockup shows metrics that **have no truthful source in Phase 1** — we do NOT
fabricate them (per the #252 "no untruthful bar" rule):

- **Progress bars (64%, 32%)** — there is NO progress fraction anywhere in the
  jobs backend. `Job`, `JobState`, and `JobRun` carry no `progress`/`steps`
  field; a running job is just an in-flight daemon turn with `running_since`.
  **In Phase 1, every job's `progress` is `None` → the PROGRESS column renders
  `—` for all rows.** The bar UI is built but only draws when a real signal is
  later wired (e.g. a goal's N/M). We snapshot the honest table (STATUS · ELAPSED
  · `—`), not the mockup's fictional bars.
- **Header `uptime` / `load`** — no source exists (no agent uptime or loadavg in
  the codebase). The ACTIVE AGENT header shows only what is real: **name ·
  state** (and, for a running job, live elapsed). No uptime/load fields.

## Scope (Phase 1: jobs-first)

**In scope** — durable/long-running work owned by the jobs backend, scoped per
agent:
- cron jobs, scheduled (`At`/`Every`), dynamic self-paced loops, and goals
- read from `harness.jobs` (`ops.list_jobs(agent_id=…)` already exists)
- the rich table, per-agent scoping, truthful progress, and a scoped chat rail

**Out of scope** (later specs):
- merging live interactive `TaskItem`s from an in-flight turn into the same table
- goals surfaced as first-class rows beyond what the jobs backend exposes
- rich free-form job mutation from chat beyond a small verb set

## Why this is grounded (existing infrastructure)

Verified against live code before writing:

- `harness/jobs/ops.py::list_jobs(include_disabled, agent_id)` — **scoped
  listing already exists.** No new query layer needed.
- `harness/jobs/model.py::Job` — `id, name, agent_id, description, enabled,
  schedule (At/Every/Cron/Dynamic), payload`.
- `harness/jobs/model.py::JobState` — `next_run_at, running_since, last_run_at,
  last_status, last_error, last_duration, consecutive_errors, version`. Every
  `JobRow` field has a real source.
- `harness/tui/widgets/agent_rail.py::AgentRail` — the rail already renders
  per-agent status cards with state dots; this IS the "N agents online" panel.
- `harness/tui/state.py::AgentSnapshot` — already carries `state, tasks, elapsed,
  tokens, schedule`.
- Today `Ctrl+J` opens a flat, global `CronDashboard` (one-line rows, not
  agent-scoped). Phase 1 introduces the agent-scoped rich screen alongside it;
  the old drawer is not removed in this phase (avoid a big behavior swap mid-build).

## Architecture (deep modules, testable seams)

Four units, each independently understandable and testable:

### 1. `harness/jobs/view.py` — view model (PURE, no Textual)

```
JobRow(name, description, status, progress, when, elapsed)   # a frozen dataclass
job_rows(agent_id: str, now: float) -> tuple[JobRow, ...]
```

Reads `ops.list_jobs(agent_id=agent_id)` and maps each `Job` + `JobState` to a
`JobRow`. Pure and Textual-free, so it is unit-testable in isolation and the UI
stays dumb. **This is the key deepening seam.**

- `status`: derived — `RUNNING` (running_since set), `SCHEDULED` (enabled +
  next_run_at), `DISABLED` (not enabled), `COMPLETED`/`FAILED` (from last_status
  when not currently armed). One derivation function, unit-tested per branch.
  `QUEUED` (enabled + due now, not yet running) is modeled but **rare in practice**
  — the daemon fires due jobs within a tick, so it is a transient the user
  seldom sees; no dedicated UI ceremony or snapshot state for it.
- `progress: float | None` — **always `None` in Phase 1** (no fraction source
  exists in the backend, verified). The field and bar-rendering exist so a future
  real signal (goal N/M, or a job-run fraction if the backend gains one) can
  populate it, but P1 renders `—` for every row. No elapsed-based guessing (per
  the #252 "no untruthful bar" lesson).
- `when`: scheduled → `in 2d 14h` from next_run_at; running → live elapsed from
  running_since; done → `last_run_at` relative.
- `elapsed`: running → `HH:MM:SS` from running_since; done → `last_duration`;
  else `—`.

### 2. `harness/tui/widgets/jobs_table.py` — table widget (DUMB/reactive)

`set_rows(rows: tuple[JobRow, ...])`. Renders TASK · STATUS · PROGRESS · ELAPSED
using design-system tokens: status chips (colored pills), a truthful progress bar
(rendered only when `progress is not None`, else `—`), per-row state dot glyphs.
No data access — takes rows, renders. Snapshot-tested.

### 3. `harness/tui/screens/agent_dashboard.py` — the screen (composition)

Composes `AgentRail` (right), an `ActiveAgent` header (**name · state** only —
no uptime/load, no truthful source), `JobsTable`, and a `MESSAGE / COMMAND`
input. Owns open/close, agent-switching (re-render table for the newly selected
agent), and forwarding input submissions.

### 4. Wiring in `harness/tui/app.py`

A new `J` binding (verified free — only `n` is bound on the rail; `ctrl+j` is the
old cron drawer) when an agent row is focused/selected in the rail →
`push_screen(AgentDashboard(agent_id))`. Reuses the rail's existing selection and
`PersonaSelected` message. If `J` proves undeliverable from a focused `ListView`
row, fall back to `enter` (the rail's existing select) opening the dashboard.

Data flows one way: `jobs.store` → `view.job_rows()` → `JobsTable`.

## The input rail — command-first (from caveman-review)

The MESSAGE / COMMAND box in Phase 1 is **command-first**, not free chat. It
drives job verbs against the selected agent's jobs via the existing `ops`
(`run` / `update` for enable-disable / `remove`) — which need **no agent
session**. This deliberately avoids the session-lifecycle problem: the selected
agent may be a scheduled persona with no live session, so "route a prompt to it"
would require spinning up a session — out of scope for P1.

Free-form chat to the selected agent (with the session lifecycle that implies) is
a later phase; the box's placeholder in P1 reflects command intent ("pause nightly
sync · run now · disable"). This keeps P1c small and deterministic.

## UX build method — snapshot-driven, state by state

We build each screen state against **fake `JobRow` data first**, snapshot-test
it, iterate the render until it matches the mockup + the written style guide
(`components.md`, the tui-design-system spec), THEN wire real jobs. The snapshot
tests ARE the design surface — this is exactly what the visual-snapshot net
(PR #251) was built for.

States to snapshot (each a `snap_compare` test driven by injected fake rows —
hermetic, no jobs backend, no daemon):

1. **running + mixed** — a RUNNING job (live elapsed, `—` progress), SCHEDULED
   rows, a COMPLETED row. The honest table, NOT the mockup's fictional bars.
2. **all-idle** — agent online, no active jobs.
3. **scheduled-only** — cron/scheduled jobs, none running.
4. **empty** — no jobs (a clean "nothing scheduled" state).
5. **command input focused** — the MESSAGE / COMMAND input active.

Plus Pilot tests for behavior: J opens the screen for the focused agent, agent
switch re-renders the table, a command submit invokes the right `ops` verb.
Baselines judged
against the style guide before commit (baseline-acceptance rule). New status-chip
/ table components get catalog entries in `components.md` per AGENTS.md §7.

## Phasing (one spec, built in slices)

- **P1a** — `view.py` (pure view model, unit-tested per status branch) +
  `JobsTable` widget, snapshot-driven against fake rows. The beautiful UX, no
  screen wiring yet.
- **P1b** — `AgentDashboard` screen + `J` navigation, wired to real
  `ops.list_jobs(agent_id)`. Live data on the beautiful table.
- **P1c** — the command-first input rail (job verbs via `ops`: run / disable /
  remove against the selected agent's jobs; no agent session needed).

Later specs: free-form chat to the selected agent (with session lifecycle),
live-task merge, goals-as-rows, and possibly retiring the old global `Ctrl+J`
drawer once the agent-scoped view supersedes it.

## Error handling

- `view.job_rows` on a missing/empty store → empty tuple (renders the "empty"
  state), never raises into the UI.
- A job whose `agent_id` no longer resolves is simply absent from that agent's
  rows (the daemon already auto-disables orphans).
- Progress with no truthful source → `None` → `—`. Never a fabricated bar.

## Testing & verification

- **Unit:** `view.job_rows` / status-derivation per branch (running, scheduled,
  queued, disabled, completed, failed) — pure, fast, no Textual.
- **Snapshot:** the 5 screen states above, judged vs the style guide.
- **Pilot:** J-opens-screen, agent-switch-re-renders, chat-submit-routes.
- Full suite green before any PR; caveman-review (inline) on each slice's diff.

## Success criteria

- P1a: `JobsTable` renders all 5 states correctly (snapshots judged vs style
  guide + committed) and `view.job_rows` maps every real `Job`/`JobState` field
  with truthful progress.
- P1b: pressing J on a focused agent opens its dashboard showing that agent's
  real jobs; switching agents re-renders.
- P1c: a command in the dashboard input invokes the correct `ops` verb on the
  selected agent's job (run / disable / remove), verified without a live session.

## Test command

`.venv/bin/python -m pytest tests/ -q` (from the worktree root; shared `.venv`).
