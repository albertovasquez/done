import asyncio
import pytest
from harness import acp_main, paths


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("MSWEA_SILENT_STARTUP", "1")
    return tmp_path


class _Stop(Exception):
    pass


def _spy_workspace(monkeypatch):
    """Patch HarnessAgent to capture workspace_dir then abort before run_agent."""
    captured = {}
    import harness.acp_agent as agent_mod

    def fake_init(self, **kw):
        captured["workspace_dir"] = kw.get("workspace_dir")
        raise _Stop()
    monkeypatch.setattr(agent_mod.HarnessAgent, "__init__", fake_init)
    return captured


def _spy_model(monkeypatch):
    """Patch HarnessAgent to capture worker_model_id then abort before run_agent."""
    captured = {}
    import harness.acp_agent as agent_mod

    def fake_init(self, **kw):
        captured["worker_model_id"] = kw.get("worker_model_id")
        raise _Stop()
    monkeypatch.setattr(agent_mod.HarnessAgent, "__init__", fake_init)
    return captured


def test_no_persona_uses_default_workspace(monkeypatch):
    captured = _spy_workspace(monkeypatch)
    with pytest.raises(_Stop):
        asyncio.run(acp_main._main(["--model", "mock", "--cwd", "."]))
    assert captured["workspace_dir"] == paths.default_workspace_dir()


def test_named_persona_uses_its_workspace(monkeypatch, tmp_path):
    ws = paths.config_dir() / "agents" / "fred"
    ws.mkdir(parents=True)
    captured = _spy_workspace(monkeypatch)
    with pytest.raises(_Stop):
        asyncio.run(acp_main._main(["--model", "mock", "--cwd", ".", "--persona", "fred"]))
    assert captured["workspace_dir"] == ws


def test_unknown_persona_exits_nonzero(monkeypatch, capsys):
    captured = _spy_workspace(monkeypatch)   # must never fire for an unknown id
    with pytest.raises(SystemExit) as exc:
        asyncio.run(acp_main._main(["--model", "mock", "--cwd", ".", "--persona", "ghost"]))
    assert exc.value.code != 0
    assert "ghost" in capsys.readouterr().err
    assert captured == {}   # the agent was NEVER constructed — no boot onto a bad persona


# --- F2 model/workspace split-brain tests ---

def test_standalone_persona_uses_persisted_model(monkeypatch, tmp_path):
    """Standalone dn-agent --persona fred must use fred's persisted done.conf model,
    not the engine default, when VIBEPROXY_MODEL is not in the environment."""
    from harness import config
    # Create the workspace dir so persona resolution succeeds
    ws = paths.config_dir() / "agents" / "fred"
    ws.mkdir(parents=True)
    # Persist fred's model in done.conf
    config.save_agent("fred", config.AgentConfig(backend="vibeproxy", model="m-fred"))
    # Ensure no VIBEPROXY_MODEL in env (standalone path)
    monkeypatch.delenv("VIBEPROXY_MODEL", raising=False)

    captured = _spy_model(monkeypatch)
    with pytest.raises(_Stop):
        asyncio.run(acp_main._main(["--model", "vibeproxy", "--cwd", ".", "--persona", "fred"]))
    assert captured["worker_model_id"] == "m-fred"


def test_env_vibeproxy_model_wins_over_persisted(monkeypatch, tmp_path):
    """When VIBEPROXY_MODEL is set in the env (TUI parent or real shell), it must
    win over the done.conf persisted value — preserves existing TUI behavior."""
    from harness import config
    ws = paths.config_dir() / "agents" / "fred"
    ws.mkdir(parents=True)
    config.save_agent("fred", config.AgentConfig(backend="vibeproxy", model="m-fred"))
    # Env value takes precedence
    monkeypatch.setenv("VIBEPROXY_MODEL", "env-wins")

    captured = _spy_model(monkeypatch)
    with pytest.raises(_Stop):
        asyncio.run(acp_main._main(["--model", "vibeproxy", "--cwd", ".", "--persona", "fred"]))
    assert captured["worker_model_id"] == "env-wins"


def test_no_persona_no_env_uses_engine_default(monkeypatch, tmp_path):
    """No persona, no VIBEPROXY_MODEL in env, nothing persisted: must fall back to
    the engine default model from vibeproxy.DEFAULT_MODEL."""
    from harness import vibeproxy
    monkeypatch.delenv("VIBEPROXY_MODEL", raising=False)

    captured = _spy_model(monkeypatch)
    with pytest.raises(_Stop):
        asyncio.run(acp_main._main(["--model", "vibeproxy", "--cwd", "."]))
    assert captured["worker_model_id"] == vibeproxy.DEFAULT_MODEL
