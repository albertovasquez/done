import json
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

import yaml
from minisweagent.environments.local import LocalEnvironment
from minisweagent.models.test_models import DeterministicToolcallModel, make_toolcall_output

from harness.events import Emitter
from harness.models_mock import build_mock_model
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


def _build_agent(tmp_path, turns, cwd):
    """Construct a TracingAgent with a deterministic model (does NOT run it)."""
    emitter = Emitter(tmp_path / "events.jsonl", clock=lambda: 0.0, console=False)
    model = _tc_model(turns)
    env = LocalEnvironment(cwd=str(cwd))
    agent_cfg = _agent_config()
    agent_cfg["output_path"] = str(tmp_path / "traj.json")
    return TracingAgent(model, env, emitter=emitter, **agent_cfg)


def _run(tmp_path, turns, cwd):
    agent = _build_agent(tmp_path, turns, cwd)
    agent.run("dummy task")
    agent._emitter.close()
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


_SUBMIT = [("done", ["echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"])]


def test_run_with_prior_seeds_messages_between_system_and_instance(tmp_path):
    agent = _build_agent(tmp_path, _SUBMIT, cwd=tmp_path)
    prior = [{"role": "user", "content": "earlier"},
             {"role": "assistant", "content": "reply"}]
    agent.run("the task", prior=prior)
    assert agent.messages[0]["role"] == "system"
    assert agent.messages[1] == {"role": "user", "content": "earlier"}
    assert agent.messages[2] == {"role": "assistant", "content": "reply"}
    assert agent.messages[3]["role"] == "user"          # the fresh instance/task message
    assert "the task" in agent.messages[3]["content"]


def test_run_without_prior_unchanged(tmp_path):
    agent = _build_agent(tmp_path, _SUBMIT, cwd=tmp_path)
    agent.run("the task")
    assert agent.messages[0]["role"] == "system"
    assert agent.messages[1]["role"] == "user"          # instance directly after system
    assert "the task" in agent.messages[1]["content"]


def _make_agent(tmp_path, **blocks):
    em = Emitter(tmp_path / "e.jsonl", clock=lambda: 0.0, console=False)
    return TracingAgent(
        build_mock_model(), LocalEnvironment(cwd=str(tmp_path)), emitter=em,
        system_template="SYS BASE", instance_template="INST {{task}}",
        **blocks)


def test_base_block_prepended_before_persona_in_system_template(tmp_path):
    agent = _make_agent(tmp_path, base_block="BASEBLOCK", persona_block="PERSONA")
    rendered = agent._render_template(agent.config.system_template)
    assert "BASEBLOCK" in rendered
    # base block comes before persona in the appended order
    assert rendered.index("BASEBLOCK") < rendered.index("PERSONA")


def test_base_block_not_added_to_instance_template(tmp_path):
    agent = _make_agent(tmp_path, base_block="BASEBLOCK")
    agent.extra_template_vars = {"task": "t"}
    rendered = agent._render_template(agent.config.instance_template)
    assert "BASEBLOCK" not in rendered


# ---- cancel_flag: the engine loop must stop between steps when set ----

def test_cancel_flag_stops_loop_between_steps(tmp_path):
    # A multi-step turn (3 non-terminal turns then submit). The flag is set after
    # the first step runs, so the loop must end on the cancel checkpoint BEFORE
    # consuming the rest — exit_status "cancelled", fewer LLM calls than turns.
    flag = threading.Event()
    turns = [
        ("step1", ["echo a"]),
        ("step2", ["echo b"]),
        ("step3", ["echo c"]),
        ("done", ["echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"]),
    ]
    emitter = Emitter(tmp_path / "events.jsonl", clock=lambda: 0.0, console=False)
    model = _tc_model(turns)
    env = LocalEnvironment(cwd=str(tmp_path))
    cfg = _agent_config()
    cfg["output_path"] = str(tmp_path / "traj.json")
    agent = TracingAgent(model, env, emitter=emitter, cancel_flag=flag, **cfg)

    # set the flag the moment the first LLM call fires, so step 2 is never reached
    orig_query = agent.query
    def query_then_cancel():
        result = orig_query()
        flag.set()
        return result
    agent.query = query_then_cancel

    result = agent.run("dummy task")
    assert result.get("exit_status") == "cancelled"
    assert agent.n_calls == 1, "loop kept calling the model after cancel"


def test_no_cancel_flag_runs_normally(tmp_path):
    # Regression: an agent built without a cancel_flag (CLI/mock) behaves as before.
    agent = _build_agent(tmp_path, _SUBMIT, cwd=tmp_path)
    assert agent.run("the task").get("exit_status") == "Submitted"
