from harness import skills
from harness.compress import skill_cache


def _redirect(monkeypatch, tmp_path):
    from harness import paths
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path)


def _make_skill(root, name, body):
    d = root / name; d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\n{body}")


def test_compose_uses_cached_body_when_on(tmp_path, monkeypatch):
    _redirect(monkeypatch, tmp_path / "cfg")
    monkeypatch.setattr(skills, "_compress_skills_on", lambda: True, raising=False)
    root = tmp_path / "skills"
    _make_skill(root, "foo", "VERBOSE original body of the foo skill")
    # parse the source body exactly as compose will, then seed the cache
    _, body = skills._parse_skill_md(root / "foo" / "SKILL.md")
    skill_cache.store_body(body, "terse foo")
    load = skills.compose([root], ["foo"])
    assert "terse foo" in load.block
    assert "VERBOSE original" not in load.block


def test_compose_uses_original_when_off(tmp_path, monkeypatch):
    _redirect(monkeypatch, tmp_path / "cfg")
    monkeypatch.setattr(skills, "_compress_skills_on", lambda: False, raising=False)
    root = tmp_path / "skills"
    _make_skill(root, "foo", "VERBOSE original body")
    _, body = skills._parse_skill_md(root / "foo" / "SKILL.md")
    skill_cache.store_body(body, "terse foo")
    load = skills.compose([root], ["foo"])
    assert "VERBOSE original" in load.block        # off -> original
    assert "terse foo" not in load.block


def test_compose_uses_original_on_cache_miss(tmp_path, monkeypatch):
    _redirect(monkeypatch, tmp_path / "cfg")
    monkeypatch.setattr(skills, "_compress_skills_on", lambda: True, raising=False)
    root = tmp_path / "skills"
    _make_skill(root, "foo", "VERBOSE original body")     # no cache entry stored
    load = skills.compose([root], ["foo"])
    assert "VERBOSE original" in load.block        # miss -> original (degrade)
