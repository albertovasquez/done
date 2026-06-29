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

The **cron drawer** opens. If you've never made a job, the roster is empty.

## Step 2 — start the create flow

With the dashboard focused, press **`n`**.

`done` closes the drawer and drops a seed message into the chat —
*"I want to create a scheduled cron job."* — then hands off to the agent, which
loads the **`create-job`** skill and begins asking you questions.

> You can also just type the request yourself in chat (e.g. *"create a daily
> reminder job"*) instead of pressing `n`. Same skill, same gates.

## Step 3 — answer the four gates

A job runs unattended with no per-run confirmation, so the skill **will not
create anything until you answer four gates**. It fails closed: vague answers
get bounced back as specific questions. For our reminder, answer like this:

| Gate | What it asks | Answer for this job |
|---|---|---|
| **Timeout** | max wall-clock seconds for one run | `60` |
| **Min-cadence** | the closest interval it may run | `86400` (once a day) |
| **Max failures** | consecutive failures before auto-disable | `3` |
| **Permissions** | paths / shell / tools / network it needs | *none — it's a text reminder* |

You'll also tell the agent **what the job does** and **when**. Say something
like:

> A daily reminder at 9am that says: "stand-up in 15 minutes."

The agent turns "9am daily" into the cron schedule `0 9 * * *`.

When all four gates are satisfied, the agent calls the single privileged door
(`harness/create_job`) and confirms with a **job id**. Your job now lives in
`~/.config/harness/cron/jobs.json`.

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
| `n` | create another |

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
| Create a job | `n` → answer the four gates in chat |
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
