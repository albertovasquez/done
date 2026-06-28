from harness.skills import SkillMeta
from harness.flows import scope_catalog, render_map

A = SkillMeta("a", "global")                       # no flow -> always in
B = SkillMeta("b", "seo skill", flows=("seo",))
C = SkillMeta("c", "mktg skill", flows=("marketing",))
D = SkillMeta("ask-done", "router", model_invocable=False, flows=("seo",))


def test_scope_keeps_global_and_enabled_only():
    out = scope_catalog([A, B, C], ["seo"])
    assert {m.name for m in out} == {"a", "b"}


def test_scope_empty_flows_keeps_only_global():
    assert {m.name for m in scope_catalog([A, B, C], [])} == {"a"}


def test_scope_multi_flow_membership():
    multi = SkillMeta("m", "both", flows=("seo", "marketing"))
    assert {x.name for x in scope_catalog([multi], ["marketing"])} == {"m"}


def test_render_map_groups_and_marks_user_only():
    out = render_map([A, B, D], ["seo"])
    assert "seo" in out and "**b**" in out and "**a**" in out
    assert "/ask-done" in out          # disable-model-invocation marked with /name


def test_bundled_spine_is_global_and_present():
    # The maturity spine ships as GLOBAL skills (no flow tag) so it is always
    # available regardless of which flow a persona enables.
    from harness import paths, skills
    cat = {m.name: m for m in skills.load_catalog(paths.skills_dirs())}
    for n in ["clarify-before-acting", "planning-before-coding",
              "systematic-debugging", "test-driven-development", "ask-done"]:
        assert n in cat, n
        assert cat[n].flows == (), f"{n} should be global (no flow tag)"
    # global skills survive scoping to an unrelated flow
    scoped = scope_catalog(list(cat.values()), ["marketing"])
    assert "clarify-before-acting" in {m.name for m in scoped}
