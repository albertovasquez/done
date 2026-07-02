"""Byte-stability of the system prompt across turns (#139 PR2).

The append-only invariant (docs/superpowers/specs/
2026-07-02-prompt-cache-prefix-stability-design.md): within a session the
system message must be byte-identical across turns unless a declared
boundary fires. Router-picked skills differ per turn, so skill bodies must
ride the instance message, never the system message.
"""
import json
from pathlib import Path

import yaml
from minisweagent.environments.local import LocalEnvironment
from minisweagent.models.test_models import DeterministicToolcallModel, make_toolcall_output

from harness.events import Emitter
from harness.tracing_agent import TracingAgent


def _agent_cfg() -> dict:
    cfg = yaml.safe_load(Path("upstream/src/minisweagent/config/mini.yaml").read_text())
    return cfg["agent"]


def _submit_model():
    tcid = "call_0_0"
    return DeterministicToolcallModel(outputs=[make_toolcall_output(
        "done",
        [{"id": tcid, "type": "function",
          "function": {"name": "bash", "arguments": json.dumps(
              {"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"})}}],
        [{"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT",
          "tool_call_id": tcid}],
    )], cost_per_call=0.0)


def _run_turn(tmp_path, skill_block: str, name: str, env_block: str = "ENVBLOCK"):
    emitter = Emitter(tmp_path / f"{name}.jsonl", clock=lambda: 0.0, console=False)
    cfg = _agent_cfg()
    cfg["output_path"] = str(tmp_path / f"{name}-traj.json")
    agent = TracingAgent(
        _submit_model(), LocalEnvironment(cwd=str(tmp_path)), emitter=emitter,
        skill_block=skill_block, base_block="BASEBLOCK", persona_block="PERSONA",
        memory_block="MEMORY", env_block=env_block, **cfg)
    agent.run("same task every turn")
    emitter.close()
    system = agent.messages[0]["content"]
    instance = agent.messages[1]["content"]
    return system, instance


def test_system_prompt_byte_stable_when_skills_differ(tmp_path):
    sys_a, inst_a = _run_turn(tmp_path, "SKILL-BODY-A", "a")
    sys_b, inst_b = _run_turn(tmp_path, "SKILL-BODY-B", "b")
    assert sys_a == sys_b                       # THE invariant
    assert "SKILL-BODY-A" not in sys_a
    assert "SKILL-BODY-A" in inst_a
    assert "## Skills loaded for this task" in inst_a
    assert "SKILL-BODY-B" in inst_b


def test_no_skill_block_leaves_instance_untouched(tmp_path):
    _, inst = _run_turn(tmp_path, "", "none")
    assert "## Skills loaded for this task" not in inst


def test_env_block_is_system_suffix(tmp_path):
    sys_a, _ = _run_turn(tmp_path, "", "envcheck")
    assert sys_a.endswith("ENVBLOCK")


def test_model_swap_changes_only_the_env_suffix(tmp_path):
    # Spec §2b: a mid-session model swap must invalidate ONLY the final env
    # block — the prefix above it stays byte-identical.
    sys_a, _ = _run_turn(tmp_path, "", "swap-a", env_block="ENV-MODEL-A")
    sys_b, _ = _run_turn(tmp_path, "", "swap-b", env_block="ENV-MODEL-B")
    assert sys_a.endswith("ENV-MODEL-A") and sys_b.endswith("ENV-MODEL-B")
    assert sys_a[:-len("ENV-MODEL-A")] == sys_b[:-len("ENV-MODEL-B")]
