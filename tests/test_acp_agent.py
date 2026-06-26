import sys

sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

import asyncio

import pytest

from harness.acp_agent import HarnessAgent
from harness import config


def _make_agent(backend="vibeproxy"):
    """A HarnessAgent with cheap stand-ins; only set_model behavior is exercised."""
    return HarnessAgent(
        model_factory=lambda *a, **k: None,
        agent_cfg={},
        skills_dir=[],
        router=object(),
        worker_model_id="gpt-5.4",
        yolo=False,
        backend=backend,
    )


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path


def test_set_model_persists_backend_and_model():
    agent = _make_agent(backend="vibeproxy")
    result = asyncio.run(agent.ext_method("harness/set_model", {"model": "claude-opus-4-8"}))
    assert result == {"ok": True, "model": "claude-opus-4-8"}
    assert config.load_default() == config.AgentConfig(
        backend="vibeproxy", model="claude-opus-4-8")


def test_set_model_empty_model_does_not_persist():
    agent = _make_agent()
    asyncio.run(agent.ext_method("harness/set_model", {"model": ""}))
    assert config.load_default() is None  # nothing written for a no-op swap


def test_set_model_survives_save_failure(monkeypatch):
    def boom(_cfg):
        raise OSError("disk full")
    monkeypatch.setattr(config, "save_default", boom)
    agent = _make_agent()
    result = asyncio.run(agent.ext_method("harness/set_model", {"model": "x"}))
    assert result == {"ok": True, "model": "x"}  # swap still succeeds


def test_set_yolo_active_true_sets_gate_no_persist():
    agent = _make_agent()
    agent._yolo = False
    result = asyncio.run(agent.ext_method("harness/set_yolo", {"active": True}))
    assert agent._yolo is True
    assert result["ok"] is True and result["active"] is True
    assert config.load_default() is None      # active alone never persists


def test_set_yolo_active_false_turns_gate_off():
    agent = _make_agent()
    agent._yolo = True
    asyncio.run(agent.ext_method("harness/set_yolo", {"active": False}))
    assert agent._yolo is False


def test_set_yolo_pin_true_persists():
    agent = _make_agent()
    asyncio.run(agent.ext_method("harness/set_yolo", {"active": True, "pin": True}))
    assert config.yolo_pinned() is True


def test_set_yolo_pin_false_unpins():
    config.update_default(backend="vibeproxy", model="gpt-5.4", yolo_pinned=True)
    agent = _make_agent()
    asyncio.run(agent.ext_method("harness/set_yolo", {"pin": False}))
    assert config.yolo_pinned() is False


def test_set_yolo_omitted_pin_does_not_touch_persistence():
    config.update_default(backend="vibeproxy", model="gpt-5.4", yolo_pinned=True)
    agent = _make_agent()
    asyncio.run(agent.ext_method("harness/set_yolo", {"active": False}))
    assert config.yolo_pinned() is True       # pin untouched by a live-only toggle


def test_set_yolo_survives_persist_failure(monkeypatch):
    def boom(**kw):
        raise OSError("disk full")
    monkeypatch.setattr(config, "update_default", boom)
    agent = _make_agent()
    result = asyncio.run(agent.ext_method("harness/set_yolo", {"active": True, "pin": True}))
    assert result["ok"] is True and agent._yolo is True   # live toggle still succeeds


def test_acp_main_wires_default_workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("HARNESS_ROUTER_STUB", "1")
    from harness import acp_agent, paths

    captured = {}
    real_init = acp_agent.HarnessAgent.__init__
    def spy_init(self, **kw):
        captured.update(kw)
        real_init(self, **kw)
    monkeypatch.setattr(acp_agent.HarnessAgent, "__init__", spy_init)

    # run _main far enough to construct the agent, then stop at run_agent
    import acp
    monkeypatch.setattr(acp, "run_agent", lambda agent: asyncio.sleep(0))
    from harness import acp_main
    asyncio.run(acp_main._main(["--model", "mock"]))
    assert captured["workspace_dir"] == paths.default_workspace_dir()


def test_acp_main_seeds_default_workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("HARNESS_ROUTER_STUB", "1")
    import asyncio
    import acp
    from harness import persona

    called = {"n": 0}
    real = persona.seed_default_workspace
    def spy():
        called["n"] += 1
        real()
    monkeypatch.setattr(persona, "seed_default_workspace", spy)
    monkeypatch.setattr(acp, "run_agent", lambda agent: asyncio.sleep(0))

    from harness import acp_main
    asyncio.run(acp_main._main(["--model", "mock"]))
    assert called["n"] == 1
    # and it actually seeded
    assert (tmp_path / "harness" / "agents" / "default" / "SOUL.md").is_file()
