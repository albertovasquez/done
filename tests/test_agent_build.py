from harness.agent_build import build_persona_agent


def _agent_cfg():
    # Minimal agent config dict (the keys DefaultAgent.AgentConfig accepts).
    return {"step_limit": 0, "cost_limit": 0, "wall_time_limit_seconds": 0}


def test_mock_model_when_model_name_none(tmp_path):
    runner, registry = build_persona_agent(
        "default", model_name=None, agent_cfg=_agent_cfg(),
        memory_root=tmp_path,
    )
    # Mock path: runner is constructed, env is stamped with the persona.
    assert runner._env._active_persona == "default"


def test_step_and_walltime_override_applied(tmp_path):
    runner, _ = build_persona_agent(
        "default", model_name=None, agent_cfg=_agent_cfg(),
        memory_root=tmp_path, step_limit=15, wall_time_limit=30,
    )
    assert runner._agent_cfg["step_limit"] == 15
    assert runner._agent_cfg["wall_time_limit_seconds"] == 30
    # Original cfg dict not mutated (builder copies).
    # (re-call with a fresh cfg and check independence)


def test_worker_registry_excludes_subagent(tmp_path):
    runner, registry = build_persona_agent(
        "default", model_name=None, agent_cfg=_agent_cfg(),
        memory_root=tmp_path, toolset={"read", "bash"}, is_worker=True,
    )
    assert "subagent" not in {t.name for t in registry}
    assert {"read", "bash"} == {t.name for t in registry}
