"""Per-task-type instance templates — the USER-turn framing injected every step.

LEAF module: stdlib-only imports. Do NOT import acp_agent / router / run_traced /
jobs.* (cycle guard, same as textgate.py / permcheck.py). The engine's default
instance_template (mini.yaml) reads "Please solve this issue: {{task}} … Edit the
source code to resolve it" — an every-turn work-order. We swap that framing per
task type so a read-only request is not treated as a fix job (issue #177).
"""
from __future__ import annotations

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


def _instance_template_for(task_type: str, default: str) -> str:
    """Pick the engine instance_template for this turn. code_explain → answer-only;
    ops_task → observe-first; every other task_type keeps the engine default."""
    if task_type == "code_explain":
        return ANSWER_ONLY_INSTANCE
    if task_type == "ops_task":
        return OBSERVE_FIRST_INSTANCE
    return default
