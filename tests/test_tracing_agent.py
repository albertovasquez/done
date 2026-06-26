import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

import yaml
from minisweagent.environments.local import LocalEnvironment
from minisweagent.models.test_models import DeterministicToolcallModel, make_toolcall_output

from harness.events import Emitter
from harness.tracing_agent import TracingAgent


def _agent_config() -> dict:
    cfg = yaml.safe_load(Path("upstream/src/minisweagent/config/mini.yaml").read_text())
    return cfg["agent"]


def _tc_model(turns):
    outputs = []
    for i, (content, commands) in enumerate(turns):
        tc_actions, tool_calls = [], []
        for j, command in enumerate(commands):
            tcid = f"call_{i}_{j}"
            tc_actions.append({"command": command, "tool_call_id": tcid})
            tool_calls.append({
                "id": tcid, "type": "function",
                "function": {"name": "bash", "arguments": json.dumps({"command": command})},
            })
        outputs.append(make_toolcall_output(content, tool_calls, tc_actions))
    return DeterministicToolcallModel(outputs=outputs, cost_per_call=0.0)


def _run(tmp_path, turns, cwd):
    emitter = Emitter(tmp_path / "events.jsonl", clock=lambda: 0.0, console=False)
    model = _tc_model(turns)
    env = LocalEnvironment(cwd=str(cwd))
    agent_cfg = _agent_config()
    agent_cfg["output_path"] = str(tmp_path / "traj.json")
    agent = TracingAgent(model, env, emitter=emitter, **agent_cfg)
    agent.run("dummy task")
    emitter.close()
    records = [json.loads(l) for l in (tmp_path / "events.jsonl").read_text().splitlines()]
    return records


def test_A_happy_path_sequence(tmp_path):
    turns = [
        ("hello", ["echo hi"]),
        ("done", ["echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"]),
    ]
    records = _run(tmp_path, turns, cwd=tmp_path)
    types = [r["type"] for r in records]
    assert types[0] == "run.started"
    assert types[-1] == "run.finished"
    assert "llm.call" in types and "llm.return" in types
    assert "action" in types and "action.done" in types
    assert records[-1]["data"]["ok"] is True
    # seq is strictly increasing from 0
    assert [r["seq"] for r in records] == list(range(len(records)))


def test_B_terminal_submission_emits_action_done(tmp_path):
    # The FINAL action is the submit sentinel, which makes env.execute raise
    # Submitted BEFORE returning. action.done for that action must still appear.
    turns = [("done", ["echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"])]
    records = _run(tmp_path, turns, cwd=tmp_path)

    # Find the submit action and assert a following action.done exists.
    submit_idx = next(i for i, r in enumerate(records)
                      if r["type"] == "action"
                      and "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT" in r["data"]["command"])
    later_types = [r["type"] for r in records[submit_idx + 1:]]
    assert "action.done" in later_types, "final action.done was dropped on Submitted"
    assert records[-1]["type"] == "run.finished"
    assert records[-1]["data"]["ok"] is True
    assert records[-1]["data"]["exit_status"] == "Submitted"
