"""A canned tool-call model: deterministic, zero-cost, ends in submission.

The command sequence, run in examples/sample-repo, fixes the `add` bug then
submits. Mirrors upstream tests/agents/test_default.py::make_tc_model so the
tool-call/observation pairing is exactly the shape the real LitellmModel emits.
"""

from __future__ import annotations

from minisweagent.models.test_models import DeterministicToolcallModel, make_toolcall_output

# (assistant_text, [commands]) per turn.
# Commands run with cwd=examples/sample-repo (set by the runner), so no cd prefix needed.
_TURNS: list[tuple[str, list[str]]] = [
    ("Let me reproduce the failure first.",
     ["python3 -m pytest test_calculator.py -q || true"]),
    ("The add() function subtracts. I'll fix it.",
     ["sed -i '' 's/return a - b/return a + b/' calculator.py"]),
    ("Re-running the test to confirm the fix.",
     ["python3 -m pytest test_calculator.py -q"]),
    ("Test passes. Submitting.",
     ["echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"]),
]


def _make_tc_model(turns: list[tuple[str, list[str]]]) -> DeterministicToolcallModel:
    outputs = []
    for i, (content, commands) in enumerate(turns):
        tc_actions, tool_calls = [], []
        for j, command in enumerate(commands):
            tcid = f"call_{i}_{j}"
            tc_actions.append({"command": command, "tool_call_id": tcid})
            tool_calls.append({
                "id": tcid,
                "type": "function",
                "function": {"name": "bash", "arguments": '{"command": ' + _json_str(command) + "}"},
            })
        out = make_toolcall_output(content, tool_calls, tc_actions)
        out["extra"]["cost"] = 0.0  # make_toolcall_output hardcodes 1.0; zero it so cost_limit isn't hit
        outputs.append(out)
    return DeterministicToolcallModel(outputs=outputs, cost_per_call=0.0)


def _json_str(s: str) -> str:
    import json
    return json.dumps(s)


def build_mock_model() -> DeterministicToolcallModel:
    return _make_tc_model(_TURNS)
