# Done Temperament: Contain Upstream's "Act Now" Identity

**Date:** 2026-07-01
**Status:** Implemented
**Related:** #177 (per-task instance templates), zero-upstream-edits constraint (pyproject.toml), #254 (base_prompt identity)

## Problem

Done is built on a **vendored, unmodified** mini-swe-agent (`upstream/`, pinned
`==2.4.2`). That engine is a SWE-bench *solver* — its entire job is "take an
issue, submit a code patch." Its DNA bleeds into Done's behavior and makes the
agent eager to act before it has understood the problem or been asked to change
anything.

Two concrete leak channels were found by tracing what reaches the model:

1. **`instance_template`** (the per-turn user framing): upstream's
   `"Please solve this issue: {{task}} … Edit the source code to resolve it."`
   This was **already contained** by #177 via `_instance_template_for`
   (answer-only for `code_explain`, observe-first for `ops_task`, work-order
   otherwise).

2. **`system_template`** (the standing identity): upstream's
   `"You are a helpful assistant that can interact with a computer."` This was
   **NOT** contained — the harness *appended* Done's identity (`base_block`)
   after it (`tracing_agent.py:106-109`), so upstream's act-on-a-computer line
   was still the first thing the model read, every turn, on every path
   (interactive, dev-CLI, and headless cron).

The user's framing: *"we're getting things from upstream that are bleeding
through that are core to what Done is. What is the approach to ensure that
those wanting to act prematurely aren't part of what Done is?"* — i.e. a
**constitutional temperament**, enforced by construction, not another one-off
per-task patch. And the containment must never require editing `upstream/`.

## Temperament spec (the "constitution")

Elicited via brainstorming:

- **Understand before acting.** Reading/searching/inspecting/read-only commands
  are always free — never gated. Show understanding before proposing change.
- **Propose before mutating.** Editing/creating/deleting files (and state-mutating
  commands) is a *licensed* act, not a reflex.
- **Context-scoped gate — one rule, two branches:**
  - *Interactive + no standing directive:* for anything beyond a trivial,
    clearly-requested change, state a short plan and **wait** for a go-ahead,
    then execute fully (gate per *task*, not per edit). A vague/exploratory
    message ("how should we…", "X feels off") is an invitation to think
    together, not a work order.
  - *Standing directive given* (explicit "do it", `/goal`, a scheduled job,
    `/ship`): the directive **is** the confirmation. State the plan (so it's
    visible) and carry it through autonomously, without re-asking per step.
    Headless (cron/loop) has **no elicitation channel** (`executor.py:182`), so
    the directive is necessarily assumed — the plan-wait branch is impossible
    there by construction, which is exactly why it must not apply.
- **Trivial escape hatch.** An unambiguous, small, reversible, clearly-requested
  change (rename, typo, "add a test for X") skips the ceremony. Restraint is for
  ambiguity and stakes, not for every action.
- **Stay in scope.** Act on what was asked; don't refactor adjacent code.

## Approach (chosen: A — own the identity at the composition seam)

Rejected alternative (B): put the posture only in `base_prompt.py` and leave
`system_template` inherited. Rejected because it treats the symptom — upstream's
identity line still *leads*, with Done's posture bolted on after as a rebuttal.
It does not contain the bleed the user identified.

Chosen: make Done's identity **supersede** upstream's at the one composition
seam, zero upstream edits.

1. **`DONE_SYSTEM_TEMPLATE`** (in `instance_templates.py`): a neutral, Done-owned
   system opener that *replaces* (not appends to) upstream's `system_template`.
   `render_base_prompt` (with the new Posture section) is appended after it by
   `TracingAgent`, exactly as before.

2. **Posture section in `base_prompt.py`**: the temperament above, written to be
   **self-scoping** — the model reads whether a standing directive is present
   from the conversation itself (a `/goal`, a cron order, "do it"), so no
   `interactive` boolean has to be plumbed through five call sites. Placed
   immediately after the identity line — the most prominent position — because
   temperament is who Done *is*, not a working principle.

3. **`done_agent_cfg(cfg, task_type)`** (in `instance_templates.py`): the single
   chokepoint that returns a cfg copy with **both** `system_template`
   (Done-native) and `instance_template` (per task_type) overridden. The three
   seams call it:
   - `run_traced.py` (`_instance_template_cfg`) — dev CLI
   - `acp_agent.py` — interactive/ACP
   - `jobs/executor.py` (`_observe_or_default_cfg`) — headless cron/loop
     (keeps its `mode`-based instance selection, adds the system_template strip)

4. **`WORK_ORDER_INSTANCE`** simplified: it no longer commands "make the change"
   as an imperative; it defers to the Posture (act on clear directives / trivial
   changes; propose-and-confirm when ambiguous or high-stakes and interactive).

## Invariant enforcement

`test_composed_system_message_is_done_native_not_upstream`
(`tests/test_tracing_agent.py`): builds a `TracingAgent` with the Done-native cfg
+ base block and asserts the composed system message the engine *actually sends*
contains **no** `"helpful assistant that can interact with a computer"`, leads
with `"You are Done"`, and carries the Posture. This fails if any future upstream
bump (or a reverted seam) lets upstream's identity back in — the anti-rot guard.

Unit coverage: `done_agent_cfg` overrides both templates without mutating the
caller (`test_instance_templates.py`); Posture present + inspection-not-gated
(`test_base_prompt.py`); headless default-mode still strips the system_template
(`tests/jobs/test_executor.py`).

## Files touched

- `harness/base_prompt.py` — new `# Posture` section in `BASE_POLICY`
- `harness/instance_templates.py` — `DONE_SYSTEM_TEMPLATE`, `done_agent_cfg`,
  simplified `WORK_ORDER_INSTANCE`
- `harness/run_traced.py` — `_instance_template_cfg` → `done_agent_cfg`
- `harness/acp_agent.py` — use `done_agent_cfg`; trim now-unused imports
- `harness/jobs/executor.py` — `_observe_or_default_cfg` also strips system_template
- tests: `test_base_prompt.py`, `test_instance_templates.py`,
  `test_tracing_agent.py`, `tests/jobs/test_executor.py`

## Non-goals / YAGNI

- No `interactive` flag plumbed through call sites — the posture is self-scoping.
- No change to the engine loop's "every turn needs a tool call" requirement
  (that's upstream mechanism; restraint is expressed *within* it via read-only
  commands + propose, exactly as answer-only/observe-first already prove).
- No new router `task_type` — temperament lives in identity, above the router,
  so a misclassification can't bypass it.
