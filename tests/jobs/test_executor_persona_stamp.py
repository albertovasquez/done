from pathlib import Path

from harness.agent_build import build_persona_agent


def test_builder_stamps_active_persona(tmp_path):
    # The cron path previously built a bare LocalEnvironment with no _active_persona.
    # The builder must stamp it so env-bound tools (create_job, subagent) resolve.
    runner, _ = build_persona_agent(
        "alice", model_name=None, agent_cfg={"step_limit": 0},
        memory_root=tmp_path, cwd=str(tmp_path),
    )
    assert getattr(runner._env, "_active_persona", None) == "alice"
