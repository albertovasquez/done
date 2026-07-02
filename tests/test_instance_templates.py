import pytest
from harness.instance_templates import (
    ANSWER_ONLY_INSTANCE, OBSERVE_FIRST_INSTANCE, WORK_ORDER_INSTANCE,
    DONE_SYSTEM_TEMPLATE, ACTION_SKILLS, _instance_template_for, done_agent_cfg,
)

DEFAULT = "Please solve this issue: {{task}}\nEdit the source code to resolve it."


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


def test_observe_first_keeps_task_placeholder_and_sentinel():
    assert "{{task}}" in OBSERVE_FIRST_INSTANCE
    assert "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT" in OBSERVE_FIRST_INSTANCE


def test_observe_first_is_read_only_imperative_not_work_order():
    low = OBSERVE_FIRST_INSTANCE.lower()
    # imperative read-only floor (ANSWER_ONLY strength), not a soft "ask"
    assert "do not" in low or "don't" in low
    assert "edit" in low and "create" in low and "delete" in low
    # the exact #177 anti-pattern must be forbidden in words
    assert "test suite" in low
    # must NOT carry the work-order framing
    assert "solve this issue" not in low


def test_work_order_keeps_contract_and_is_tool_native():
    low = WORK_ORDER_INSTANCE.lower()
    assert "{{task}}" in WORK_ORDER_INSTANCE
    assert "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT" in WORK_ORDER_INSTANCE
    # points at the real tools, not the shell-edit tutorial
    assert "read" in low and "write" in low and "edit" in low
    # must NOT teach cat/sed file editing
    assert "sed -i" not in low and "cat <<" not in WORK_ORDER_INSTANCE


# --------------------------------------------------------------------------
# Done-native system template + the cfg override chokepoint
# --------------------------------------------------------------------------

def test_done_system_template_replaces_upstream_identity():
    """The engine's system prompt must be Done's, not upstream's SWE-bench solver
    identity. Upstream's 'helpful assistant that can interact with a computer' line
    must be gone; Done's identity must lead."""
    low = DONE_SYSTEM_TEMPLATE.lower()
    assert "helpful assistant that can interact with a computer" not in low
    assert "you are done" in low


def test_done_agent_cfg_overrides_both_system_and_instance():
    """done_agent_cfg is the single chokepoint the CLI, ACP, and headless paths use
    to strip upstream's framing: it swaps in Done's system_template AND the
    per-task instance_template, without mutating the caller's dict."""
    upstream = {"system_template": "You are a helpful assistant that can interact with a computer.\n",
                "instance_template": "Please solve this issue: {{task}}",
                "step_limit": 0}
    out = done_agent_cfg(upstream, "code_fix")
    assert out["system_template"] == DONE_SYSTEM_TEMPLATE      # upstream identity gone
    assert out["instance_template"] == WORK_ORDER_INSTANCE     # per-task framing applied
    assert out["step_limit"] == 0                              # other keys preserved
    # caller's dict untouched (built once at module scope, reused)
    assert upstream["system_template"].startswith("You are a helpful assistant")


def test_done_agent_cfg_respects_task_type():
    assert done_agent_cfg({}, "code_explain")["instance_template"] == ANSWER_ONLY_INSTANCE
    assert done_agent_cfg({}, "ops_task")["instance_template"] == OBSERVE_FIRST_INSTANCE


# --------------------------------------------------------------------------
# #307: an attached ACTION skill (create-job/create-persona) overrides the
# ops_task observe floor. The router keys on the "cron"/"agent" noun and can
# misfile a *create* request as ops_task; when it does, an action skill is
# attached, and the observe-only floor would hand the agent the create tool and
# forbid using it. A genuine observe request ("is the cron firing") attaches no
# action skill, so it must still get the floor — that is the differentiator.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("skill", sorted(ACTION_SKILLS))
def test_ops_task_with_action_skill_gets_work_order_not_observe(skill):
    # a create request the router misfiled as ops_task must still be allowed to act
    assert _instance_template_for("ops_task", DEFAULT, [skill]) == WORK_ORDER_INSTANCE
    assert done_agent_cfg({}, "ops_task", [skill])["instance_template"] == WORK_ORDER_INSTANCE


def test_ops_task_without_action_skill_still_observes():
    # GUARD: a genuine observe request (no action skill) keeps the observe floor.
    assert _instance_template_for("ops_task", DEFAULT, []) == OBSERVE_FIRST_INSTANCE
    # a non-action skill attached to an ops_task does NOT lift the floor
    assert _instance_template_for("ops_task", DEFAULT, ["systematic-debugging"]) == OBSERVE_FIRST_INSTANCE
    # and the chokepoint agrees
    assert done_agent_cfg({}, "ops_task", [])["instance_template"] == OBSERVE_FIRST_INSTANCE


def test_action_skill_mixed_with_others_still_lifts_floor():
    # the router commonly attaches create-job alongside a helper skill; presence of
    # ANY action skill is enough to lift the observe floor.
    mixed = ["verification-before-completion", "create-job"]
    assert _instance_template_for("ops_task", DEFAULT, mixed) == WORK_ORDER_INSTANCE


def test_action_skill_does_not_disturb_non_ops_task_types():
    # the guard is scoped to ops_task; other task types are unaffected whether or
    # not an action skill is attached.
    assert _instance_template_for("code_explain", DEFAULT, ["create-job"]) == ANSWER_ONLY_INSTANCE
    assert _instance_template_for("code_feature", DEFAULT, ["create-job"]) == WORK_ORDER_INSTANCE


def test_done_agent_cfg_skills_defaults_preserve_prior_behavior():
    # backward compat: the skills arg is optional; omitting it reproduces the
    # pre-#307 per-task-type behavior exactly (every existing caller/test relies
    # on this).
    assert done_agent_cfg({}, "ops_task")["instance_template"] == OBSERVE_FIRST_INSTANCE
    assert done_agent_cfg({}, "code_fix")["instance_template"] == WORK_ORDER_INSTANCE
