from pathlib import Path  # noqa: E402

import yaml  # noqa: E402
from minisweagent.environments.local import LocalEnvironment  # noqa: E402
from minisweagent.models.test_models import (  # noqa: E402
    DeterministicToolcallModel,
    make_toolcall_output,
)

from harness.events import Emitter  # noqa: E402
from harness.models_mock import build_mock_model  # noqa: E402
from harness.tracing_agent import TracingAgent  # noqa: E402


def _agent(tmp_path, cwd):
    cfg = yaml.safe_load(Path("upstream/src/minisweagent/config/mini.yaml").read_text())["agent"]
    cfg["output_path"] = str(tmp_path / "traj.json")
    emitter = Emitter(tmp_path / "events.jsonl", clock=lambda: 0.0, console=False)
    return TracingAgent(build_mock_model(), LocalEnvironment(cwd=str(cwd)), emitter=emitter, **cfg)


class _CreateJobTool:
    name = "create_job"
    schema = {"type": "function", "function": {"name": "create_job"}}

    def __init__(self, *, returncode=0):
        self.returncode = returncode

    def display_label(self, args):
        return "create_job test"

    def execute(self, args, env):
        if self.returncode == 0:
            return {"output": "Created job abc123 (test) for persona 'default'.",
                    "returncode": 0, "exception_info": None}
        return {"output": "Could not create job: bad schedule",
                "returncode": 1, "exception_info": None}


def _agent_with_registry(tmp_path, cwd, registry):
    cfg = yaml.safe_load(Path("upstream/src/minisweagent/config/mini.yaml").read_text())["agent"]
    cfg["output_path"] = str(tmp_path / "traj.json")
    emitter = Emitter(tmp_path / "events.jsonl", clock=lambda: 0.0, console=False)
    return TracingAgent(
        build_mock_model(), LocalEnvironment(cwd=str(cwd)), emitter=emitter,
        registry=registry, **cfg)


def test_file_tool_action_dispatches_to_tool(tmp_path):
    (tmp_path / "a.txt").write_text("data")
    agent = _agent(tmp_path, tmp_path)
    msg = {"extra": {"actions": [{"tool_name": "read", "args": {"path": "a.txt"}, "tool_call_id": "c0"}]}}
    out_msgs = agent.execute_actions(msg)
    assert out_msgs[0]["role"] == "tool"
    assert out_msgs[0]["tool_call_id"] == "c0"
    assert "data" in out_msgs[0]["content"]


def test_action_without_tool_name_dispatches_as_bash(tmp_path):
    agent = _agent(tmp_path, tmp_path)
    msg = {"extra": {"actions": [{"command": "echo hi", "tool_call_id": "c1"}]}}
    out_msgs = agent.execute_actions(msg)  # must NOT raise; bash path
    assert out_msgs[0]["tool_call_id"] == "c1"
    assert "hi" in out_msgs[0]["content"]


def test_mixed_actions_pair_to_correct_ids(tmp_path):
    (tmp_path / "b.txt").write_text("zzz")
    agent = _agent(tmp_path, tmp_path)
    msg = {"extra": {"actions": [
        {"command": "echo one", "tool_call_id": "a"},
        {"tool_name": "read", "args": {"path": "b.txt"}, "tool_call_id": "b"},
    ]}}
    out = agent.execute_actions(msg)
    by_id = {m["tool_call_id"]: m["content"] for m in out}
    assert "one" in by_id["a"] and "zzz" in by_id["b"]


def test_successful_create_job_terminates_turn_with_tool_output(tmp_path):
    agent = _agent_with_registry(tmp_path, tmp_path, [_CreateJobTool(returncode=0)])
    msg = {"extra": {"actions": [
        {"tool_name": "create_job", "args": {}, "tool_call_id": "job"},
    ]}}
    out = agent.execute_actions(msg)

    assert out[0]["role"] == "tool"
    assert out[0]["tool_call_id"] == "job"
    assert agent.messages[-1]["role"] == "exit"
    assert agent.messages[-1]["extra"] == {
        "exit_status": "Submitted",
        "submission": "Created job abc123 (test) for persona 'default'.",
    }


def test_failed_create_job_does_not_terminate_turn(tmp_path):
    agent = _agent_with_registry(tmp_path, tmp_path, [_CreateJobTool(returncode=1)])
    msg = {"extra": {"actions": [
        {"tool_name": "create_job", "args": {}, "tool_call_id": "job"},
    ]}}
    agent.execute_actions(msg)

    assert agent.messages[-1]["role"] == "tool"
    assert "Could not create job" in agent.messages[-1]["content"]


def test_run_stops_after_successful_create_job_without_second_llm_call(tmp_path):
    first = make_toolcall_output(
        "Creating the job now.",
        [{"id": "job", "type": "function",
          "function": {"name": "create_job", "arguments": "{}"}}],
        [{"tool_name": "create_job", "args": {}, "tool_call_id": "job"}],
    )
    second = make_toolcall_output(
        "This should not run.",
        [{"id": "again", "type": "function",
          "function": {"name": "create_job", "arguments": "{}"}}],
        [{"tool_name": "create_job", "args": {}, "tool_call_id": "again"}],
    )
    first["extra"]["cost"] = second["extra"]["cost"] = 0.0
    agent = _agent_with_registry(tmp_path, tmp_path, [_CreateJobTool(returncode=0)])
    agent.model = DeterministicToolcallModel(outputs=[first, second], cost_per_call=0.0)

    result = agent.run("create a reminder")

    assert result["exit_status"] == "Submitted"
    assert result["submission"] == "Created job abc123 (test) for persona 'default'."
    assert agent.n_calls == 1
