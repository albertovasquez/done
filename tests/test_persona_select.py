import pytest
from harness import persona_select, paths


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path


def test_none_and_default_resolve_to_default_dir():
    assert persona_select.resolve_workspace(None) == paths.default_workspace_dir()
    assert persona_select.resolve_workspace("default") == paths.default_workspace_dir()

def test_named_persona_resolves_when_dir_exists():
    target = paths.config_dir() / "agents" / "fred"
    target.mkdir(parents=True)
    assert persona_select.resolve_workspace("fred") == target

def test_unknown_persona_raises():
    with pytest.raises(persona_select.UnknownPersona) as exc:
        persona_select.resolve_workspace("nope")
    assert "nope" in str(exc.value)

def test_list_personas_enumerates_existing_only_and_is_read_only(tmp_path):
    agents = paths.config_dir() / "agents"
    (agents / "default").mkdir(parents=True)
    (agents / "fred").mkdir(parents=True)
    (agents / "afile").parent.mkdir(parents=True, exist_ok=True)
    (agents / "afile").write_text("x")          # a non-dir must be ignored
    result = persona_select.list_personas()
    assert result == ["default", "fred"]
    # read-only: calling it created nothing new
    assert sorted(p.name for p in agents.iterdir()) == ["afile", "default", "fred"]
