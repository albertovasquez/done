from pathlib import Path
from harness.skills import (SkillLoad, SkillMeta, load_catalog, compose,
                            _meta_from_frontmatter)


def _write_skill(root: Path, name: str, description: str, body: str, *, dirname=None):
    d = root / (dirname or name)
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}", encoding="utf-8")
    return d


def test_load_catalog_parses_frontmatter_sorted_and_skips_bad(tmp_path, caplog):
    _write_skill(tmp_path, "python-testing", "Write pytest tests", "# body")
    _write_skill(tmp_path, "git-pr-flow", "Make PRs", "# body2")
    (tmp_path / "no-skill-md").mkdir()                       # dir without SKILL.md -> skipped
    bad = tmp_path / "broken"; bad.mkdir()
    (bad / "SKILL.md").write_text("not: [valid", encoding="utf-8")  # malformed yaml -> skipped
    with caplog.at_level("WARNING", logger="harness.skills"):
        catalog = load_catalog([tmp_path])
    assert [(m.name, m.description) for m in catalog] == [
        ("git-pr-flow", "Make PRs"), ("python-testing", "Write pytest tests")]
    # the malformed skill must be NAMED in a warning — otherwise it just vanishes
    # from the catalog with no clue why it's unselectable.
    assert any("broken" in r.message for r in caplog.records), \
        f"malformed skill must be logged by name; got {[r.message for r in caplog.records]}"


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
    cat = {m.name: m.description for m in load_catalog([bundled, user])}   # later root wins
    assert cat["a"] == "user A"


def test_invalid_user_skill_does_not_shadow_bundled(tmp_path):
    bundled = tmp_path / "bundled"; (bundled / "a").mkdir(parents=True)
    (bundled / "a" / "SKILL.md").write_text("---\nname: a\ndescription: bundled A\n---\nbody\n")
    user = tmp_path / "user"; (user / "a").mkdir(parents=True)
    (user / "a" / "SKILL.md").write_text("not valid frontmatter")
    cat = {m.name: m.description for m in load_catalog([bundled, user])}
    assert cat["a"] == "bundled A"     # invalid user skill ignored, bundled stays


# --- Layer A: SkillMeta + invocation-model frontmatter -----------------------

def test_meta_defaults_when_only_name_desc():
    m = _meta_from_frontmatter({"name": "x", "description": "d"}, "x")
    assert m == SkillMeta(name="x", description="d", model_invocable=True,
                          user_invocable=True, flows=())


def test_meta_disable_model_invocation_and_user_flag():
    m = _meta_from_frontmatter(
        {"name": "x", "description": "d",
         "disable-model-invocation": True, "user-invocable": False}, "x")
    assert m.model_invocable is False
    assert m.user_invocable is False


def test_meta_flow_scalar_and_list_and_garbage():
    # a string flow == a single flow; a list == those flows (strings only)
    assert _meta_from_frontmatter({"name": "x", "description": "d", "flow": "seo"}, "x").flows == ("seo",)
    assert _meta_from_frontmatter({"name": "x", "description": "d", "flows": ["a", "b"]}, "x").flows == ("a", "b")
    assert _meta_from_frontmatter({"name": "x", "description": "d", "flows": "one"}, "x").flows == ("one",)
    # a non-str/non-list flow (e.g. a number) degrades to no flow, never raises;
    # and only literal True disables model invocation (a truthy string does NOT).
    g = _meta_from_frontmatter({"name": "x", "description": "d",
                                "disable-model-invocation": "yes", "flows": 42}, "x")
    assert g.model_invocable is True and g.flows == ()
    # a list with non-string members keeps only the strings
    assert _meta_from_frontmatter({"name": "x", "description": "d", "flows": ["a", 3, "b"]}, "x").flows == ("a", "b")


def test_load_catalog_returns_skillmeta(tmp_path):
    d = tmp_path / "alpha"; d.mkdir()
    (d / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: A\ndisable-model-invocation: true\nflow: seo\n---\nbody\n")
    cat = load_catalog([tmp_path])
    assert cat == [SkillMeta(name="alpha", description="A",
                             model_invocable=False, user_invocable=True, flows=("seo",))]


# --- Layer B: lazy skill menu ------------------------------------------------

def test_compose_menu_lists_names_not_bodies():
    metas = [SkillMeta("a", "does A"), SkillMeta("b", "does B")]
    from harness.skills import compose_menu
    out = compose_menu(metas)
    assert "does A" in out and "**a**" in out and "load_skill" in out
    assert "# Skills" in out


def test_compose_menu_empty_is_blank():
    from harness.skills import compose_menu
    assert compose_menu([]) == ""


# --- #87: surface skipped/malformed skills -----------------------------------

def test_load_catalog_with_skips_reports_reasons(tmp_path, caplog):
    _write_skill(tmp_path, "good", "fine", "# body")
    bad = tmp_path / "broken"; bad.mkdir()
    (bad / "SKILL.md").write_text("not: [valid", encoding="utf-8")     # malformed yaml
    nomatch = tmp_path / "wrong-dir"; nomatch.mkdir()
    (nomatch / "SKILL.md").write_text("---\nname: other\ndescription: d\n---\nb", encoding="utf-8")
    from harness.skills import load_catalog_with_skips, CatalogLoad
    load = load_catalog_with_skips([tmp_path])
    assert isinstance(load, CatalogLoad)
    assert [m.name for m in load.skills] == ["good"]
    skipped_names = {n for n, _ in load.skipped}
    assert "broken" in skipped_names and "wrong-dir" in skipped_names
    # each skip carries a human reason
    assert all(reason for _, reason in load.skipped)


def test_load_catalog_unchanged_signature(tmp_path):
    # the original list-returning load_catalog is preserved (no caller breakage)
    _write_skill(tmp_path, "good", "fine", "# body")
    cat = load_catalog([tmp_path])
    assert [m.name for m in cat] == ["good"]


def test_format_catalog_surfaces_skipped(tmp_path):
    from harness.chat_handler import _format_catalog
    from harness.skills import SkillMeta
    out = _format_catalog([SkillMeta("good", "fine")],
                          skipped=[("broken", "frontmatter is not a mapping")])
    assert "good" in out
    assert "broken" in out and "frontmatter is not a mapping" in out
    assert "skipped" in out.lower()


def test_format_catalog_no_skips_unchanged(tmp_path):
    from harness.chat_handler import _format_catalog
    from harness.skills import SkillMeta
    out = _format_catalog([SkillMeta("good", "fine")])
    assert "skipped" not in out.lower() and "good" in out
