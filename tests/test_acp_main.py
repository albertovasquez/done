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
