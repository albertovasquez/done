"""Tool-call id pairing — the load-bearing invariant for the permission /
tool-call handshake, which was previously UNTESTED (surfaced by the core-refactor
adversarial review).

AcpEnvironment.execute drives a per-command handshake:
  on_command("start")  -> emits tool_call_start(id)   and sets the current id
  request_permission   -> the permission modal must carry that SAME id
  on_command("done"/"rejected") -> emits tool_call_done(SAME id)

start and done must carry the same id, the permission modal must carry that id,
and ids must RESET per turn (a fresh turn starts again at tc1). These tests lock
all three so a refactor that relocates the id state can't silently break the wire
contract while keeping the rest of the suite green.

Driven directly at HarnessAgent.prompt() (no subprocess), modelled on
test_acp_agent_streaming.py.
"""

import asyncio
import sys

sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

import acp
from acp.schema import AllowedOutcome

from minisweagent.models.test_models import DeterministicToolcallModel, make_toolcall_output

from harness.acp_agent import build_harness_agent
from harness.router import Classification

_SUBMIT = "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"


class RecordingConn:
    """Captures session_update tool-call ids and serves request_permission with a
    fixed outcome, recording the tool_call_id the modal was handed."""

    def __init__(self, *, permission_outcome=None):
        self.updates = []
        self.permission_ids = []          # tool_call_id seen by each request_permission
        self._permission_outcome = permission_outcome

    async def session_update(self, session_id, update, **kw):
        self.updates.append(update)

    async def request_permission(self, *, options, session_id, tool_call):
        self.permission_ids.append(tool_call.tool_call_id)

        class _Resp:
            outcome = self._permission_outcome
        return _Resp()

    def starts(self):
        """tool_call_id of every tool_call_start (type ToolCallStart), in order."""
        return [u.tool_call_id for u in self.updates
                if type(u).__name__ == "ToolCallStart"]

    def dones(self):
        """tool_call_id of every tool_call_done (emitted as a ToolCallProgress
        update — see harness.acp_emit.tool_call_done), in order."""
        return [u.tool_call_id for u in self.updates
                if type(u).__name__ == "ToolCallProgress"
                and getattr(u, "tool_call_id", None) is not None]


class _ScriptedRouter:
    catalog = []

    def classify(self, text, history=None):
        return Classification(task_type="code_fix", skills=[], confidence=1.0)


def _cmd_output(command, call_id):
    out = make_toolcall_output(
        "working",
        [{"id": call_id, "type": "function",
          "function": {"name": "bash", "arguments": '{"command": "' + command + '"}'}}],
        [{"command": command, "tool_call_id": call_id}],
    )
    out["extra"]["cost"] = 0.0
    return out


class _RunThenSubmitModel(DeterministicToolcallModel):
    """Step 1 runs a REAL command (echo hi) → completes → emits start AND done
    (the submit sentinel raises before 'done', so it can't exercise the done leg).
    Step 2 submits so the loop exits."""

    def __init__(self):
        super().__init__(
            outputs=[_cmd_output("echo hi", "call_0"),
                     _cmd_output(_SUBMIT, "call_1")],
            cost_per_call=0.0)


def _agent_cfg():
    import yaml
    from pathlib import Path
    return yaml.safe_load(Path("upstream/src/minisweagent/config/mini.yaml").read_text())["agent"]


def _build(conn, *, yolo, client_caps):
    from pathlib import Path
    agent = build_harness_agent(
        model_factory=lambda *a, **k: _RunThenSubmitModel(),
        agent_cfg=_agent_cfg(),
        skills_dir=Path("skills"),
        router=_ScriptedRouter(),
        worker_model_id="gpt-5.4",
    )
    agent._conn = conn
    agent._client_caps = client_caps
    agent._yolo = yolo
    return agent


def _prompt(agent, sid, text):
    return asyncio.run(agent.prompt([acp.text_block(text)], sid))


def test_tool_call_done_id_matches_start_id(tmp_path):
    """Every tool_call_done must carry the same id as the tool_call_start that
    preceded it (yolo path: no permission round-trip)."""
    conn = RecordingConn()
    agent = _build(conn, yolo=True, client_caps=None)
    sid = agent._store.new(cwd=str(tmp_path))
    _prompt(agent, sid, "fix the bug")

    starts, dones = conn.starts(), conn.dones()
    assert starts, "no tool_call_start emitted"
    assert dones, "no tool_call_done (ToolCallProgress) emitted"
    # the first completed command's done id equals its start id
    assert starts[0] == dones[0], f"start/done id mismatch: starts={starts!r} dones={dones!r}"


class _Caps:
    """Minimal client_caps advertising elicitation so request_permission fires."""
    class _Elic:
        pass
    elicitation = _Elic()
    terminal = None


def test_permission_modal_sees_the_start_id(tmp_path):
    """When a permission round-trip happens, the modal's tool_call_id must equal
    the id of the just-started tool call — and the subsequent done/rejected uses
    that same id."""
    conn = RecordingConn(
        permission_outcome=AllowedOutcome(option_id="allow_once", outcome="selected"))
    agent = _build(conn, yolo=False, client_caps=_Caps())
    sid = agent._store.new(cwd=str(tmp_path))
    _prompt(agent, sid, "fix the bug")

    assert conn.permission_ids, "request_permission was never called"
    starts = conn.starts()
    assert starts, "no tool_call_start emitted"
    # the modal saw the same id as the start, and that id is well-formed (tcN)
    assert conn.permission_ids[0] == starts[0], (
        f"permission modal id {conn.permission_ids[0]!r} != start id {starts[0]!r}")
    assert starts[0].startswith("tc")
    # and the done emitted AFTER the allowed permission carries that same id —
    # the full start→permission→done handshake on the permission path uses one id.
    dones = conn.dones()
    assert dones, "no tool_call_done after allow_once"
    assert dones[0] == starts[0], (
        f"done id {dones[0]!r} != start id {starts[0]!r} on the permission path")


def test_tool_call_ids_reset_per_turn(tmp_path):
    """A second turn in the same session must restart ids at tc1 — the id counter
    is per-turn, not cumulative across the session."""
    conn1 = RecordingConn()
    agent = _build(conn1, yolo=True, client_caps=None)
    sid = agent._store.new(cwd=str(tmp_path))
    _prompt(agent, sid, "first turn")
    first_starts = conn1.starts()

    # second turn, fresh conn so we read only this turn's ids
    conn2 = RecordingConn()
    agent._conn = conn2
    _prompt(agent, sid, "second turn")
    second_starts = conn2.starts()

    assert first_starts and second_starts, "expected a tool_call in each turn"
    # both turns start their numbering at the same first id (tc1) — ids reset
    assert first_starts[0] == second_starts[0], (
        f"ids did not reset per turn: turn1 {first_starts!r} vs turn2 {second_starts!r}")
