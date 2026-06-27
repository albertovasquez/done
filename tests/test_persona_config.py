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
