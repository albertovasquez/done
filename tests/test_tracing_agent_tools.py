import sys

sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

from pathlib import Path  # noqa: E402

import yaml  # noqa: E402
from minisweagent.environments.local import LocalEnvironment  # noqa: E402

from harness.events import Emitter  # noqa: E402
from harness.models_mock import build_mock_model  # noqa: E402
from harness.tracing_agent import TracingAgent  # noqa: E402


def _agent(tmp_path, cwd):
    cfg = yaml.safe_load(Path("upstream/src/minisweagent/config/mini.yaml").read_text())["agent"]
    cfg["output_path"] = str(tmp_path / "traj.json")
    emitter = Emitter(tmp_path / "events.jsonl", clock=lambda: 0.0, console=False)
    return TracingAgent(build_mock_model(), LocalEnvironment(cwd=str(cwd)), emitter=emitter, **cfg)


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
