# Jobs (Cron / Scheduled Tasks)

A **job** lets a persona do work on a schedule, unattended — a nightly backup, an
hourly health check, a Monday-morning reminder. A small background daemon
(`harness-cron`) watches the clock and, when a job is due, runs it **as the
persona that owns it**: same model, same workspace, same memory, same AGENTS.md
as if that persona had typed the prompt live.

> **New to jobs?** Start with the hands-on
> [first-job walkthrough](jobs-walkthrough.md) — create and run one end-to-end
> inside `dn` in a few minutes. This page is the reference behind it.

> This document describes what ships today (Phase 1). The "Not yet" section at the
> end lists what's deferred so you don't expect it — most importantly, the
> permission `grant` fields are **recorded but not yet enforced at runtime**.

Jobs sit alongside the harness's other context sources:

- **Skills** are *task-knowledge* (router-selected per request).
- **Personas** are *identity* (one persona per session — see
  [personas.md](personas.md)).
- **Jobs** are *scheduled identity* — a saved instruction plus a schedule, bound
  to one persona, run for you by the daemon.

## How a job is bound to a persona

Every job carries a required `agent_id` — the id of the persona it runs as. This
is the single binding mechanism; there is no second knob.

When the daemon fires a job it resolves everything **from that `agent_id`**, so a
scheduled run for persona `alberto` is byte-for-byte the same turn `alberto`
would produce interactively:

| What | Where it comes from |
|---|---|
| **Workspace** | `resolve_workspace(agent_id)` → the persona's `~/.config/harness/agents/<agent_id>/` directory |
| **Model** | `resolve_session_model(agent_id)` → daemon env / global `.env` `PROXY_MODEL` or `VIBEPROXY_MODEL`, else the persona's model in `done.conf [agents.<agent_id>]`, else the engine default |
| **Persona block** | `SOUL.md` / `IDENTITY.md` / `USER.md` from that workspace |
| **Memory block** | the persona's memory (see [memory.md](memory.md)) |
| **AGENTS.md** | three-tier: persona ▸ project ▸ global |

If the persona named by `agent_id` no longer exists, the daemon **auto-disables
the job** on its next due tick (without recording a failed run) rather than
running it as someone else.

## Viewing your jobs (the TUI dashboard)

There is **no `dn jobs list` CLI** in this phase — jobs are viewed in the TUI.

From inside the TUI (`dn`), press **`Ctrl+J`** to toggle the **cron drawer**. It
has two parts:

- **Daemon-status header** — a line above the roster showing whether the
  `harness-cron` daemon is running: `✓ daemon running — jobs will fire` (green),
  `⚠ daemon running but ticks are failing` / `⚠ daemon stalled …` (yellow), or
  `✗ daemon not running — scheduled jobs won't fire` (red). This is how you tell
  at a glance whether your scheduled jobs are actually armed.
- **Dashboard** — one row per job: `● {name} · {status} · {when}`. Status is
  `scheduled`, `running`, or `disabled`; the `{when}` column says `due`, `<1m`,
  `in 8h`, and so on.
- **Detail chart** — selecting a job draws a bar chart of its recent run
  durations (read from the job's run log).

The header is read each time you open the drawer. It works off a heartbeat the
daemon writes every tick (`cron/ticker_heartbeat` + `cron/ticker_success`): a
fresh heartbeat means running, a stale or missing one means stopped/stalled.

Keys while the dashboard is focused:

| Key | Action |
|---|---|
| `r` | **Run now** — fire the selected job immediately (ignores the schedule) |
| `t` | **Toggle enabled** — pause / resume the job |
| `Backspace` | **Remove** the selected job |

(The dashboard has no create key — see "Creating a job" below.)

## Creating a job

Job creation is **agent-native**: you just ask, in chat, for the job you want —
e.g. *"remind me every Monday at 9am to review deploy metrics"* or *"every night
at 2am, back up the database to /backups"*. The router loads the **`create-job`
skill**, which turns your plain-language intent into a real job.

The skill is **guess-first**, not an interrogation. It applies safe defaults for
anything you didn't specify, and asks a follow-up **only** when it can't tell
*when* the job should run, or when the job needs a **risky permission** (shell
commands, network access, or writing outside the project) — those it confirms
before granting. A plain reminder is created with no questions.

Defaults the skill fills for you:

| Setting | Default |
|---|---|
| Timeout | 300s (5 min); longer if the action implies it |
| Min-cadence | derived from the schedule |
| Max failures | 3 |
| Permissions (`grant`) | none — widened only after you confirm a risky one |

Under the hood the skill calls the **`create_job` tool**, which re-validates the
spec fail-closed (cost + grant must be present) and writes the job. That tool is
the only way a job is written.

### Specifying the schedule

A job's `schedule` is one of three shapes:

| Kind | Form | Example | Meaning |
|---|---|---|---|
| **Cron** | 5-field cron string, optional `tz` | `"0 2 * * *"` | 2:00 every day |
| **Every** | fixed interval in seconds | `3600` | every hour |
| **At** | ISO-8601 timestamp | `"2026-06-29T10:00:00"` | once, then never again |

The cron syntax is standard 5-field (`minute hour day-of-month month
day-of-week`) parsed by `croniter`. For `Every` schedules, the create gate
rejects any interval below `cost.min_cadence_secs` (the cron case isn't
floor-checked yet — see "Not yet").

### Payload

A job's `payload` is either an **agent turn** (a message the persona runs as a
full LLM turn) or a **reminder** (a text-only notification — no inference).

