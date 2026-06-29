# Observe-only off-ramp: stop forcing a fix-workflow on read-only requests

**Issue:** #177 — agent forced into fix-workflow on read-only "check X" requests
**Branch:** `fix/observe-offramp`
**Date:** 2026-06-29

## Problem

Asked to *"check if the cron was firing"* (a read-only status query), the agent (Bob)
classified it as `ops_task`, loaded `systematic-debugging`, demanded a bug report,
then **manufactured a reproduction** — it ran the full pytest suite hunting for a
failing test, chased an unrelated flaky test, and never looked at the cron. (Cron
was firing fine.) The agent refuses read-only inspection and invents unrelated work.

Root cause, in the user's words: **we hard-code that there must be a problem to fix.**

## Three layers of the same root cause (all verified against live code)

| # | Layer | Location | Defect |
|---|---|---|---|
| L1 | Instance template | `upstream/.../config/mini.yaml:4-19` applied via `harness/acp_agent.py:65-69` | Every task type *except* `code_explain` gets the SWE-bench work-order: *"Please solve this issue … Create a script to reproduce the issue … Edit the source code to resolve it."* So `ops_task` ("check cron") is framed as a fix job. |
| L2 | Router skill attach | `harness/router.py:59-82` | The cheap triage model picks skills from each skill's `description`. It has no notion of observe-vs-fix intent within `ops_task`, so it attaches `systematic-debugging` to "check X". |
| L3 | Skill has no off-ramp | `harness/skills/systematic-debugging/SKILL.md` | 100% fix-oriented. Once loaded: "you cannot propose fixes" without Phase 1; "ANY technical issue"; Phase 1 step 1 "Read Error Messages" (assumes an error); **Phase 4 step 1 "Create Failing Test Case … MUST have before fixing"** — this is literally why it ran pytest. No precondition for "there is no bug." |

#177 named L2 + L3. L1 (found during investigation) is the most direct cause of the
"always frame as a bug" behavior: even with no skill attached, `ops_task` is told
"solve this issue."

## Design — defense-in-depth, prompt-text only

Each layer is fixed independently so any one defends even if another regresses.
No new code paths, no keyword-heuristic gate — all four edits are prompt text.

### L1 — `ops_task` gets an observe-first instance template (the core fix)

Today `_instance_template_for(task_type, default)` (`acp_agent.py:65-69`) returns
`ANSWER_ONLY_INSTANCE` only for `code_explain`; everything else gets the default
work-order.

**Change:** add a new `OBSERVE_FIRST_INSTANCE` constant and return it for `ops_task`.
`code_fix` / `code_feature` / `code_refactor` keep the default action template
(they are genuine work orders). `code_explain` keeps `ANSWER_ONLY_INSTANCE`.

`OBSERVE_FIRST_INSTANCE` semantics (observe-first with consent-gated escalation —
chosen by the user over pure observe-only):
- Treat the request as: **inspect the relevant state and report what you find.**
- Read files, run read-only commands (status, logs, heartbeat, PID, job state).
- **Do not assume something is broken.** If everything is healthy, say so and stop.
- If you discover a *real* failure, describe it and **ask whether to fix it** before
  changing anything — do not start a fix yourself, and do **not** manufacture a
  reproduction (e.g. do not run the test suite to find a failing test that wasn't
  reported).
- Finish with the standard `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` sentinel,
  matching `ANSWER_ONLY_INSTANCE`'s contract.

`_instance_template_for` becomes a small explicit mapping rather than a single
ternary, so the three special cases (`code_explain` → answer-only, `ops_task` →
observe-first, everything else → default) are readable at a glance.

### L2 — Router learns observe-vs-fix intent

In `router.py` `_system_prompt`, add guidance: within `ops_task`, distinguish
**observe-intent** ("check", "is X working", "show status", "did Y fire", "is the
cron firing") from **fix-intent** (a reported failure, error, or "X is broken").
Do **not** attach debugging skills (e.g. `systematic-debugging`) to an observe-only
request — only attach them when the user reports a failing behavior. (We do not add
a new `ops_check` task type; `ops_task` + the L1 observe-first template + this skill
guidance is sufficient and keeps `TASK_TYPES` stable.)

### L3 — `systematic-debugging` off-ramp + tightened description

1. **Frontmatter `description`** (the text the router classifies on): change from
   *"Use when encountering any bug, test failure, or unexpected behavior, before
   proposing fixes"* to scope it to a **reported failing behavior** (e.g. *"Use when
   there is a reported bug, failing test, or error to fix — not for read-only status
   checks"*). This reduces L2 over-attach at the source.

2. **Precondition block at the top of the body**, before "The Iron Law": this skill
   applies **only when there is a reported failing behavior** (an error, a failing
   test, broken output the user pointed at). If the request is to observe / check /
   report status with no reported failure, **do not enter this workflow** — inspect
   and answer directly, and never manufacture a reproduction (do not run the test
   suite to find a failing test that wasn't reported). The four phases assume a
   confirmed failure exists.

## Files touched

- `harness/acp_agent.py` — add `OBSERVE_FIRST_INSTANCE`, update `_instance_template_for`
- `harness/router.py` — extend `_system_prompt` with observe-vs-fix guidance
- `harness/skills/systematic-debugging/SKILL.md` — tighten `description`, add precondition off-ramp
- `tests/test_acp_agent.py` — **update** `test_work_order_turn_keeps_engine_instance_template`
  (line 65 currently asserts `ops_task` keeps the default — drop `"ops_task"` from that
  loop) and add a new `test_ops_task_turn_gets_observe_first_template` + a content test
  for `OBSERVE_FIRST_INSTANCE` mirroring `test_answer_only_template_*`.
- `tests/test_router.py` — assert an observe-intent prompt does not attach `systematic-debugging`
  (extend with a stubbed-classification case).

## Testing / success criteria

1. **Unit (L1):** `_instance_template_for("ops_task", default)` returns
   `OBSERVE_FIRST_INSTANCE`; `"code_explain"` still returns `ANSWER_ONLY_INSTANCE`;
   `"code_fix"`/`"code_feature"`/`"code_refactor"` still return `default`.
2. **Content (L1):** `OBSERVE_FIRST_INSTANCE` contains the observe/ask-before-fix
   contract and the completion sentinel; does **not** contain "solve this issue".
3. **Content (L3):** `systematic-debugging/SKILL.md` body contains the observe-only
   precondition; frontmatter `description` no longer says "any … unexpected behavior".
4. **Regression:** existing `test_router.py`, `test_run_traced.py`,
   `test_system_skills.py`, `test_flows.py` stay green (baseline: 39 passed).
5. **Behavioral acceptance (manual, from #177 repro):** in `dn`, "check if the cron
   was firing" inspects daemon/heartbeat/job state and answers, without asking for a
   bug report or running pytest.

## Out of scope

- Code-level keyword gate that strips `systematic-debugging` from `cls.skills`
  (rejected: brittle heuristics duplicating prose guidance).
- New `ops_check` task type (not needed; would touch `TASK_TYPES`, flows, tests).
- The upstream `mini.yaml` default template is left as-is (it is correct for the
  SWE-bench `code_fix` lane); we override per task type in the harness, not upstream.
- #176 (create_job re-announce loop) — separate turn-termination bug.

## Risks

- **Lowest-risk class of change** (prompt text). Blast radius bounded by the four
  named test modules.
- The router change is advisory (the cheap model may still occasionally mis-attach);
  L1 + L3 are the real safety net and make the agent behave correctly even then.
