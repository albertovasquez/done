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


# --- F1a: unsafe persona id validation ---

def test_dot_in_id_raises_invalid_even_when_dir_exists():
    """fred.smith → TOML nested-table corruption; reject at the gate."""
    target = paths.config_dir() / "agents" / "fred.smith"
    target.mkdir(parents=True)
    with pytest.raises(persona_select.InvalidPersonaId) as exc:
        persona_select.resolve_workspace("fred.smith")
    assert "fred.smith" in str(exc.value)


def test_space_in_id_raises_invalid():
    with pytest.raises(persona_select.InvalidPersonaId):
        persona_select.resolve_workspace("bad id")


def test_uppercase_in_id_raises_invalid():
    with pytest.raises(persona_select.InvalidPersonaId):
        persona_select.resolve_workspace("FRED")


def test_valid_id_resolves_normally():
    target = paths.config_dir() / "agents" / "fred_2-x"
    target.mkdir(parents=True)
    assert persona_select.resolve_workspace("fred_2-x") == target


def test_none_and_default_still_resolve_to_default():
    assert persona_select.resolve_workspace(None) == paths.default_workspace_dir()
    assert persona_select.resolve_workspace("default") == paths.default_workspace_dir()


def test_invalid_persona_id_is_not_subclass_of_unknown_persona():
    assert not issubclass(persona_select.InvalidPersonaId, persona_select.UnknownPersona)
    assert issubclass(persona_select.InvalidPersonaId, Exception)
