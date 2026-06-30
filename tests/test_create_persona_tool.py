"""Unit tests for CreatePersonaTool — the agent-facing create-only persona tool.

Uses isolated_config (XDG_CONFIG_HOME → tmp_path) so paths.config_dir() resolves
under tmp_path and create_persona writes there, never the real ~/.config.
"""
import pytest

from harness import paths, persona
from harness.persona_select import slugify_persona_name
from harness.tools.create_persona import CreatePersonaTool
from harness.tools.registry import build_registry


@pytest.fixture
def isolated_config(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path


class _Env:
    """Minimal stand-in for the agent env; create_persona ignores it, but the
    Tool protocol passes one. _active_persona lets us assert no seat switch."""
    def __init__(self, active="bob"):
        self._active_persona = active


def test_happy_path_creates_trio_and_returns_ok(isolated_config):
    res = CreatePersonaTool().execute({"name": "Robbie"}, _Env())
    assert res["returncode"] == 0
    assert res["exception_info"] is None
    ws = paths.config_dir() / "agents" / "robbie"
    assert ws.is_dir()
    for name in persona.PERSONA_FILES:
        assert (ws / name).is_file()
    # display name persisted for the rail label
    assert 'name = "Robbie"' in (ws / "persona.toml").read_text()
    # message carries id, drawer hint, and the "starts blank" note (option B)
    out = res["output"]
    assert "robbie" in out
    assert "drawer" in out.lower()
    assert "blank" in out.lower()


def test_slugifies_free_text_name(isolated_config):
    res = CreatePersonaTool().execute({"name": "Robbie The Bold!"}, _Env())
    assert res["returncode"] == 0
    pid = slugify_persona_name("Robbie The Bold!")   # assert against the real fn
    assert (paths.config_dir() / "agents" / pid).is_dir()
    assert f"id: {pid}" in res["output"]


def test_duplicate_name_fails_without_clobber(isolated_config):
    tool = CreatePersonaTool()
    assert tool.execute({"name": "Robbie"}, _Env())["returncode"] == 0
    res = tool.execute({"name": "Robbie"}, _Env())
    assert res["returncode"] == 1
    assert "already exists" in res["output"]


@pytest.mark.parametrize("args", [{}, {"name": ""}, {"name": "   "}])
def test_missing_or_blank_name_is_error(isolated_config, args):
    res = CreatePersonaTool().execute(args, _Env())
    assert res["returncode"] == 1
    assert "name required" in res["output"]
    assert not (paths.config_dir() / "agents").exists()


@pytest.mark.parametrize("bad", ["default", "!!!", "🙂"])
def test_name_that_slugifies_invalid_is_error(isolated_config, bad):
    res = CreatePersonaTool().execute({"name": bad}, _Env())
    assert res["returncode"] == 1
    # friendly message, never a traceback escaping into the dispatcher
    assert res["exception_info"] is None
    assert not (paths.config_dir() / "agents").exists()


def test_create_does_not_switch_active_persona(isolated_config):
    env = _Env(active="bob")
    CreatePersonaTool().execute({"name": "Robbie"}, env)
    assert env._active_persona == "bob"   # create-only: seat unchanged


def test_registered_in_default_registry(isolated_config):
    tool = next((t for t in build_registry() if t.name == "create_persona"), None)
    assert tool is not None
    assert tool.schema["function"]["name"] == "create_persona"
    assert callable(tool.display_label) and callable(tool.execute)
