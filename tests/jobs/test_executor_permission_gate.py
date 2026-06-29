"""#168: file tools must be gated + workspace-confined on the headless cron-executor
path (and the dev-CLI path), reusing the same permcheck/decide_permission machinery
the ACP path uses — with deny-by-default because there is no elicitation channel.

These tests exercise the gating at the chokepoint level (TracingAgent._dispatch_tool
+ the file tools' parent_escapes recheck) against an env stamped exactly the way the
executor stamps it, plus the pure helper that builds that stamp.
"""
from pathlib import Path

import pytest

from harness.permcheck import PermissionRequest, decide_permission
from harness.jobs.executor import stamp_headless_gate


def _env(workspace: Path):
    """A minimal env stand-in carrying the two attributes the gate reads."""
    class _E:
        class config:
            cwd = str(workspace)
    e = _E()
    stamp_headless_gate(e, workspace)
    return e


# ── the headless policy is deny-by-default (no elicitation) ──────────────────

def test_decide_permission_headless_denies_out_of_root_write():
    req = PermissionRequest(kind="file", is_write=True, outside_roots=True)
    assert decide_permission(req, yolo=False, has_elicitation=False) == "deny"


def test_decide_permission_headless_denies_out_of_root_read():
    req = PermissionRequest(kind="file", is_write=False, outside_roots=True)
    assert decide_permission(req, yolo=False, has_elicitation=False) == "deny"


def test_decide_permission_headless_denies_bash():
    req = PermissionRequest(kind="bash", command="rm -rf /")
    assert decide_permission(req, yolo=False, has_elicitation=False) == "deny"


def test_decide_permission_in_root_write_allowed():
    req = PermissionRequest(kind="file", is_write=True, outside_roots=False)
    assert decide_permission(req, yolo=False, has_elicitation=False) == "allow"


# ── the executor stamps a working gate onto its env ──────────────────────────

def test_stamp_headless_gate_sets_check_and_roots(tmp_path):
    e = _env(tmp_path)
    assert e._allowed_roots == [tmp_path]
    # the stamped check denies an out-of-root write and allows an in-root one
    assert e._check_permission(
        PermissionRequest(kind="file", is_write=True, outside_roots=True)) is False
    assert e._check_permission(
        PermissionRequest(kind="file", is_write=True, outside_roots=False)) is True


def test_stamped_check_denies_bash(tmp_path):
    e = _env(tmp_path)
    assert e._check_permission(PermissionRequest(kind="bash", command="ls")) is False


# ── end-to-end: the issue's acceptance criterion ─────────────────────────────

def test_headless_write_outside_workspace_denied_and_not_created(tmp_path):
    """#168 acceptance: a headless run (executor-stamped env) writing OUTSIDE the
    workspace is denied at the chokepoint and the file is never created."""
    import yaml
    from minisweagent.environments.local import LocalEnvironment
    from harness.events import Emitter
    from harness.models_mock import build_mock_model
    from harness.tracing_agent import TracingAgent

    workspace = tmp_path / "ws"; workspace.mkdir()
    target = tmp_path / "escape.txt"          # sibling of workspace, outside it

    cfg = yaml.safe_load(Path("upstream/src/minisweagent/config/mini.yaml").read_text())["agent"]
    cfg["output_path"] = str(tmp_path / "traj.json")
    emitter = Emitter(tmp_path / "events.jsonl", clock=lambda: 0.0, console=False)
    env = LocalEnvironment(cwd=str(workspace))
    stamp_headless_gate(env, workspace)        # exactly what the executor does
    agent = TracingAgent(build_mock_model(), env, emitter=emitter, **cfg)

    msg = {"extra": {"actions": [
        {"tool_name": "write", "args": {"path": str(target), "content": "pwn"},
         "tool_call_id": "c0"}]}}
    out = agent.execute_actions(msg)
    assert not target.exists()                 # never written — deny-by-default held
    assert "denied" in out[0]["content"].lower()


def test_headless_write_inside_workspace_allowed(tmp_path):
    import yaml
    from minisweagent.environments.local import LocalEnvironment
    from harness.events import Emitter
    from harness.models_mock import build_mock_model
    from harness.tracing_agent import TracingAgent

    workspace = tmp_path / "ws"; workspace.mkdir()
    cfg = yaml.safe_load(Path("upstream/src/minisweagent/config/mini.yaml").read_text())["agent"]
    cfg["output_path"] = str(tmp_path / "traj.json")
    emitter = Emitter(tmp_path / "events.jsonl", clock=lambda: 0.0, console=False)
    env = LocalEnvironment(cwd=str(workspace))
    stamp_headless_gate(env, workspace)
    agent = TracingAgent(build_mock_model(), env, emitter=emitter, **cfg)

    msg = {"extra": {"actions": [
        {"tool_name": "write", "args": {"path": "note.txt", "content": "ok"},
         "tool_call_id": "c1"}]}}
    agent.execute_actions(msg)
    assert (workspace / "note.txt").read_text() == "ok"
