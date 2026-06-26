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
