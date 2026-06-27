from pathlib import Path
from harness import persona_config


def test_missing_workspace_returns_empty(tmp_path):
    assert persona_config.read_skills(tmp_path / "nope") == []

def test_none_workspace_returns_empty():
    assert persona_config.read_skills(None) == []

def test_reads_skills_list(tmp_path):
    (tmp_path / "persona.toml").write_text('skills = ["/a/b", "~/c"]\n')
    got = persona_config.read_skills(tmp_path)
    assert got == [Path("/a/b"), Path("~/c").expanduser()]

def test_corrupt_toml_returns_empty(tmp_path):
    (tmp_path / "persona.toml").write_text("skills = [unclosed\n")
    assert persona_config.read_skills(tmp_path) == []

def test_no_skills_key_returns_empty(tmp_path):
    (tmp_path / "persona.toml").write_text('other = "x"\n')
    assert persona_config.read_skills(tmp_path) == []


def test_read_name_returns_name(tmp_path):
    (tmp_path / "persona.toml").write_text('name = "Fred R."\n')
    assert persona_config.read_name(tmp_path) == "Fred R."


def test_read_name_none_when_missing_workspace():
    assert persona_config.read_name(None) is None


def test_read_name_none_when_no_file(tmp_path):
    assert persona_config.read_name(tmp_path / "nope") is None


def test_read_name_none_when_no_key(tmp_path):
    (tmp_path / "persona.toml").write_text('skills = ["/a"]\n')
    assert persona_config.read_name(tmp_path) is None


def test_read_name_none_when_corrupt(tmp_path):
    (tmp_path / "persona.toml").write_text("name = [unclosed\n")
    assert persona_config.read_name(tmp_path) is None


def test_read_name_none_when_non_str(tmp_path):
    (tmp_path / "persona.toml").write_text("name = 42\n")
    assert persona_config.read_name(tmp_path) is None


def test_read_name_none_when_empty_string(tmp_path):
    """Empty string name is falsy — read_name returns None, not ""."""
    (tmp_path / "persona.toml").write_text('name = ""\n')
    assert persona_config.read_name(tmp_path) is None
