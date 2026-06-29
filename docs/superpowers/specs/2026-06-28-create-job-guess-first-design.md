# Create-job skill: guess-first, ask-only-when-needed — design

**Status:** approved (brainstorming) — ready for plan
**Date:** 2026-06-28
**Fixes:** the create-job gate LOOP — the skill re-prints the four-gate template
forever instead of creating the job, even when the user gave a reasonable request.

## Problem

`harness/skills/create-job/SKILL.md` is rigidly fail-closed:

- *"Every gate must be answered. Never create a job unless every gate has approval."*
- *"Fail Closed... If ANY gate is unanswered or unclear, do not create the job.
  Return to the user with specific questions."*
- *"Only when ALL four gates are answered do you proceed."*

This forces the model to treat any request lacking explicit values for all four
gates (timeout, cadence, max-failures, permissions) as "fail closed → re-ask".
For a normal human request ("remind me about standup every weekday at 9"), the
model judges timeout/failures/permissions "unanswered or unclear" and loops,
re-printing the gate template indefinitely. **The gates are sound; the rigidity
manufactures the loop.**

The `create_job` tool (PR #159) and its normalization already exist and work —
this is purely the skill *behavior* that drives the model.

## Decision (brainstorming)

Flip the skill's default from *demand-everything* to **guess-sensibly,
confirm-the-risky**: apply safe defaults for anything the user didn't specify,
and ask a follow-up **only** when (a) the schedule can't be determined, or (b)
the job needs a risky permission (shell, network, or writes outside the project).
Otherwise, create immediately.

## The change — skill text only

Rewrite `SKILL.md`. **No code changes** — the `create_job` tool already accepts
and normalizes the friendly format and defaults `payload`/`id`.

### 1. New core principle (replaces "Never create unless every gate answered")

> Collect the user's intent: **what** the job does and **how often** it runs.
> Apply safe defaults for anything they didn't specify. Ask a follow-up **only**
> when you cannot determine the *schedule*, or when the job needs a *risky*
> permission. Otherwise, create the job.

### 2. Safe defaults (filled silently)

| Gate | Default |
|---|---|
| Timeout | 300s (5 min). Use longer only if the action clearly implies it (a backup, a report, a large sync). |
| Min-cadence | Derived from the schedule (daily → 86400, hourly → 3600, every N min → N×60). |
| Max consecutive failures | 3 |
| Permissions (grant) | **none** — `paths: []`, `shell: false`, `network: false`, `tools: []`. The safe baseline for a reminder or read-only check. |

### 3. The only two "must ask" triggers

- **Schedule unclear.** The user said "remind me" / "run a check" with no usable
  *when*. → Ask one question: "How often / at what time?"
- **Risky permission.** The action clearly needs shell, network, or writes
  *outside the current project directory*. → Confirm that one thing explicitly,
  e.g. "This needs to run shell commands and write to `/backups` — okay?"
  Reminders and read-only jobs never hit this.

Everything else creates with **no questions**.

### 4. Replace the "Fail Closed" section

Replace the entire "## Fail Closed" section (the "re-ask all four" template) with a
"## Sensible Defaults & Targeted Questions" section that states: create
immediately when the request is safe and the schedule is clear; otherwise ask at
most a focused question (the schedule and/or the risky grant) — **never** the
four-gate template.

### 5. Keep

- The `create_job` tool call instruction (PR #159) and the example arg shape.
- The gate *concepts* (timeout/cadence/failures/permissions) as the fields whose
  values are set — now defaulted rather than interrogated.
- The "grant fields are recorded, not enforced (v1)" note — it is exactly why
  defaulting permissions is safe today, and why the risky-grant question is the
  right future-proof guard.
- "Once `create_job` returns 'Created job', stop — do not re-ask the gates."

## Why this fixes the loop

The loop is the model repeatedly judging "are all gates answered?" → "no" →
re-ask. Removing the rule that *requires* explicit answers means the model
defaults the routine gates and reaches the `create_job` call after at most one
focused question. There is no longer a fail-closed gate for it to loop on.

## Risk / scope

- **Skill text only.** No code, no new tool, no modal. Lowest-risk fix for the
  highest-impact symptom; addresses the root cause (skill rigidity) directly.
- **Defaulting permissions is safe in v1** because grant is recorded-not-enforced
  (a defaulted permission is metadata with no runtime effect yet). The
  "ask before risky grant" trigger is the correct guard for when enforcement
  lands.
- A friendly intake modal was considered and **rejected**. Job creation is
  agent-native: "create a cron job that…" in chat already routes to this skill
  (it's global + model-invocable), and the skill now creates with defaults. No
  new UI is needed; the modal would add surface for no benefit.

## Also in scope: remove the `n` "new job" shortcut

The cron dashboard's `n` key was only a shortcut that seeded the same chat prompt
("I want to create a scheduled cron job.") the agent already handles — it was
never a modal. Per the agent-native direction, **remove it entirely** so creation
is purely conversational:

- `harness/tui/widgets/cron_dashboard.py`: drop the `Binding("n", ...)`,
  `action_new_job`, the `NewJobRequested` message class, and the docstring lines
  referencing them.
- `harness/tui/app.py`: drop the `NewJobRequested` import, `on_new_job_requested`,
  and `_seed_create_job`.
- The dashboard keeps `r` (run now), `t` (toggle), `Backspace` (remove). Creation
  is "ask the agent in chat."
- Tests: remove `test_new_job_seeds_create_prompt_and_closes_drawer` and
  `test_new_job_as_first_action_on_landing_does_not_crash` (both exercise the
  removed path) and the `NewJobRequested` import.

## Testing

- Update `tests/jobs/test_create_job_skill.py` content assertions: drop the
  "fail closed" / "every gate must be answered" required-substring expectations;
  assert the new language is present — e.g. `create_job` (tool, kept), "default",
  and an "ask" trigger phrase for schedule/risky permission. Keep the
  timeout/cadence/failures/permission *concept* words (still documented).
- Behavioral verification (model actually creates without looping on a simple
  request) is **manual** — pressing `n` and giving "remind me every weekday at 9"
  should create the job with no gate interrogation. Unit tests can't assert model
  behavior; the skill-content test is the automated gate.

## Files

- **Modify:** `harness/skills/create-job/SKILL.md` (the guess-first rewrite).
- **Modify:** `tests/jobs/test_create_job_skill.py` (content assertions).
- **Modify:** `harness/tui/widgets/cron_dashboard.py` (remove `n`/`NewJobRequested`/`action_new_job`).
- **Modify:** `harness/tui/app.py` (remove the import, `on_new_job_requested`, `_seed_create_job`).
- **Modify:** `tests/jobs/test_cron_drawer_mount.py` (remove the two `n`-path tests + import).
