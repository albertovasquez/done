from pathlib import Path

from harness.agents import resolve_agents, AgentsLoad, MAX_AGENTS_CHARS


def _write(d: Path, body: str):
    d.mkdir(parents=True, exist_ok=True)
    (d / "AGENTS.md").write_text(body, encoding="utf-8")


def test_none_present_is_empty_noop(tmp_path):
    load = resolve_agents(persona_dir=tmp_path / "p", project_cwd=tmp_path / "c",
                          global_dir=tmp_path / "g")
    assert load == AgentsLoad()
    assert load.block == ""


def test_all_three_ordered_global_project_persona(tmp_path):
    _write(tmp_path / "g", "GLOBAL RULES")
    _write(tmp_path / "c", "PROJECT RULES")
    _write(tmp_path / "p", "PERSONA RULES")
    b = resolve_agents(persona_dir=tmp_path / "p", project_cwd=tmp_path / "c",
                       global_dir=tmp_path / "g").block
    assert b.index("GLOBAL RULES") < b.index("PROJECT RULES") < b.index("PERSONA RULES")
    assert "persona over project over global" in b.lower()      # precedence preamble
    assert "## Global instructions" in b and "## Persona instructions" in b


def test_blank_tier_skipped(tmp_path):
    _write(tmp_path / "g", "<!-- nothing -->\n")
    _write(tmp_path / "p", "REAL")
    load = resolve_agents(persona_dir=tmp_path / "p", project_cwd=None,
                          global_dir=tmp_path / "g")
    assert "REAL" in load.block and "Global instructions" not in load.block


def test_unreadable_recorded_not_raised(tmp_path):
    p = tmp_path / "p"; p.mkdir()
    (p / "AGENTS.md").write_bytes(b"\xff\xfe bad")
    load = resolve_agents(persona_dir=p, project_cwd=None, global_dir=None)
    assert load.skipped and load.block == ""


def test_over_cap_trimmed(tmp_path):
    _write(tmp_path / "p", "x" * (MAX_AGENTS_CHARS + 500))
    load = resolve_agents(persona_dir=tmp_path / "p", project_cwd=None, global_dir=None)
    assert "truncated" in load.block.lower()


def test_none_dirs_safe():
    assert resolve_agents(persona_dir=None, project_cwd=None, global_dir=None) == AgentsLoad()


def test_only_project_tier(tmp_path):
    _write(tmp_path / "c", "PROJECT ONLY")
    load = resolve_agents(persona_dir=None, project_cwd=tmp_path / "c", global_dir=None)
    assert "PROJECT ONLY" in load.block
    assert load.injected == ["Project"]


def test_agents_md_from_project_cwd_reaches_base_block(tmp_path):
    # The dispatch contract: an AGENTS.md in the project cwd lands in base_block,
    # which both the agent runner and ChatHandler consume.
    from harness import agents, base_prompt, paths
    (tmp_path / "AGENTS.md").write_text("PROJECT POLICY XYZ", encoding="utf-8")
    load = agents.resolve_agents(persona_dir=None, project_cwd=tmp_path,
                                 global_dir=paths.config_dir())
    bb = base_prompt.render_base_prompt(agents_block=load.block)
    assert "PROJECT POLICY XYZ" in bb
