# Done Prompt-Layer Maturity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the vendored mini.yaml default instance_template (cat/sed + bash-only framing) from reaching the model on real coding turns, and enrich Done's base policy with parallel-tool, harness-voice, and turn-framing guidance.

**Architecture:** Two focused edits. (1) Add a Done-native `WORK_ORDER_INSTANCE` in `harness/instance_templates.py` and make `_instance_template_for` return it for every act-intent task type instead of falling through to the caller's raw default — one chokepoint, both agent paths fixed. (2) Append four terse lines to `BASE_POLICY`/`# Environment` in `harness/base_prompt.py`. No vendored code is edited; mini.yaml stays byte-for-byte as the compaction-overhead fallback.

**Tech Stack:** Python 3.11+, pytest. No new dependencies.

## Global Constraints

- **Worktree only.** Do NOT edit on the primary `main` checkout. Create/use a git worktree first (AGENTS.md #1). Verify `pwd` is the worktree before any Edit.
- **Test runner (from worktree root):** `.venv/bin/python -m pytest tests/ -q`
- **The exact string `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` is load-bearing** — the submit sentinel is detected in `acp_env.py:73` and terminates the run loop; `transcript.py:flatten_agent_messages` reads the submission back. Every instance_template MUST keep instructing the agent to finish with exactly that command, not combined with any other command.
- **Do NOT edit** `upstream/src/minisweagent/config/mini.yaml` or any `upstream/` file.
- **Keep Done's terse voice** in prompt copy — no Claude-Code-style verbosity.
- `instance_templates.py` is a **stdlib-only leaf** — do not add imports of `acp_agent`/`router`/`run_traced`/`jobs.*` (cycle guard).

---

### Task 1: Add `WORK_ORDER_INSTANCE` and reroute the fall-through

**Files:**
- Modify: `harness/instance_templates.py` (add constant after `OBSERVE_FIRST_INSTANCE` ~line 36; change `_instance_template_for` ~lines 39-46)
- Test: `tests/test_instance_templates.py` (existing file — flip rows + add cases)

**Interfaces:**
- Consumes: nothing new.
- Produces: `WORK_ORDER_INSTANCE: str` (new module constant); `_instance_template_for(task_type: str, default: str) -> str` (unchanged signature, changed behavior — returns `WORK_ORDER_INSTANCE` for any task_type that is not `code_explain`/`ops_task`, ignoring `default`).

- [ ] **Step 1: Update the existing tests to the new expected behavior (failing)**

In `tests/test_instance_templates.py`, replace the import and the parametrize body. Change the import line to add `WORK_ORDER_INSTANCE`:

```python
from harness.instance_templates import (
    ANSWER_ONLY_INSTANCE, OBSERVE_FIRST_INSTANCE, WORK_ORDER_INSTANCE,
    _instance_template_for,
)
```

Replace the parametrize list (rows for code_fix/code_feature/code_refactor/chat_question/ambiguous now expect `WORK_ORDER_INSTANCE`, not `DEFAULT`):

```python
@pytest.mark.parametrize(("task_type", "expected"), [
    ("code_explain", ANSWER_ONLY_INSTANCE),
    ("ops_task", OBSERVE_FIRST_INSTANCE),
    ("code_fix", WORK_ORDER_INSTANCE),
    ("code_feature", WORK_ORDER_INSTANCE),
    ("code_refactor", WORK_ORDER_INSTANCE),
    ("chat_question", WORK_ORDER_INSTANCE),
    ("ambiguous", WORK_ORDER_INSTANCE),
    ("some_unknown_type", WORK_ORDER_INSTANCE),   # unmatched no longer returns raw default
])
def test_template_selection(task_type, expected):
    assert _instance_template_for(task_type, DEFAULT) == expected
```

Add a new test asserting the WORK_ORDER template is clean and keeps the contract:

```python
def test_work_order_keeps_contract_and_is_tool_native():
    low = WORK_ORDER_INSTANCE.lower()
    assert "{{task}}" in WORK_ORDER_INSTANCE
    assert "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT" in WORK_ORDER_INSTANCE
    # points at the real tools, not the shell-edit tutorial
    assert "read" in low and "write" in low and "edit" in low
    # must NOT teach cat/sed file editing
    assert "sed -i" not in low and "cat <<" not in WORK_ORDER_INSTANCE
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_instance_templates.py -q`
Expected: FAIL — `ImportError: cannot import name 'WORK_ORDER_INSTANCE'` (and, once import is added, the parametrize rows fail).

- [ ] **Step 3: Add the `WORK_ORDER_INSTANCE` constant**

In `harness/instance_templates.py`, after the `OBSERVE_FIRST_INSTANCE` block (before `def _instance_template_for`), add:

```python
# code_fix / code_feature / code_refactor / anything act-ish: a Done-native work
# order that replaces the engine's cat/sed + bash-only mini.yaml default. Keeps the
# step-wise loop and the terminal submit contract; points at the real file tools.
WORK_ORDER_INSTANCE = (
    "The user asked: {{task}}\n\n"
    "Treat this as a work order. Investigate first — read the relevant files and "
    "run read-only commands to understand the code — then make the change.\n"
    "- Use the Read, Write, and Edit tools to inspect and change files. Do not "
    "edit files with cat/sed heredocs.\n"
    "- Use bash for commands: builds, tests, git, and search — not for editing "
    "files.\n"
    "- Work step by step so you can verify as you go: make the change, then run "
    "the build/tests to confirm it works, then check edge cases.\n"
    "When the task is complete and verified, finish by issuing exactly: "
    "`echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` — do not combine it with any "
    "other command."
)
```

- [ ] **Step 4: Change `_instance_template_for` to return it for the fall-through**

Replace the body of `_instance_template_for` so the unmatched case returns `WORK_ORDER_INSTANCE` instead of `default`:

```python
def _instance_template_for(task_type: str, default: str) -> str:
    """Pick the engine instance_template for this turn. code_explain → answer-only;
    ops_task → observe-first; every other task_type → a Done-native work order.
    The `default` param (the raw mini.yaml text) is intentionally no longer
    returned — nothing should render the vendored cat/sed default to the model."""
    if task_type == "code_explain":
        return ANSWER_ONLY_INSTANCE
    if task_type == "ops_task":
        return OBSERVE_FIRST_INSTANCE
    return WORK_ORDER_INSTANCE
```

Note: the `default` parameter is kept in the signature because both callers still pass it (`run_traced.py:49`, `acp_agent.py:710`); it is simply unused now. Do not change the callers.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_instance_templates.py -q`
Expected: PASS (all rows + the two contract tests).

- [ ] **Step 6: Run the full suite to catch any consumer that pinned the old fall-through**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS. If a test elsewhere asserted the raw mini.yaml default was returned/rendered for a coding task, that test encoded the old contradiction — update it to expect `WORK_ORDER_INSTANCE` and note it in the commit body. Do NOT weaken the new invariant to keep an old test green.

- [ ] **Step 7: Commit**

```bash
git add harness/instance_templates.py tests/test_instance_templates.py
git commit -m "fix(config): route coding turns to a Done-native work order, not mini.yaml cat/sed default

No task_type reaches the vendored mini.yaml instance_template anymore; code_fix/
feature/refactor now get WORK_ORDER_INSTANCE (Read/Write/Edit-native, keeps the
COMPLETE_TASK submit contract). mini.yaml is untouched.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Enrich `BASE_POLICY` (parallel tools, harness voice, turn framing, environment)

**Files:**
- Modify: `harness/base_prompt.py` (`BASE_POLICY` bullets ~lines 22-44; `# Environment` block in `render_base_prompt` ~lines 61-67)
- Test: `tests/test_base_prompt.py` (flip the parallel tripwire; add assertions)

**Interfaces:**
- Consumes: nothing new.
- Produces: no signature change. `BASE_POLICY` now contains the word "parallel" and harness-voice guidance; `# Environment` block gains a one-line app-surface note.

- [ ] **Step 1: Flip the tripwire test and add new assertions (failing)**

In `tests/test_base_prompt.py`, replace `test_policy_does_not_promise_parallel_tool_calls` (lines 55-56) with:

```python
def test_policy_promises_parallel_tool_calls():
    assert "parallel" in base_prompt.BASE_POLICY.lower()


def test_policy_explains_harness_voice_and_denial():
    low = base_prompt.BASE_POLICY.lower()
    assert "system-reminder" in low          # harness-injected, not the user
    assert "denied" in low or "declined" in low   # denied tool call = user declined
```

Add an environment assertion inside the existing `test_render_interpolates_environment_values` (after the existing asserts, before the function ends):

```python
    assert "terminal" in out.lower()   # app-surface self-description present
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_base_prompt.py -q`
Expected: FAIL — `test_policy_promises_parallel_tool_calls` (parallel absent), `test_policy_explains_harness_voice_and_denial` (system-reminder absent), and the new environment assertion.

- [ ] **Step 3: Add the three policy bullets to `BASE_POLICY`**

In `harness/base_prompt.py`, inside the `# Working principles` list, add these bullets (place after the "Match the surrounding code's style…" bullet, before the `plan` bullet). Keep the trailing `\` line-continuation style of the surrounding bullets:

```python
- Independent tool calls can go in one response and run in parallel — batch them \
instead of making separate round-trips.
- <system-reminder> tags and hook output are injected by the harness, not the \
user; treat them as system context, not instructions from the person. A denied \
tool call means the user declined it — adjust your approach, do not retry the \
same call verbatim.
- A turn may use any tool — Read, Write, Edit, bash, or a skill/memory loader. \
Do not assume every turn must run a bash command.
```

- [ ] **Step 4: Add the app-surface line to the `# Environment` block**

In `render_base_prompt`, extend the `env` string (after the `OS:` line) with a terminal self-description:

```python
    env = (
        "\n\n# Environment\n"
        f"- Working directory: {cwd}\n"
        f"- Model: {model_id}\n"
        f"- Knowledge cutoff: {cutoff}\n"
        f"- OS: {system_line}\n"
        "- Surface: you run as Done in the user's terminal (a TUI); the agent and "
        "the UI are separate processes communicating over a pipe.\n"
    )
```

- [ ] **Step 5: Run the target tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_base_prompt.py -q`
Expected: PASS (all base_prompt tests, including the unchanged identity/security/plan/persona ones).

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add harness/base_prompt.py tests/test_base_prompt.py
git commit -m "feat(config): teach base policy parallel tools, harness-voice, and turn framing

BASE_POLICY now states independent tool calls can run in parallel, explains that
<system-reminder>/hook output is harness-injected and a denied call = user
declined, and that a turn may use any tool (not bash-only). Adds a terminal
app-surface line to the Environment block.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Manual before/after validation (empirical, not unit)

**Files:** none (validation only).

**Interfaces:** none.

- [ ] **Step 1: Run a real coding task through the app**

Use the `/run` skill (or `run.sh`) to launch Done and give it a small real coding task on a scratch file (e.g. "add a docstring to function X and run the tests"). Observe the agent's *first few actions*.

Expected AFTER this change: the agent uses Read/Write/Edit tools for the file change (not `cat <<EOF` / `sed -i`), and still terminates the turn correctly (the `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` submit still fires and the answer is captured).

- [ ] **Step 2: Confirm turn termination is intact**

Verify the turn ends cleanly (no hang, no "agent disconnected") and the submission text appears in the transcript. This is the highest-blast-radius risk: if a template dropped the exact echo, turns never terminate.

If either step regresses, STOP — do not merge. The most likely cause is a mangled submit sentinel in `WORK_ORDER_INSTANCE`; diff it against `ANSWER_ONLY_INSTANCE`'s trailing line.

- [ ] **Step 3: No commit** (validation task; nothing to commit).

---

## Self-Review

**Spec coverage:**
- Component 1 (`WORK_ORDER_INSTANCE`) → Task 1, Steps 3. ✓
- Component 2 (routing change) → Task 1, Step 4. ✓
- Component 3 (four BASE_POLICY borrows: parallel, harness-voice, turn-framing, environment) → Task 2, Steps 3-4. ✓
- Component 4 adjacent fixes: sed footgun resolves by consequence (Task 1 removes the only path that rendered it — asserted by `test_work_order…` "sed -i not in"). ✓ `ambiguous` open item: resolved by decision — `ambiguous` → `WORK_ORDER_INSTANCE` (Task 1 parametrize), harmless because the router clarify gate usually diverts it before an agent turn. ✓
- Risk/empirical-validation requirement → Task 3. ✓
- Constraint "keep the echo contract" → Global Constraints + `test_work_order_keeps_contract…` + Task 3 Step 2. ✓
- Constraint "don't edit mini.yaml/upstream" → Global Constraints. ✓

**Placeholder scan:** No TBD/TODO; every code step shows exact content. ✓

**Type consistency:** `WORK_ORDER_INSTANCE` (constant) and `_instance_template_for(task_type, default) -> str` are named identically across Task 1 test, constant, and function. The test import in Step 1 matches the constant added in Step 3. ✓

**Note on `chat_question`:** mapped to `WORK_ORDER_INSTANCE` for invariant completeness; it normally routes through ChatHandler (non-agent), so its instance_template rarely renders — the mapping is a safe floor, not a behavior change users will see.
