import pytest
from harness.instance_templates import (
    ANSWER_ONLY_INSTANCE, OBSERVE_FIRST_INSTANCE, WORK_ORDER_INSTANCE,
    _instance_template_for,
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
