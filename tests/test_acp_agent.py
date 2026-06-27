import sys

sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

import asyncio

import pytest

from harness.acp_agent import HarnessAgent
from harness import config


def _make_agent(backend="vibeproxy", workspace_dir=None, cwd=None):
    """A HarnessAgent with cheap stand-ins; only set_model behavior is exercised."""
    return HarnessAgent(
        model_factory=lambda *a, **k: None,
        agent_cfg={},
        skills_dir=[],
        router=object(),
        worker_model_id="gpt-5.4",
        yolo=False,
        backend=backend,
        workspace_dir=workspace_dir,
        cwd=cwd,
    )


@pytest.fixture
def agent(isolated_config):
    """A bare HarnessAgent (no persona workspace)."""
    return _make_agent(backend="mock")


@pytest.fixture
def agent_with_persona(isolated_config):
    """A HarnessAgent with an 'ana' persona workspace pre-seeded on disk."""
    from harness import paths, config as cfg
    ws = paths.config_dir() / "agents" / "ana"
    ws.mkdir(parents=True)
    cfg.save_agent("ana", cfg.AgentConfig(backend="mock", model=None))
    return _make_agent(backend="mock", cwd="/tmp")


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path


def test_set_model_persists_under_default_when_no_workspace():
    agent = _make_agent(backend="vibeproxy")          # workspace_dir=None -> "default"
    result = asyncio.run(agent.ext_method("harness/set_model", {"model": "claude-opus-4-8"}))
    assert result == {"ok": True, "model": "claude-opus-4-8"}
    assert config.load_default() == config.AgentConfig(
        backend="vibeproxy", model="claude-opus-4-8")


def test_set_model_persists_under_named_persona(tmp_path):
    ws = tmp_path / "agents" / "fred"
    ws.mkdir(parents=True)
    agent = _make_agent(backend="vibeproxy", workspace_dir=ws)
    asyncio.run(agent.ext_method("harness/set_model", {"model": "m-fred"}))
    assert config.load_agent("fred") == config.AgentConfig(backend="vibeproxy", model="m-fred")
    assert config.load_default() is None               # default table untouched


def test_set_model_empty_model_does_not_persist():
    agent = _make_agent()
    asyncio.run(agent.ext_method("harness/set_model", {"model": ""}))
    assert config.load_default() is None  # nothing written for a no-op swap


def test_set_model_reports_failure(monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(config, "save_agent", boom)
    agent = _make_agent()
    result = asyncio.run(agent.ext_method("harness/set_model", {"model": "x"}))
    # swap still applies in-session, but the response reports it did NOT persist
    assert result["model"] == "x"
    assert result["ok"] is False


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
    def boom(*a, **kw):
        raise OSError("disk full")
    monkeypatch.setattr(config, "update_agent", boom)
    agent = _make_agent()
    result = asyncio.run(agent.ext_method("harness/set_yolo", {"active": True, "pin": True}))
    # live toggle still succeeds, but ok=False surfaces the failed persist so the
    # TUI can reconcile rather than show a false "pinned".
    assert agent._yolo is True
    assert result["ok"] is False


def test_set_yolo_pin_pairs_backend_and_model_on_fresh_config():
    # Pinning on a fresh config writes a COMPLETE default (the agent supplies its
    # known backend+model), never backend=""/model="" (which would break launch).
    agent = _make_agent(backend="vibeproxy")   # worker_model_id="gpt-5.4"
    asyncio.run(agent.ext_method("harness/set_yolo", {"active": True, "pin": True}))
    assert config.load_default() == config.AgentConfig(
        backend="vibeproxy", model="gpt-5.4", yolo_pinned=True)


def test_set_yolo_non_bool_active_is_ignored():
    # ACP params are untyped; "false" is truthy. A non-bool must NOT flip the gate.
    agent = _make_agent()
    agent._yolo = False
    asyncio.run(agent.ext_method("harness/set_yolo", {"active": "false"}))
    assert agent._yolo is False                # not coerced on


def test_set_yolo_non_bool_pin_is_ignored():
    agent = _make_agent()
    asyncio.run(agent.ext_method("harness/set_yolo", {"pin": "false"}))
    assert config.yolo_pinned() is False       # "false" did not persist True


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


# --- set_persona tests (Task 3) ---

def test_set_persona_unknown_keeps_active(agent):
    before = agent._active_persona
    resp = asyncio.run(agent.ext_method("harness/set_persona", {"id": "nope-does-not-exist"}))
    assert resp["ok"] is False
    assert agent._active_persona == before          # unchanged on failure


def test_set_persona_invalid_charset(agent):
    resp = asyncio.run(agent.ext_method("harness/set_persona", {"id": "bad.id"}))
    assert resp["ok"] is False


def test_set_persona_valid_switches_and_returns_seat(agent_with_persona):
    resp = asyncio.run(agent_with_persona.ext_method("harness/set_persona", {"id": "ana"}))
    assert resp["ok"] is True and resp["id"] == "ana"
    assert resp["session_id"]
    assert agent_with_persona._active_persona == "ana"


def test_set_model_persists_under_active_persona(agent_with_persona):
    asyncio.run(agent_with_persona.ext_method("harness/set_persona", {"id": "ana"}))
    asyncio.run(agent_with_persona.ext_method("harness/set_model", {"model": "m-ana-new"}))
    assert config.load_agent("ana").model == "m-ana-new"
