import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

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
