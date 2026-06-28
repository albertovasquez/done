from types import SimpleNamespace as NS

from harness.tui.render import render_update, harness_chips, status_style, format_cwd, RenderedItem
from harness.tui.tokens import GLYPH


# --- helpers: build stub update objects that duck-type the acp ones ---
def _msg(text):
    return NS(__class__=type("AgentMessageChunk", (), {}), content=NS(text=text))

# render_update dispatches on type(update).__name__, so name the stub classes.
def _named(name, **attrs):
    cls = type(name, (), {})
    obj = cls()
    for k, v in attrs.items():
        setattr(obj, k, v)
    return obj


def test_render_agent_message_chunk():
    u = _named("AgentMessageChunk", content=NS(text="hello"), field_meta=None)
    item = render_update(u)
    assert item == RenderedItem(kind="message", text="hello")


def test_render_user_message_chunk():
    u = _named("UserMessageChunk", content=NS(text="hi"), field_meta=None)
    assert render_update(u) == RenderedItem(kind="user", text="hi")


def test_render_agent_thought_chunk():
    u = _named("AgentThoughtChunk", content=NS(text="thinking"), field_meta=None)
    assert render_update(u) == RenderedItem(kind="thought", text="thinking")


def test_render_tool_call_start():
    u = _named("ToolCallStart", tool_call_id="tc1", title="$ echo hi", status="pending")
    assert render_update(u) == RenderedItem(kind="tool", id="tc1", title="$ echo hi", status="pending")


def test_render_tool_call_progress_with_body():
    content = [NS(content=NS(text="output here"))]
    u = _named("ToolCallProgress", tool_call_id="tc1", status="completed", content=content)
    assert render_update(u) == RenderedItem(kind="tool_update", id="tc1", status="completed", body="output here")


def test_render_tool_call_progress_no_content():
    u = _named("ToolCallProgress", tool_call_id="tc1", status="failed", content=None)
    assert render_update(u) == RenderedItem(kind="tool_update", id="tc1", status="failed", body="")


def test_render_plan_update():
    entry_a = NS(content="Push + PR", status="in_progress")
    entry_b = NS(content="CI + merge", status="pending")
    u = _named("AgentPlanUpdate", entries=[entry_a, entry_b])
    assert render_update(u) == RenderedItem(
        kind="plan",
        entries=(("Push + PR", "in_progress"), ("CI + merge", "pending")),
    )


def test_render_plan_update_empty():
    u = _named("AgentPlanUpdate", entries=[])
    assert render_update(u) == RenderedItem(kind="plan", entries=())


def test_render_unknown_returns_none():
    assert render_update(_named("SomeFutureUpdate", foo=1)) is None


def test_status_style_all_str_forms():
    assert status_style("pending") == "yellow"
    assert status_style("in_progress") == "blue"
    assert status_style("completed") == "green"
    assert status_style("failed") == "red"
    assert status_style("something-else") == "white"


def test_status_style_stringified_enum_forms():
    # the smoke tests showed status can arrive as "ToolCallStatus.failed"
    assert status_style("ToolCallStatus.failed") == "red"
    assert status_style("ToolCallStatus.completed") == "green"


def test_harness_chips_task_classified():
    fm = {"harness": {"task_classified": {"task_type": "code_fix", "skills": ["debugging"], "confidence": 0.9}}}
    assert harness_chips(fm) == ["classified: code_fix · skills: debugging · conf: 0.90"]


def test_harness_chips_task_classified_no_skills():
    fm = {"harness": {"task_classified": {"task_type": "chat_question", "skills": [], "confidence": 0.5}}}
    assert harness_chips(fm) == ["classified: chat_question · skills: — · conf: 0.50"]


def test_harness_chips_skill_load():
    fm = {"harness": {"skill_load": {"injected": ["a", "b"], "skipped": ["c"]}}}
    assert harness_chips(fm) == ["skills: 2 loaded, 1 skipped"]


def test_harness_chips_none_and_empty():
    assert harness_chips(None) == []
    assert harness_chips({}) == []
    assert harness_chips({"harness": {}}) == []


def test_harness_chips_malformed_never_raises():
    # missing nested keys must yield [], not raise
    assert harness_chips({"harness": {"task_classified": {}}}) == ["classified: ? · skills: — · conf: 0.00"]
    assert harness_chips({"harness": {"skill_load": {}}}) == ["skills: 0 loaded, 0 skipped"]
    assert harness_chips({"harness": "not-a-dict"}) == []


# --- format_cwd: the two-tone, home-relative status-bar path ---
G = GLYPH["path"]


def test_format_cwd_collapses_home_and_emphasizes_basename():
    out = format_cwd("/Users/alberto/Work/Quiubo/harness", home="/Users/alberto")
    # parent (incl. ~) is dim, current dir is bright, leading glyph present
    assert out == f"[$path-dim]{G} ~/Work/Quiubo/[/][$path]harness[/]"


def test_format_cwd_home_itself_is_just_tilde():
    out = format_cwd("/Users/alberto", home="/Users/alberto")
    assert out == f"[$path-dim]{G} [/][$path]~[/]"


def test_format_cwd_non_home_absolute_path_keeps_leading_slash():
    out = format_cwd("/etc/nginx", home="/Users/alberto")
    assert out == f"[$path-dim]{G} /etc/[/][$path]nginx[/]"


def test_format_cwd_root_edge_case():
    out = format_cwd("/", home="/Users/alberto")
    assert out == f"[$path-dim]{G} [/][$path]/[/]"


def test_format_cwd_empty_is_safe():
    out = format_cwd("", home="/Users/alberto")
    assert out == f"[$path-dim]{G} [/][$path]/[/]"


def test_format_cwd_left_truncates_long_path_never_the_basename():
    long = "/Users/alberto/Work/Quiubo/very/deeply/nested/project-dir"
    out = format_cwd(long, home="/Users/alberto", max_width=24)
    # the bright basename always survives; the dim prefix collapses behind …/
    assert "[$path]project-dir[/]" in out
    assert "…/" in out
    # markup chars don't count toward width; the visible text fits the budget
    visible = (out.replace("[$path-dim]", "").replace("[$path]", "")
                  .replace("[/]", ""))
    assert len(visible) <= 24


def test_format_cwd_no_home_leaves_absolute_path():
    out = format_cwd("/srv/app", home=None)
    assert out == f"[$path-dim]{G} /srv/[/][$path]app[/]"
