---
name: create-job
description: Use when the user wants to create a scheduled cron job, recurring task, or reminder
---

# Create Job (Cron/Scheduled Tasks)

## Overview

A cron job runs automatically on a schedule. Your job is to turn the user's
plain-language intent — **what** they want and **how often** — into a real
scheduled job, calling the `create_job` tool.

**Core principle:** Be helpful, not bureaucratic. Collect the user's intent,
**apply safe defaults for anything they didn't specify**, and create the job.
Ask a follow-up **only** when you genuinely can't proceed — see "When to ask"
below. Do NOT interrogate the user for every parameter.

## What you need

A job has four kinds of settings. You almost always only need the user to tell
you the **schedule** (and what the job does) — the rest have safe defaults you
apply yourself.

| Setting | How to fill it |
|---|---|
| **Schedule** (when/how often) | From the user. The one thing you usually must know. |
| **Timeout** (max time per run) | **Default 300s (5 min).** Use longer only if the action clearly implies it (a backup, a big report/sync → e.g. 600–1800s). |
| **Min-cadence** (frequency floor) | **Derive from the schedule** (daily → 86400, hourly → 3600, every N min → N×60). |
| **Max consecutive failures** | **Default 3.** |
| **Permissions** (`grant`) | **Default none** — `paths: []`, `shell: false`, `network: false`, `tools: []`. That's correct for a reminder or read-only check. Only widen it if the action obviously needs it. |

> **Note (v1 — grant fields are recorded, not enforced):** The `grant` fields are
> stored as a declared scope (auditable metadata) but the harness does **not yet
> enforce them at runtime** — a job's effective access is "whatever the persona
> could do." Defaulting permissions to none is therefore safe today; it does not
> restrict the job, it just records intent. Enforcement is a later phase, at which
> point the "ask before a risky grant" rule below becomes the real guard.

## When to ask (the ONLY two triggers)

Ask a focused follow-up **only** in these cases. Otherwise, create the job with
defaults — no questions.

1. **The schedule is unclear.** The user said "remind me" / "run a check" with no
   usable *when*. → Ask one question: *"How often, or at what time?"*

2. **The job needs a risky permission.** The action requires **shell commands**,
   **network access** (any call to an external service or API — fetching PRs,
   weather, a webpage, posting a message), or **writing to paths outside the
   current project**. → Confirm that one thing explicitly, e.g. *"This will fetch
   from GitHub's API — okay to grant network access?"* or *"This will run shell
   commands and write to `/backups` — okay?"* Then set the matching grant field
   (`network: true`, `shell: true`, or the path).
   - Only a **purely local, read-only** job (a plain reminder, or reading a file
     inside the project) is exempt and needs no question. A job that *looks*
     read-only but reaches the network ("summarize my open PRs") is **not**
     exempt — confirm the network grant.

That's it. Never re-print a list of four gates. Never refuse a job that only
lacks a default-able value.

## Implementation: Call the `create_job` tool

Once you know the schedule (and have confirmed any risky permission), call the
**`create_job` tool**. Fill the defaults yourself for anything the user didn't
specify:

```json
{
    "schedule": "0 9 * * 1-5",
    "description": "stand-up reminder",
    "cost": {
        "timeout_secs": 300,
        "min_cadence_secs": 86400,
        "max_consecutive_failures": 3
    },
    "grant": {
        "paths": [],
        "shell": false,
        "network": false,
        "tools": []
    }
}
```

- **Do NOT pass `agent_id`** — the tool binds the job to the persona you are
  currently acting as, automatically.
- `schedule` accepts a plain 5-field cron string (`"0 9 * * 1-5"`), an interval
  in seconds, or an ISO-8601 timestamp for a one-shot. Translate the user's
  phrase into one of these (e.g. "every weekday at 9am" → `"0 9 * * 1-5"`,
  "every 6 hours" → `21600`).
- The tool returns `Created job <id> …` on success, or `Could not create job:
  <reason>` if something is genuinely invalid — in which case fix it (or ask the
  user for the one missing piece) and call again. **Once it returns "Created
  job", the turn is complete; do NOT re-ask anything, do NOT call filler tools,
  and do NOT create the job again.**

## Examples

Most jobs are **high-level human tasks**, not scripts. Lead with those. A job
can be a plain reminder/check the agent performs on schedule — the user does not
need to hand you a command. Only treat it as a script/command job if the user
actually gives you one.

**User:** "Remind me every Monday at 9am to review deploy metrics."
→ Schedule clear, no permission needed. Create immediately:
`schedule="0 9 * * 1"`, `description="review deploy metrics"`, timeout 300,
max-fail 3, grant none. Report the job id. **No questions.**

**User:** "Every morning, give me a summary of my open PRs."
→ Schedule = daily (pick a morning time, e.g. 9am → `"0 9 * * *"`). This LOOKS
read-only but it reaches GitHub over the network, so confirm that one thing:
*"This will fetch your PRs from GitHub — okay to grant network access?"* On yes,
create with `network: true` (everything else default).

**User:** "Set up a reminder."
→ Schedule unclear. Ask once: *"Sure — how often, or at what time?"* Then create.

**User (developer, gives a command):** "Run `python sync.py` every 6 hours."
→ Now it's a script job: `schedule=21600`, and since it runs shell, confirm:
*"This will run a shell command — okay to allow that?"* Then create with
`shell: true`.

Prefer phrasing your own suggestions as **high-level tasks** ("remind me to…",
"every morning, summarize…") rather than scripts. Developers can give you a
command if they want one; everyone else just describes what they want.

## Key Points

- **Guess, don't interrogate.** Apply the defaults table. Ask only for an unclear
  schedule or a risky permission.
- **Translate phrases to schedules** yourself ("weekdays at 9" → `0 9 * * 1-5`).
- **One way to create:** the `create_job` tool. No direct `ops.add`; do not use
  bash to create jobs.
- **Stop after success.** "Created job …" means done. Do not re-announce it,
  do not call harmless/filler tools, and do not create it again.
