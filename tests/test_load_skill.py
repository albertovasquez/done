import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

from types import SimpleNamespace

from harness.tools.load_skill import LoadSkillTool
from harness.tools.registry import build_registry


def _skill(tmp_path, name, body="do the thing"):
    d = tmp_path / name
    d.mkdir()
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\n{body}\n",
                               encoding="utf-8")


def _env(tmp_path):
    return SimpleNamespace(config=SimpleNamespace(cwd=str(tmp_path)))


def test_load_skill_returns_body(tmp_path):
    _skill(tmp_path, "alpha", "ALPHA BODY")
    out = LoadSkillTool([tmp_path]).execute({"skill_name": "alpha"}, _env(tmp_path))
    assert out["returncode"] == 0 and "ALPHA BODY" in out["output"]


def test_load_skill_unknown_lists_available(tmp_path):
    _skill(tmp_path, "alpha")
    out = LoadSkillTool([tmp_path]).execute({"skill_name": "ghost"}, _env(tmp_path))
    assert out["returncode"] == 1 and "alpha" in out["output"]


def test_load_skill_duplicate_is_short_circuited(tmp_path):
    _skill(tmp_path, "alpha", "ALPHA BODY")
    env = _env(tmp_path)
    env._loaded_skills = set()
    tool = LoadSkillTool([tmp_path])
    tool.execute({"skill_name": "alpha"}, env)
    out2 = tool.execute({"skill_name": "alpha"}, env)
    assert "already loaded" in out2["output"].lower() and "ALPHA BODY" not in out2["output"]


def test_loaded_set_is_reset_between_turns(tmp_path):
    _skill(tmp_path, "alpha", "ALPHA BODY")
    env = _env(tmp_path)
    tool = LoadSkillTool([tmp_path])
    env._loaded_skills = set()
    tool.execute({"skill_name": "alpha"}, env)
    # a new turn resets the env slot -> the same skill can be re-pulled
    env._loaded_skills = set()
    out = tool.execute({"skill_name": "alpha"}, env)
    assert "ALPHA BODY" in out["output"]


def test_registry_no_op_without_roots():
    assert [t.name for t in build_registry()] == ["bash", "read", "write", "edit"]


def test_registry_appends_load_skill_with_roots(tmp_path):
    assert "load_skill" in [t.name for t in build_registry(skill_roots=[tmp_path])]
