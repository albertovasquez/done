---
name: create-job
description: Use when the user wants to create a scheduled cron job, recurring task, or reminder
---

# Create Job (Cron/Scheduled Tasks)

## Overview

A cron job is a privileged operation: it runs automatically on a schedule without user confirmation. This skill documents the gate procedure you must follow before creating any job.

**Core principle:** Every gate must be answered. Never create a job unless every gate has approval.

## The Gate Procedure

Creating a job requires passing four gates: **cost**, **cadence**, **failure handling**, and **permissions**. You apply these gates BEFORE calling `harness/create_job`.

### 1. Cost Gate: Timeout

Every job run has a **timeout** — the maximum wall-clock time the agent may spend on one execution.

**Questions to answer:**
- How long should a single run be allowed to execute?
- What is a reasonable timeout for this specific task?
- Is the timeout based on typical/worst-case runtime plus buffer?

**Example:**
- A daily backup job: timeout = 10 minutes (15 min worst-case, 5 min buffer)
- An hourly health check: timeout = 30 seconds
- A weekly report generation: timeout = 30 minutes

**Never proceed without a specific timeout.** If the user says "reasonable," ask: "Typically how long does this take?"

### 2. Cadence Gate: Min-Cadence

Every job has a **minimum frequency floor** — the closest interval a job should run.

**Questions to answer:**
- What is the minimum time between runs?
- Is hourly too frequent? Daily? Weekly?
- Can the downstream system handle this frequency?

**Examples:**
- "Run every 6 hours" → min-cadence = 6 hours
- "Run every Monday" → min-cadence = 1 week
- "Run every 30 minutes" → min-cadence = 30 minutes

**Never allow runaway schedules.** If the user says "very often," ask: "Specifically, how often? Every hour? Every minute?"

### 3. Failure Gate: Max Consecutive Failures

Every job has a **maximum consecutive failure limit** — if the job fails that many times in a row, it auto-disables to prevent a broken job from hammering a system.

**Questions to answer:**
- How many consecutive failures before auto-disabling?
- What is the acceptable failure tolerance?
- Is the system set up to alert on auto-disable?

**Examples:**
- A backup job: 3 consecutive failures → disable (alert the team)
- A health check: 5 consecutive failures → disable (too flaky to trust)
- A data sync: 10 consecutive failures → disable (wait for manual review)

**Never allow unlimited retries.** If the user says "robust," ask: "Specifically, how many failures can you tolerate before giving up?"

### 4. Permissions Gate

Every job declares what permissions it needs: file tools, shell commands, network, LLM cost.

**Questions to answer:**
- What paths does this job access (read/write)?
- What external tools or APIs does it call?
- Does it execute shell commands?
- What is its LLM cost budget (if any)?

**Example permission spec:**
```
grant:
  paths: [/var/backups/db, /home/user/restore]
  shell: true
  tools: ["bash", "file-read", "file-write"]
  network: false
```

## Fail Closed

**CRITICAL: Fail closed is not optional. It is the rule.**

If ANY gate is unanswered or unclear, **do not create the job.** Return to the user with specific questions:

```
I cannot create this job yet. Before I proceed:

1. Timeout: You said "fast" — specifically, how many seconds/minutes should one run take?
2. Min-cadence: You said "often" — specifically, every hour? Every 10 minutes?
3. Consecutive failures: How many failures before I should auto-disable and alert you?
4. Permissions: Does this job need to write to any paths outside the project?
```

Only when ALL four gates are answered do you proceed to the next step.

## Implementation: Call harness/create_job

Once every gate is answered, call `harness/create_job` with this structure:

```python
{
    "agent_id": "the-persona-or-agent-id",
    "cost": {
        "timeout_secs": <integer>,
        "min_cadence_secs": <integer>,
        "max_consecutive_failures": <integer>,
    },
    "grant": {
        "paths": [<absolute paths>],
        "shell": <true|false>,
        "tools": [<tool names>],
        "network": <true|false>,
    },
    "schedule": "0 2 * * *",  # cron schedule or interval
    "description": "what this job does",
}
```

The `harness/create_job` method is the ONLY way to create a cron job. It is the single-door privileged write path. The method validates every gate and returns `{"ok": true, "job_id": ...}` on success or `{"ok": false, "error": "..."}` on failure.

## Example Walkthrough

**User:** "I want a daily backup job."

**You:**
1. **Timeout:** "How long should a backup run typically take?" User: "10 minutes tops." → timeout = 600 seconds.
2. **Min-cadence:** "What time of day?" User: "2 AM." → min-cadence = 86400 seconds (1 day).
3. **Consecutive failures:** "If the backup fails 3 days in a row, should I disable it?" User: "Yes, alert me." → max_consecutive_failures = 3.
4. **Permissions:** "This needs to read `/var/www` and write to `/backups/daily`, right?" User: "Correct." → grant = {paths: [...], ...}.

All gates answered. Now call `harness/create_job`.

## Key Points

- **Timeout:** Always specific (seconds/minutes), never vague ("reasonable," "fast").
- **Min-cadence:** Always specific interval (seconds, hours, days), never vague ("often").
- **Consecutive failures:** Always a specific number, never unlimited.
- **Permissions:** Always scoped to actual paths/tools, never a blanket "full access."
- **Fail closed:** No job until every gate is answered. This is the law.
- **Implementation:** The ONLY way to create a job is via `harness/create_job`. No direct ops.add calls. No shortcuts.

## Common Mistakes

**❌ Wrong:** "I'll set a generous timeout." → Unanswered. Ask "How many minutes?"
**❌ Wrong:** "I'll allow 10 failures." → Unanswered until you know WHY. Is that the right number for THIS job?
**❌ Wrong:** Calling code directly instead of `harness/create_job` → Wrong gate. Always use the ext-method.
**✓ Right:** Ask all four questions. Get specific numbers. Call `harness/create_job` once all gates are answered.
