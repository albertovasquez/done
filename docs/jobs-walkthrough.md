# Your first cron job — a walkthrough

This is a hands-on, step-by-step guide to creating and running your **first
scheduled job**, done entirely from inside the `done` TUI. For the full
reference (data model, persona binding, schedule shapes, on-disk layout) see
[jobs.md](jobs.md).

We'll create a low-risk **daily reminder**, watch it appear in the roster, then
fire it once on demand — so you see it work without waiting for its scheduled
time.

> **What's in-app vs. not.** Creating, viewing, running-now, toggling, and
> removing jobs all happen inside `done`. The one thing that lives *outside* the
> TUI is the **`harness-cron` daemon** — the process that fires jobs at their
> scheduled time while you're away. This walkthrough doesn't need it (we use
> "run now"); the last section covers turning it on. (Making the TUI manage the
> daemon is tracked as a follow-up.)

## Before you start

You need the `done` TUI open. That's it — the daemon is not required for this
walkthrough.

## Step 1 — open the cron dashboard

In `done`, press **`Ctrl+J`**.

The **cron drawer** opens. If you've never made a job, the roster is empty. (This
is just to view jobs — you create them in chat, next.)

## Step 2 — just ask for the job in chat

Job creation is **agent-native** — you don't press a key, you just say what you
want. In the chat composer, type something like:

> Remind me every weekday at 9am: stand-up in 15 minutes.

The router loads the **`create-job`** skill and the agent turns that into a real
job.

## Step 3 — the agent fills in the details

The skill is **guess-first**: it applies safe defaults and only asks a follow-up
when it genuinely needs to. For a plain reminder like ours, it asks **nothing** —
it already has the schedule ("every weekday at 9am" → `0 9 * * 1-5`) and the
message, and everything else gets a safe default:

| Setting | Default it uses |
|---|---|
| Timeout | 300s (5 min) |
| Min-cadence | derived from the schedule (daily) |
| Max failures | 3 |
| Permissions | none — it's a text reminder |

It only stops to ask if your request is missing the **schedule** ("remind me" —
when?), or needs a **risky permission** (shell, network, or writing outside the
project) — which it confirms before granting.

The agent calls the **`create_job` tool** and confirms with a **job id**. Your job
now lives in `~/.config/harness/cron/jobs.json`.

### Which persona owns it?

The job is bound to the persona you're currently chatting as (its `agent_id`).
When it fires, it runs **as that persona** — same model, workspace, and memory.
To create it for a *different* persona, switch to that persona first (the agents
rail), or name the target persona when you ask. See
[personas.md](personas.md).

## Step 4 — see it in the roster

Press **`Ctrl+J`** again. Your job now shows as:

```
● stand-up reminder · new · <next run time>
```

The status word starts at `new` (never run). Selecting the job draws a small
chart of its run durations — empty for now, since it hasn't run.

## Step 5 — run it now (no daemon needed)

Select the job and press **`r`** (run now).

This fires the job **immediately**, inside `done`, ignoring the schedule. Watch
the status flip to `ok` and a first bar appear in the detail chart. That's your
proof the job works — no waiting until 9am, no daemon required.

Other roster keys while you're here:

| Key | Action |
|---|---|
| `r` | run now |
| `t` | enable / disable (pause without deleting) |
| `Backspace` | remove the job |

(To create another, just ask in chat — there's no create key.)

## Step 6 (optional) — let it fire on its own

"Run now" is manual. For the job to fire **at 9am on its own**, the
**`harness-cron`** daemon has to be running — it's a separate process that checks
the clock and fires due jobs:

```sh
harness-cron            # runs forever, checks every 30s
harness-cron --once     # fire all currently-due jobs once, then exit
```

Leave `harness-cron` running (in a terminal, a `launchd`/`systemd` unit, or a
`tmux` pane) and your scheduled jobs fire unattended. Stop it and only "run now"
works. If you started `done` from a Claude Code session, you can launch the
daemon here with `! harness-cron`.

**How to tell it's working:** reopen the dashboard (`Ctrl+J`). The header line
above the roster reads `✓ daemon running — jobs will fire` when the daemon is up,
and `✗ daemon not running — scheduled jobs won't fire` when it isn't — so you
never have to guess whether your jobs are armed.

## Recap

| Action | How |
|---|---|
| Open dashboard | `Ctrl+J` |
| Create a job | ask in chat (e.g. *"remind me every weekday at 9am…"*) |
| View jobs | `Ctrl+J` (roster + run chart) |
| Run a job now | select → `r` |
| Pause / resume | select → `t` |
| Delete | select → `Backspace` |
| Fire on schedule | run `harness-cron` |

Everything except scheduled firing is 100% inside `done`. For the why and the
internals, head to [jobs.md](jobs.md).

> **Phase 1 caveat:** the permission `grant` is **recorded but not yet enforced
> at runtime** — a scheduled job can currently do whatever its persona could.
> Keep unattended jobs narrow and low-privilege until enforcement ships.
