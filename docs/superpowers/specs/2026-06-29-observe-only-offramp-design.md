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

#### L1 must cover every routed run path, not just ACP (review finding)

`_instance_template_for` today has **one** call site — `acp_agent.py:716` inside
`_run_agent_turn` (the interactive TUI). Both other *routed* run paths bypass it and
fall through to the raw `mini.yaml` "Please solve this issue" work-order. Verified:

| Path | Routes? | Reaches `_instance_template_for` today? | Action |
|---|---|---|---|
| ACP interactive (`acp_agent.py:716`) | yes | yes | L1 as written |
| Chat (`chat_handler.py:151-157`) | yes | no — sends raw prompt, no template | nothing (correct already) |
| Dev CLI (`run_traced.py:197-203`) | yes — **has `cls.task_type`** at `:80` but drops it | no | **thread `task_type` through** (see below) |
| Cron executor (`jobs/executor.py:185`) | no router | no | **explicit decision** (see below) |
| Subagent worker (`tools/subagent.py:98`) | no — runs an assigned task | no | out of scope (intentional work-order) |

To avoid three divergent copies, **lift the template selection into one shared seam**
rather than re-deriving it at each caller. Concretely: move `_instance_template_for`
(and the `ANSWER_ONLY_INSTANCE` / `OBSERVE_FIRST_INSTANCE` constants) into a small
leaf module (e.g. `harness/instance_templates.py`) importable by `acp_agent.py`,
`run_traced.py`, and `jobs/executor.py` without an import cycle — mirroring how
`textgate.py` / `permcheck.py` were extracted. Then:

- **ACP:** unchanged behavior, now importing from the leaf.
- **Dev CLI (`run_traced.py`):** thread `cls.task_type` into `run_agent` and set
  `agent_cfg["instance_template"] = _instance_template_for(task_type, default)` before
  building the runner. (It already classifies; it just discards the result.)
- **Cron executor:** has no router, so it cannot classify intent. **Decision (default,
  revisitable): leave cron on the work-order template for now** — a cron job is a
  predetermined instruction the user wrote, and many are genuine "do X" jobs. Record
  this as a known limitation; a follow-up can add a per-job `mode: observe|work`
  field (`jobs/model.py`) if observe-style cron jobs become common. *(If the user
  prefers, the alternative is to default cron ops to observe-first too — flagged in
  the open question below.)*
- **Subagent worker:** out of scope — a worker is dispatched a concrete assigned task
  by its parent, so the work-order framing is correct; do not change it.

This makes the L1 fix real for **all interactive + dev-CLI routed turns**, with cron
and workers explicitly scoped rather than silently missed.

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

### Residual completeness limit (review finding, accepted)

L1 keys off `task_type`. If the cheap router **misclassifies** a read-only "check X"
as `code_fix` / `code_refactor` / `code_feature`, it still gets the work-order — L1
can't see intent the classifier got wrong. We accept this rather than chase it,
because **L3 is the cross-cutting backstop**: the `systematic-debugging` off-ramp
fires on "no reported failure" regardless of which task_type attached the skill, and
the tightened description steers the classifier away from debugging on observe-intent.
The remaining exposure is "misclassified as code_fix *and* no debugging skill loaded"
— rare, and the worst case is the agent over-eagerly acts, which is the pre-existing
behavior, not a regression. Noted, not fixed.

## Files touched

- **`harness/instance_templates.py` (new leaf)** — `ANSWER_ONLY_INSTANCE`,
  `OBSERVE_FIRST_INSTANCE`, `_instance_template_for`; moved out of `acp_agent.py`
  so all routed run paths can import without a cycle.
- `harness/acp_agent.py` — import the three symbols from the leaf; `:716` unchanged.
- `harness/run_traced.py` — thread `cls.task_type` into `run_agent`; set
  `agent_cfg["instance_template"] = _instance_template_for(task_type, default)`.
- `harness/router.py` — extend `_system_prompt` with observe-vs-fix guidance.
- `harness/skills/systematic-debugging/SKILL.md` — tighten `description`, add precondition off-ramp.
- `tests/test_acp_agent.py` — **update** `test_work_order_turn_keeps_engine_instance_template`
  (line 65 asserts `ops_task` keeps the default — drop `"ops_task"`); add
  `test_ops_task_turn_gets_observe_first_template` + an `OBSERVE_FIRST_INSTANCE` content
  test mirroring `test_answer_only_template_*`. (Import path moves to the leaf module —
  update the existing imports at `test_acp_agent.py:52,62,72`.)
- `tests/test_run_traced.py` — new: an `ops_task` run sets the observe-first template
  on the runner config (guards the dev-CLI path that the review found unprotected).
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
- **Cron-executor and subagent-worker paths** keep the work-order template (cron =
  predetermined instruction with no router intent; worker = parent-assigned task).
  A per-job `mode` field for cron is a possible follow-up, not this PR.
- Chasing classifier *misclassification* of read-only intent into `code_fix`/etc.
  (L3 off-ramp is the backstop; see "Residual completeness limit").
- #176 (create_job re-announce loop) — separate turn-termination bug.

## Open questions for the user

1. **Cron ops jobs:** default the cron path to the work-order template (current spec
   decision) or to observe-first? Work-order is safer for "do X nightly" jobs; some
   "check X and alert" jobs would prefer observe. Recommendation: keep work-order now,
   add a per-job `mode` later.
2. **`OBSERVE_FIRST_INSTANCE` escalation wording:** "find a real failure → describe it
   and ask before fixing" vs. stricter "always report only, never act." (Earlier
   answer chose observe-first-with-consent; confirm that still holds.)

## Risks

- **Low-risk class of change** — mostly prompt text plus one leaf-module extraction
  (`instance_templates.py`) and one thread-through in `run_traced.py`. Blast radius
  bounded by the named test modules; the extraction is a move, not a rewrite.
- The router change (L2) is advisory (the cheap model may still occasionally
  mis-attach); **L1 (now covering ACP + dev CLI) and L3 are the real safety net** and
  make the agent behave correctly even when L2 mis-fires.
- New import seam: `instance_templates.py` must stay leaf (no `acp_agent`/`router`
  imports) to avoid a cycle — same discipline as `textgate.py`/`permcheck.py`.
