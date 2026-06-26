import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

from pathlib import Path
from harness.skills import SkillLoad, load_catalog, compose


def _write_skill(root: Path, name: str, description: str, body: str, *, dirname=None):
    d = root / (dirname or name)
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}", encoding="utf-8")
    return d


def test_load_catalog_parses_frontmatter_sorted_and_skips_bad(tmp_path):
    _write_skill(tmp_path, "python-testing", "Write pytest tests", "# body")
    _write_skill(tmp_path, "git-pr-flow", "Make PRs", "# body2")
    (tmp_path / "no-skill-md").mkdir()                       # dir without SKILL.md -> skipped
    bad = tmp_path / "broken"; bad.mkdir()
    (bad / "SKILL.md").write_text("not: [valid", encoding="utf-8")  # malformed yaml -> skipped
    catalog = load_catalog([tmp_path])
    assert catalog == [("git-pr-flow", "Make PRs"), ("python-testing", "Write pytest tests")]


def test_load_catalog_absent_dir_is_empty(tmp_path):
    assert load_catalog([tmp_path / "does-not-exist"]) == []


def test_load_catalog_skips_name_mismatch_and_missing_keys(tmp_path):
    _write_skill(tmp_path, "real-name", "desc", "# b", dirname="wrong-dir")   # name != dirname
    miss = tmp_path / "no-desc"; miss.mkdir()
    (miss / "SKILL.md").write_text("---\nname: no-desc\n---\nbody", encoding="utf-8")  # no description
    assert load_catalog([tmp_path]) == []


def test_compose_injects_bodies_in_selection_order(tmp_path):
    _write_skill(tmp_path, "a", "da", "Alpha body")
    _write_skill(tmp_path, "b", "db", "Bravo body")
    load = compose([tmp_path], ["b", "a"])
    assert load.injected == ["b", "a"]
    assert load.skipped == []
    assert load.block.index("Bravo body") < load.block.index("Alpha body")
    assert "## b" in load.block and "## a" in load.block


def test_compose_skips_missing_but_injects_good(tmp_path):
    _write_skill(tmp_path, "good", "dg", "Good body")
    load = compose([tmp_path], ["good", "ghost"])
    assert load.injected == ["good"]
    assert load.skipped == [("ghost", "no valid SKILL.md in any root")]
    assert "Good body" in load.block


def test_compose_empty_selection_is_empty(tmp_path):
    assert compose([tmp_path], []) == SkillLoad()


def test_compose_body_with_jinja_survives_verbatim(tmp_path):
    _write_skill(tmp_path, "tpl", "d", "Use {{ x }} and {% if y %} here")
    load = compose([tmp_path], ["tpl"])
    assert "{{ x }}" in load.block and "{% if y %}" in load.block


def test_compose_non_utf8_is_skipped_not_raised(tmp_path):
    d = tmp_path / "binskill"; d.mkdir()
    (d / "SKILL.md").write_bytes(b"\xff\xfe\x00bad")
    load = compose([tmp_path], ["binskill"])
    assert load.injected == []
    assert load.skipped and load.skipped[0][0] == "binskill"


def test_load_catalog_merges_roots_user_overrides_bundled(tmp_path):
    bundled = tmp_path / "bundled"; (bundled / "a").mkdir(parents=True)
    (bundled / "a" / "SKILL.md").write_text("---\nname: a\ndescription: bundled A\n---\nbody\n")
    user = tmp_path / "user"; (user / "a").mkdir(parents=True)
    (user / "a" / "SKILL.md").write_text("---\nname: a\ndescription: user A\n---\nbody\n")
    cat = dict(load_catalog([bundled, user]))   # later root wins
    assert cat["a"] == "user A"


def test_invalid_user_skill_does_not_shadow_bundled(tmp_path):
    bundled = tmp_path / "bundled"; (bundled / "a").mkdir(parents=True)
    (bundled / "a" / "SKILL.md").write_text("---\nname: a\ndescription: bundled A\n---\nbody\n")
    user = tmp_path / "user"; (user / "a").mkdir(parents=True)
    (user / "a" / "SKILL.md").write_text("not valid frontmatter")
    cat = dict(load_catalog([bundled, user]))
    assert cat["a"] == "bundled A"     # invalid user skill ignored, bundled stays
