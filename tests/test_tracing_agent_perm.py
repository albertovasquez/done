from pathlib import Path  # noqa: E402

import yaml  # noqa: E402
from minisweagent.environments.local import LocalEnvironment  # noqa: E402

from harness.events import Emitter  # noqa: E402
from harness.models_mock import build_mock_model  # noqa: E402
from harness.tracing_agent import TracingAgent  # noqa: E402


def _agent(tmp_path, cwd, *, allow, roots):
    cfg = yaml.safe_load(Path("upstream/src/minisweagent/config/mini.yaml").read_text())["agent"]
    cfg["output_path"] = str(tmp_path / "traj.json")
    emitter = Emitter(tmp_path / "events.jsonl", clock=lambda: 0.0, console=False)
    env = LocalEnvironment(cwd=str(cwd))
    env._check_permission = allow          # Callable[[PermissionRequest], bool]
    env._allowed_roots = roots
    return TracingAgent(build_mock_model(), env, emitter=emitter, **cfg)


def test_outside_root_write_denied_and_not_written(tmp_path):
    root = tmp_path / "proj"; root.mkdir()
    target = tmp_path / "outside.txt"      # sibling of root, NOT inside it
    agent = _agent(tmp_path, root, allow=lambda req: False, roots=[root])
    msg = {"extra": {"actions": [
        {"tool_name": "write", "args": {"path": str(target), "content": "x"},
         "tool_call_id": "c0"}]}}
    out = agent.execute_actions(msg)
    assert not target.exists()             # #102+#106+#107: never written
    assert "denied" in out[0]["content"].lower()


def test_in_root_write_allowed_and_written(tmp_path):
    root = tmp_path / "proj"; root.mkdir()
    target = root / "ok.txt"
    agent = _agent(tmp_path, root, allow=lambda req: True, roots=[root])
    msg = {"extra": {"actions": [
        {"tool_name": "write", "args": {"path": "ok.txt", "content": "hi"},
         "tool_call_id": "c1"}]}}
    agent.execute_actions(msg)
    assert target.read_text() == "hi"


def test_gate_sees_file_kind_and_outside_flag(tmp_path):
    root = tmp_path / "proj"; root.mkdir()
    seen = []
    agent = _agent(tmp_path, root,
                   allow=lambda req: (seen.append(req) or True), roots=[root])
    msg = {"extra": {"actions": [
        {"tool_name": "write", "args": {"path": "../escape.txt", "content": "x"},
         "tool_call_id": "c2"}]}}
    agent.execute_actions(msg)
    assert seen and seen[0].kind == "file"
    assert seen[0].is_write is True and seen[0].outside_roots is True


def test_bash_still_routes_through_env(tmp_path):
    # bash is gated INSIDE env.execute (LocalEnvironment has no gate, so it just
    # runs) — the chokepoint must NOT add a second file-style gate for bash.
    agent = _agent(tmp_path, tmp_path, allow=lambda req: True, roots=[tmp_path])
    msg = {"extra": {"actions": [{"command": "echo hi", "tool_call_id": "c3"}]}}
    out = agent.execute_actions(msg)
    assert "hi" in out[0]["content"]       # bash path unchanged


def test_file_tool_runs_when_no_check_permission(tmp_path):
    # A plain env with NO _check_permission stamped: file tools execute ungated
    # (backward compat — the gate only engages when an env opts in).
    root = tmp_path / "proj"; root.mkdir()
    target = root / "f.txt"
    agent = _agent(tmp_path, root, allow=lambda req: False, roots=[root])
    # remove the gate that _agent stamped, to simulate an ungated env
    delattr(agent.env, "_check_permission")
    msg = {"extra": {"actions": [
        {"tool_name": "write", "args": {"path": "f.txt", "content": "hi"},
         "tool_call_id": "n0"}]}}
    agent.execute_actions(msg)
    assert target.read_text() == "hi"   # ran despite allow=False, because no gate stamped


def test_internal_tool_not_gated(tmp_path):
    # An internal (non-file) tool name must bypass the file-tool gate entirely:
    # _dispatch_tool should call tool.execute without consulting _check_permission.
    root = tmp_path / "proj"; root.mkdir()
    called = {}
    class _FakeInternalTool:
        name = "load_memory"
        def display_label(self, args): return "load_memory"
        def execute(self, args, env):
            called["ran"] = True
            return {"output": "mem", "returncode": 0, "exception_info": None}
    agent = _agent(tmp_path, root, allow=lambda req: False, roots=[root])
    agent._tools_by_name["load_memory"] = _FakeInternalTool()
    msg = {"extra": {"actions": [
        {"tool_name": "load_memory", "args": {}, "tool_call_id": "i0"}]}}
    agent.execute_actions(msg)
    assert called.get("ran") is True   # ran even though allow=False (not gated)
