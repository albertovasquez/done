# Done prompt-layer maturity — design spec

**Date:** 2026-07-01
**Status:** Approved design, pending implementation plan
**Author:** Alberto (via Claude)

## Problem

Done assembles the model-facing prompt from two layers written for different
agents, and they contradict each other on the main coding path:

1. **`base_prompt.BASE_POLICY`** (Done-native) — tells the agent to *prefer the
   dedicated Read/Write/Edit tools over cat/sed*, and is otherwise tight and good.
2. **`mini.yaml` `instance_template`** (vendored from mini-SWE-agent) — the
   user-turn message re-rendered every step. Its default teaches the agent to
   *create files with `cat <<EOF`* and *edit with `sed -i`* (with a macOS
   `sed -i ''` footgun), frames the agent as **bash-only** ("every response MUST
   include AT LEAST ONE bash tool call"), and is SWE-bench-shaped.

`instance_templates._instance_template_for()` already swaps this default for two
task types — `code_explain` → `ANSWER_ONLY_INSTANCE`, `ops_task` →
`OBSERVE_FIRST_INSTANCE`. But the router emits seven task types
(`chat_question, code_explain, code_fix, code_feature, code_refactor, ops_task,
ambiguous`). **`code_fix`, `code_feature`, and `code_refactor` fall through to
the raw mini.yaml default** — i.e. the cat/sed contradiction reaches the model on
the *majority of real coding work*. (`ambiguous` is a near-miss: router
`needs = confidence < threshold or task_type == "ambiguous"` (`router.py:221`)
usually diverts it to the clarify gate rather than an agent turn, so it only
reaches a work-order template in edge cases — see Component 4.)

**Single chokepoint (verified):** both the ACP path (`acp_agent.py:710`) and the
traced path (`run_traced.py:49`) select the instance_template through the *same*
`instance_templates._instance_template_for(task_type, default)`. One function to
change fixes every path.

Separately, Done's base policy is silent on several things the agent needs and
that the Claude Code prompt handles well: parallel tool calls, what
`<system-reminder>`/hook output means, how to react to a denied tool call, and
what app surface it's running in.

## Grounded constraints (verified against live code)

These shaped the design and must not be violated:

- **`echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` is load-bearing.** The submit
  sentinel is detected in `acp_env.py:73` (`_check_finished` raises `Submitted`),
  which propagates through `execute_actions` (`tracing_agent.py:~271`) and ends
  the run loop; the submission text lands on an `exit`-role message that
  `transcript.py:flatten_agent_messages` reads back as the turn's answer.
  `models_mock.py:31` also emits it. **Every instance_template must keep
  instructing the agent to finish with exactly that command.** This is not
  SWE-bench cruft to remove.
  (NB — the `terminal_submission` assignment at `tracing_agent.py:~283` is a
  *separate* submission source keyed on `create_job`, NOT the echo path; do not
  conflate the two.)
- **`system_template` composition** (`tracing_agent.py:96–110`): mini.yaml's
  one-line `system_template` is rendered, then Done appends `base_block` +
  persona + memory + skills. So `BASE_POLICY` edits land in the system prompt for
  both the agent path and (via `chat_handler.py:200`) the chat path.
- **`instance_template` is re-rendered every step** (`tracing_agent.py:179`) and
  carries `{{task}}`. It is the per-turn user framing, not a one-shot.
- **`test_base_prompt.py:55` pins `"parallel" not in BASE_POLICY`** ("deferred
  follow-up"). Adding parallel guidance is a tracked, deliberate change that must
  flip this test — not an accident.
- **mini.yaml is vendored upstream** and resolved via `find_spec`/`mini_yaml_path()`
  (PR #223). We do **not** edit it — we stop letting its default reach the model.

## Scope (approved)

- **Replace the fall-through with a Done-native default** — no task_type reaches
  the raw mini.yaml instance_template. mini.yaml stays byte-for-byte as the
  compaction/overhead fallback; it just no longer frames a real turn.
- **Fold four Claude-Code borrows into `BASE_POLICY`**: parallel tool calls,
  harness-voice semantics (system-reminder + hook + denial), tool-agnostic turn
  framing, environment self-description.
- **Fix adjacent obvious issues** surfaced during review (below).
- **Explicitly out of scope:** editing vendored `mini.yaml`; changing the router
  taxonomy; touching the ACP/turn-termination machinery; persona/memory/skills
  content.

## Design

### Component 1 — `WORK_ORDER_INSTANCE` (new, in `instance_templates.py`)

A Done-native default instance_template for act-intent coding turns. Mirrors the
structure of the existing two constants (same module, stdlib-only leaf, same
`{{task}}` + terminal-echo contract) so it composes identically.

Content requirements:
- Frame `{{task}}` as a work order: investigate, then make the change.
- **Point at the real tools:** "Use the Read, Write, and Edit tools to inspect and
  change files — not cat/sed heredocs. Use bash for commands (builds, tests, git,
  search), not for editing files."  — directly resolves the contradiction.
- Keep the step-wise loop value from mini.yaml (analyze → change → verify → test
  edge cases) but tool-agnostic.
- **Keep** the terminal contract verbatim: finish by issuing exactly
  `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`, not combined with any other
  command.
- No cat/sed tutorial, no "MUST include a bash call," no macOS sed footgun.

### Component 2 — `_instance_template_for()` routing change

Change the fall-through so act-intent coding types get `WORK_ORDER_INSTANCE`
instead of the raw engine default:

```
code_explain            -> ANSWER_ONLY_INSTANCE      (unchanged)
ops_task                -> OBSERVE_FIRST_INSTANCE     (unchanged)
code_fix / code_feature / code_refactor / ambiguous / <anything act-ish>
                        -> WORK_ORDER_INSTANCE        (new)
default (unknown)       -> WORK_ORDER_INSTANCE        (new; was `default`)
```

Decision: the `default` param currently threads mini.yaml's text in. After this
change nothing should render that text to the model, so `WORK_ORDER_INSTANCE`
becomes the floor. Keep the `default` param in the signature (callers pass it;
compaction still needs *a* template) but return `WORK_ORDER_INSTANCE` for the
unmatched case rather than `default`. Verify no caller relies on getting the raw
mini.yaml string back.

### Component 3 — `BASE_POLICY` enrichment (`base_prompt.py`)

Add four short bullets/lines to the existing `# Working principles` block (keep
Done's terse voice — no CC-style bloat):

1. **Parallel tool calls** — "Independent tool calls can go in one response and
   run in parallel; batch them instead of making round-trips." (flips
   `test_base_prompt.py:55`.)
2. **Harness voice** — "`<system-reminder>` tags and hook output are injected by
   the harness, not the user; treat them as system context. A denied tool call
   means the user declined it — adjust, don't retry the same call."
3. **Tool-agnostic turn framing** — reinforce that a turn may use any tool
   (Read/Write/Edit/bash/load_skill), not bash specifically. (Counters the
   residual bash-only framing the agent may still infer.)
4. **Environment self-description** — one line in the `# Environment` block:
   Done runs as a terminal TUI; the agent process and UI are separate processes
   over ACP. Keep it to a sentence.

### Component 4 — adjacent fixes found in review

- **macOS sed footgun**: no longer reaches the model once Components 1–2 land
  (the sed tutorial lived only in the mini.yaml default). No separate action;
  note it as resolved-by-consequence.
- **`ambiguous` framing**: today `ambiguous` also falls through to a work-order.
  Confirm that's acceptable, or whether ambiguous should inherit a
  clarify-leaning template. **Open item for the plan** — default to
  `WORK_ORDER_INSTANCE` unless the clarify gate already covers it (it may, per the
  `#88` clarify-gate work — verify before adding anything).

## Testing

- `test_base_prompt.py`: flip the `test_policy_does_not_promise_parallel_tool_calls`
  tripwire to assert parallel guidance is now present; add assertions for the
  harness-voice + denial lines. Keep all existing identity/security/plan
  assertions green.
- New `test_instance_templates.py` cases: `_instance_template_for("code_feature")`
  and `("code_fix")` and `("code_refactor")` return `WORK_ORDER_INSTANCE`; the
  unmatched/unknown case returns `WORK_ORDER_INSTANCE` (not the raw `default`);
  `code_explain`/`ops_task` still return their existing constants; every returned
  template still contains the `COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` contract and
  contains no `sed -i` / `cat <<`.
- Full suite: `.venv/bin/python -m pytest tests/ -q` green before PR.

## Risks

- **Prompt behavior change needs empirical validation, not just review.** These
  edits change how the agent acts on every coding turn. The plan should include a
  manual before/after run on a real task (e.g. via `/run`) — not merged on unit
  tests alone.
- **Terminal-echo contract is fragile.** If any new template drops or mangles the
  exact `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`, turns never terminate. Tests
  above guard this, but it's the highest-blast-radius mistake.
- **Compaction overhead estimate** reads the rendered system+instance
  (`tracing_agent.py:141`). A longer WORK_ORDER_INSTANCE slightly raises
  `fixed_overhead_tokens`; negligible but worth a sanity check.

## Non-goals

Editing vendored mini.yaml; router taxonomy changes; ACP/turn-termination
changes; CC-style verbosity (schedule-offer heuristics, Chrome sections, etc.).
Done's edge is being a small, readable agent — keep the additions terse.
