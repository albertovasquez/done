"""Per-task-type instance templates — the USER-turn framing injected every step.

LEAF module: stdlib-only imports. Do NOT import acp_agent / router / run_traced /
jobs.* (cycle guard, same as textgate.py / permcheck.py). The engine's default
instance_template (mini.yaml) reads "Please solve this issue: {{task}} … Edit the
source code to resolve it" — an every-turn work-order. We swap that framing per
task type so a read-only request is not treated as a fix job (issue #177).
"""
from __future__ import annotations

# The engine's own system_template (mini.yaml) is "You are a helpful assistant
# that can interact with a computer" — upstream's SWE-bench solver identity. We
# never let that reach the model: it leads with an act-on-a-computer framing that
# is the opposite of Done's understand-first posture. Done's full identity +
# temperament live in base_prompt.render_base_prompt, which TracingAgent appends
# after this line; so this only needs to be a neutral, Done-owned opener that
# stands on its own if the base block is ever empty (some construction paths pass
# base_block=""), WITHOUT restating base_prompt's identity sentence verbatim (that
# would open the prompt with the same line twice). Replacing — not appending to —
# the upstream system_template is what actually contains the bleed.
DONE_SYSTEM_TEMPLATE = "You are Done (dn), Bitlabs' terminal coding agent.\n"


# code_explain: answer, don't act.
ANSWER_ONLY_INSTANCE = (
    "The user asked: {{task}}\n\n"
    "This is a QUESTION, not a work order. Investigate as needed — read files, "
    "run read-only commands — then ANSWER in words. Do NOT edit, create, or "
    "delete files to answer it. If a good answer would require changing code, "
    "say so and ask whether to proceed; do not start the change yourself. "
    "When you have answered, finish by issuing exactly: "
    "`echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`."
)

# ops_task: observe and report; acting is the explicit, consent-gated exception.
# Read-only is an IMPERATIVE floor (ANSWER_ONLY strength) so work-order momentum
# can't read "ask first" as optional.
OBSERVE_FIRST_INSTANCE = (
    "The user asked: {{task}}\n\n"
    "Treat this as an OBSERVE request: inspect the relevant state and report what "
    "you find. Read files and run read-only commands (status, logs, heartbeat, "
    "PID, job state). Do NOT edit, create, or delete anything to investigate. "
    "Do not assume something is broken — if everything is healthy, say so and stop. "
    "Do NOT manufacture a reproduction: do not run the test suite to find a failing "
    "test that wasn't reported. If a fix turns out to be needed, STOP and ask first "
    "— describe the failure and ask whether to proceed; do not start the change "
    "yourself. When you have answered, finish by issuing exactly: "
    "`echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`."
)


# code_fix / code_feature / code_refactor / anything act-ish: a Done-native work
# order that replaces the engine's cat/sed + bash-only mini.yaml default. Keeps the
# step-wise loop and the terminal submit contract; points at the real file tools.
WORK_ORDER_INSTANCE = (
    "The user asked: {{task}}\n\n"
    "Investigate first — read the relevant files and run read-only commands to "
    "understand the code. Then act per your Posture: if this is a clear directive "
    "(or a trivial, unambiguous change), carry it through; if intent is ambiguous "
    "or the change is large or risky and you are working interactively, propose a "
    "short plan and confirm before mutating.\n"
    "- Use the Read, Write, and Edit tools to inspect and change files. Do not "
    "edit files with cat/sed heredocs.\n"
    "- Use bash for commands: builds, tests, git, and search — not for editing "
    "files.\n"
    "- Work step by step so you can verify as you go: make a change, then run the "
    "build/tests to confirm it works, then check edge cases.\n"
    "When the task is complete and verified, finish by issuing exactly: "
    "`echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` — do not combine it with any "
    "other command."
)


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


def done_agent_cfg(cfg: dict, task_type: str) -> dict:
    """Return a COPY of the engine cfg with BOTH templates made Done-native: the
    vendored mini.yaml system_template (upstream's 'helpful assistant that can
    interact with a computer') is replaced with DONE_SYSTEM_TEMPLATE, and the
    instance_template is chosen per task_type. This is the single chokepoint the
    CLI, ACP, and headless paths call so upstream's SWE-bench framing never reaches
    the model from either the system OR the user turn. Never mutates `cfg`."""
    return {
        **cfg,
        "system_template": DONE_SYSTEM_TEMPLATE,
        "instance_template": _instance_template_for(task_type, cfg.get("instance_template", "")),
    }
