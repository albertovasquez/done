import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

from pathlib import Path
from harness.persona import PersonaLoad, compose_persona, MAX_FILE_CHARS


def _write(ws: Path, name: str, body: str):
    ws.mkdir(parents=True, exist_ok=True)
    (ws / name).write_text(body, encoding="utf-8")


def test_all_three_present_in_order(tmp_path):
    _write(tmp_path, "SOUL.md", "I am terse.")
    _write(tmp_path, "IDENTITY.md", "Name: Ada")
    _write(tmp_path, "USER.md", "User is Alberto.")
    load = compose_persona(tmp_path)
    assert load.injected == ["SOUL.md", "IDENTITY.md", "USER.md"]
    assert load.skipped == []
    # ordering: SOUL before IDENTITY before USER
    assert load.block.index("I am terse.") < load.block.index("Name: Ada") < load.block.index("User is Alberto.")
    assert "# Persona" in load.block


def test_partial_injects_present_only(tmp_path):
    _write(tmp_path, "SOUL.md", "Only soul.")
    load = compose_persona(tmp_path)
    assert load.injected == ["SOUL.md"]
    assert "Only soul." in load.block
    assert "IDENTITY.md" not in load.injected and "USER.md" not in load.injected


def test_absent_dir_is_empty_noraise(tmp_path):
    load = compose_persona(tmp_path / "does-not-exist")
    assert load == PersonaLoad()
    assert load.block == "" and load.injected == []


def test_empty_workspace_is_empty(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    load = compose_persona(tmp_path)
    assert load.block == "" and load.injected == []


def test_blank_file_skipped(tmp_path):
    _write(tmp_path, "SOUL.md", "")
    load = compose_persona(tmp_path)
    assert ("SOUL.md", "blank") in load.skipped
    assert load.injected == []
    assert load.block == ""


def test_whitespace_only_file_is_blank(tmp_path):
    _write(tmp_path, "SOUL.md", "   \n\t\n  ")
    load = compose_persona(tmp_path)
    assert ("SOUL.md", "blank") in load.skipped
    assert load.block == ""          # guards the byte-identical no-op


def test_oversized_file_trimmed(tmp_path):
    big = "x" * (MAX_FILE_CHARS + 500)
    _write(tmp_path, "SOUL.md", big)
    load = compose_persona(tmp_path)
    assert load.injected == ["SOUL.md"]
    assert "[truncated]" in load.block
    # body content capped at exactly MAX_FILE_CHARS (marker excluded, no under-trim)
    assert load.block.count("x") == MAX_FILE_CHARS


def test_non_utf8_file_skipped_not_raised(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "SOUL.md").write_bytes(b"\xff\xfe\x00bad")
    load = compose_persona(tmp_path)
    assert load.injected == []
    assert load.skipped and load.skipped[0][0] == "SOUL.md"


from harness.persona import TurnContext, compose_context, resolve_persona
from harness import skills as _skills


def _write_skill(root: Path, name: str, body: str):
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\n{body}", encoding="utf-8")


def test_compose_context_bundles_given_persona_and_skills(tmp_path):
    skroot = tmp_path / "sk"
    _write_skill(skroot, "tdd", "TDD body")
    ctx = compose_context("PERSONA TEXT", [skroot], ["tdd"])
    assert ctx.persona_block == "PERSONA TEXT"
    assert "TDD body" in ctx.skill_block
    assert ctx.skills.injected == ["tdd"]


def test_compose_context_empty_persona_still_resolves_skills(tmp_path):
    skroot = tmp_path / "sk"
    _write_skill(skroot, "tdd", "TDD body")
    ctx = compose_context("", [skroot], ["tdd"])
    assert ctx.persona_block == ""
    assert "TDD body" in ctx.skill_block


def test_compose_context_all_empty(tmp_path):
    ctx = compose_context("", [tmp_path], [])
    assert ctx == TurnContext()


def test_resolve_persona_reads_workspace(tmp_path):
    (tmp_path / "SOUL.md").write_text("Be terse.", encoding="utf-8")
    load = resolve_persona(tmp_path)
    assert "Be terse." in load.block
    assert load.injected == ["SOUL.md"]


def test_resolve_persona_none_is_empty():
    assert resolve_persona(None) == PersonaLoad()


def test_resolve_persona_absent_dir_is_empty(tmp_path):
    assert resolve_persona(tmp_path / "nope") == PersonaLoad()


def test_html_comment_only_file_is_blank(tmp_path):
    # a template file (only an HTML comment) must be treated as blank -> not injected
    (tmp_path / "SOUL.md").write_text(
        "<!-- SOUL.md — describe the agent's tone here. -->\n", encoding="utf-8")
    load = compose_persona(tmp_path)
    assert ("SOUL.md", "blank") in load.skipped
    assert load.injected == []
    assert load.block == ""


def test_comment_plus_real_line_injects_whole_file(tmp_path):
    # once the user adds real content, the file injects (comment included is fine)
    (tmp_path / "SOUL.md").write_text(
        "<!-- hint -->\nYou are terse.", encoding="utf-8")
    load = compose_persona(tmp_path)
    assert load.injected == ["SOUL.md"]
    assert "You are terse." in load.block


def test_markdown_heading_is_not_a_comment(tmp_path):
    # '#' is a markdown heading, NOT a comment marker — it must inject
    (tmp_path / "SOUL.md").write_text("# Persona\nBe concise.", encoding="utf-8")
    load = compose_persona(tmp_path)
    assert load.injected == ["SOUL.md"]
    assert "# Persona" in load.block
