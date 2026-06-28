import pytest
from harness import persona_select, paths
from harness.persona_select import slugify_persona_name, _VALID_ID


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


# --- slugify_persona_name ---

@pytest.mark.parametrize("raw,expected", [
    ("My Persona", "my-persona"),
    ("Alberto", "alberto"),
    ("Fred.Smith", "fred-smith"),
    ("  spaced  ", "spaced"),
    ("a---b__c.d", "a-b-c-d"),
    ("my-persona", "my-persona"),     # already valid → passthrough
    ("ABC123", "abc123"),
    ("--lead", "lead"),
    ("trail--", "trail"),
    ("MiXeD CaSe", "mixed-case"),
    ("!!!", ""),
    ("😀", ""),
    ("", ""),
    ("___", ""),
    ("café", "caf"),                  # accented dropped (lossy, by design)
    ("İstanbul", "i-stanbul"),        # unicode combining mark → separator
])
def test_slugify_persona_name(raw, expected):
    assert slugify_persona_name(raw) == expected


@pytest.mark.parametrize("raw", [
    "My Persona", "Fred.Smith", "café", "İstanbul", "a.b.c", "  X Y  ",
    "----", "a" * 200, "Ω mega", "tab\tname", "new\nline",
])
def test_slugify_result_always_valid_or_empty(raw):
    """The invariant: a non-empty slug ALWAYS satisfies _VALID_ID."""
    s = slugify_persona_name(raw)
    assert s == "" or _VALID_ID.match(s), f"{raw!r} -> {s!r} violates _VALID_ID"