## Running the daemon

Jobs only fire while the `harness-cron` daemon is running. It is a separate
process from the TUI.

### OS service (durable — survives reboot)

The primary way to keep the daemon running is to register it as an OS service:

```sh
dn cron install      # macOS: launchd LaunchAgent; Linux: systemd user service
dn cron status       # show whether the service is installed/active
dn cron uninstall    # remove it
```

On macOS, `install` writes a LaunchAgent plist at
`~/Library/LaunchAgents/com.quiubo.done.cron.plist` with `RunAtLoad` and
`KeepAlive`, then loads it immediately via `launchctl bootstrap`. The daemon
starts at login and is restarted on crash.

On Linux, `install` writes a systemd user unit at
`~/.config/systemd/user/harness-cron.service` (`Restart=always`,
`RestartSec=5`), enables it with `systemctl --user enable --now`, and calls
`loginctl enable-linger` so the service survives logout and reboot.

The **first time you launch `dn`** it offers to install the service for you.

### TUI fallback spawn (best-effort — no reboot survival)

If you decline the first-run prompt, or if you're on an unsupported platform,
`dn` falls back to spawning `harness-cron` as a background process when you open
a window. The daemon is single-instance (several `dn` windows share one), but it
**will not** survive a reboot or fire when all windows are closed. Run
`dn cron install` any time to make it permanent.

### Manual / headless invocation

For headless use or a custom tick cadence, run the daemon directly:

```sh
harness-cron               # run forever, ticking every 30 s
harness-cron --interval 60 # tick every 60 s instead
harness-cron --once        # fire one tick (all due jobs) and exit
```

The Ctrl+J panel's daemon-status header shows whether ticks are actively firing.

On each tick the daemon selects every enabled job whose next run time has passed,
runs it as its persona, records the outcome, recomputes the next run time, and
auto-disables any job that has hit its consecutive-failure limit (or whose
persona has gone missing). A failing tick is logged and the loop continues — one
bad job never takes the daemon down.

The daemon loads `~/.config/harness/.env` at startup (so a global `PROXY_MODEL`
or legacy `VIBEPROXY_MODEL` is honored). It has no project directory, so
project-local `.env` files are not loaded for scheduled runs.

## Where jobs live on disk

All under the harness config dir (`$XDG_CONFIG_HOME/harness`, default
`~/.config/harness`):

| Path | Contents |
|---|---|
| `cron/jobs.json` | the job roster (versioned, lock-guarded JSON) |
| `cron/runs/<job_id>.jsonl` | per-job run history (one JSON line per run) — the source for the detail chart |

You generally don't hand-edit `jobs.json`; create and manage jobs through the
skill and dashboard so the gates and locking are respected.

## Not yet (deferred)

- **Permission enforcement.** The `grant` fields (`paths`, `shell`, `tools`,
  `network`) are **recorded as a declared scope but not enforced at runtime** — a
  job's effective access today is "whatever the persona could do," not what
  `grant` says. Collecting them is still correct (it's an auditable record and
  slots into enforcement once it lands), but **do not treat the Permissions Gate
  as a security boundary yet.** Prefer narrow, low-privilege scheduled tasks.
- **Sub-floor cron rejection.** The min-cadence floor is enforced for `Every`
  schedules only; an over-frequent `Cron` expression isn't rejected yet.
- **Job-list / management CLI.** Viewing and managing jobs is TUI-only in this
  phase.
- **Stopping the daemon from the TUI.** `done` reports daemon status in the Ctrl+J
  header and may spawn `harness-cron` as a fallback background process, but
  there's no in-TUI way to *stop* it — kill the `harness-cron` process yourself,
  or run `dn cron uninstall` to remove the OS service
  ([#146](https://github.com/albertovasquez/done/issues/146)).
- **Trace events.** `cron.fire` / `cron.tick` / `cron.error` are reserved in the
  debug-trace model but not yet emitted.
