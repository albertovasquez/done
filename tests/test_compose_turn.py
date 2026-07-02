"""compose_turn (#245): the one prompt-composition interface.

Pins the recipe byte-for-byte against hand-built render calls, the
ComposedPrompt invariants (menu appears once, env split out of base,
hash_inputs naming), and the assembly sites' wiring shapes — so cross-site
parity is structural, not comment-enforced prose."""
from pathlib import Path

import pytest

from harness import base_prompt, paths, skills
from harness import prompt as prompt_mod


def _mk_skill(root: Path, name: str, desc: str = "a test skill",
              flow: str | None = None) -> None:
    d = root / name
    d.mkdir(parents=True)
    fm = f"---\nname: {name}\ndescription: {desc}\n"
    if flow:
        fm += f"flows: [{flow}]\n"
    (d / "SKILL.md").write_text(fm + "---\nbody\n", encoding="utf-8")


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    """Isolated HOME/XDG plus a persona workspace and a project cwd, so no
    developer-machine skills or AGENTS.md leak into the composed bytes."""
    (tmp_path / "home").mkdir()
    (tmp_path / "cfg").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    ws = tmp_path / "agents" / "bob"
    ws.mkdir(parents=True)
    proj = tmp_path / "proj"
    proj.mkdir()
    return ws, proj


def test_composed_bytes_match_the_hand_built_recipe(iso):
    ws, proj = iso
    _mk_skill(proj / ".agents" / "skills", "proj-skill")
    composed = prompt_mod.compose_turn(
        workspace_dir=ws, cwd=str(proj), model_id="m1", system_line="TestOS",
        persona_block="P", memory_block="M")
    roots = paths.skills_dirs(project_cwd=str(proj))
    catalog = skills.load_catalog_with_skips(roots, project_cwd=str(proj))
    from harness import agents
    agents_block = agents.resolve_agents(
        persona_dir=ws, project_cwd=proj, global_dir=paths.config_dir()).block
    assert composed.base_block == base_prompt.render_base_prompt(
        skills_menu=skills.compose_menu(catalog.skills),
        agents_block=agents_block)
    assert composed.env_block == base_prompt.render_env_block(
        model_id="m1", cwd=str(proj), system_line="TestOS")
    assert composed.skill_roots == roots
    assert [m.name for m in composed.menu_metas] == [m.name for m in catalog.skills]
    assert composed.persona_block == "P"
    assert composed.memory_block == "M"


def test_no_model_renders_mock_in_env(iso):
    ws, proj = iso
    composed = prompt_mod.compose_turn(
        workspace_dir=ws, cwd=str(proj), model_id=None, system_line="S")
    assert "- Model: mock\n" in composed.env_block


def test_hash_inputs_names_match_the_boundary_contract(iso):
    """cache.boundary (#139 PR2) hashes exactly these four named blocks —
    previously a hand-listed dict at the ACP call site (acp_agent.py:550-555)."""
    ws, proj = iso
    composed = prompt_mod.compose_turn(
        workspace_dir=ws, cwd=str(proj), model_id="m", system_line="S",
        persona_block="P", memory_block="M")
    assert composed.hash_inputs == {
        "base": composed.base_block, "persona": "P",
        "memory": "M", "env": composed.env_block}


def test_menu_appears_exactly_once_and_only_in_base(iso):
    ws, proj = iso
    _mk_skill(proj / ".agents" / "skills", "only-here")
    composed = prompt_mod.compose_turn(
        workspace_dir=ws, cwd=str(proj), model_id="m", system_line="S")
    line = next(l for l in composed.skills_menu.splitlines() if "only-here" in l)
    assert composed.base_block.count(line) == 1
    assert line not in composed.env_block


def test_env_block_is_split_out_of_base(iso):
    ws, proj = iso
    composed = prompt_mod.compose_turn(
        workspace_dir=ws, cwd=str(proj), model_id="m", system_line="S")
    assert composed.env_block.startswith("\n\n# Environment\n")
    assert "# Environment" not in composed.base_block


def test_advertise_persona_files_appends_only_that_section(iso):
    """The ACP (interactive) shape differs from the run_traced/cron shape by
    EXACTLY the # Persona files section — nothing else may move."""
    ws, proj = iso
    kw = dict(workspace_dir=ws, cwd=str(proj), model_id="m", system_line="S",
              persona_block="P", memory_block="M")
    plain = prompt_mod.compose_turn(**kw)
    acp = prompt_mod.compose_turn(**kw, advertise_persona_files=True)
    assert acp.env_block == plain.env_block
    assert acp.base_block.startswith(plain.base_block)
    tail = acp.base_block[len(plain.base_block):]
    assert tail.startswith("\n\n# Persona files\n")
    assert str(ws.resolve()) in tail
    assert "# Persona files" not in plain.base_block


def test_advertise_without_workspace_is_a_noop(iso):
    _, proj = iso
    kw = dict(workspace_dir=None, cwd=str(proj), model_id="m", system_line="S")
    assert (prompt_mod.compose_turn(**kw, advertise_persona_files=True).base_block
            == prompt_mod.compose_turn(**kw).base_block)


def test_flows_scope_the_menu_but_not_the_catalog(iso):
    ws, proj = iso
    _mk_skill(proj / ".agents" / "skills", "docs-skill", flow="docs")
    _mk_skill(proj / ".agents" / "skills", "ops-skill", flow="ops")
    _mk_skill(proj / ".agents" / "skills", "global-skill")
    (ws / "persona.toml").write_text('flows = ["docs"]\n', encoding="utf-8")
    composed = prompt_mod.compose_turn(
        workspace_dir=ws, cwd=str(proj), model_id="m", system_line="S")
    names = [m.name for m in composed.menu_metas]
    assert "docs-skill" in names and "global-skill" in names
    assert "ops-skill" not in names
    # the full catalog is NOT flow-scoped (capability answers see everything)
    assert "ops-skill" in [m.name for m in composed.catalog.skills]


def test_workspace_project_roots_classified_as_project(iso):
    """The cron shape (cwd = the workspace) classifies workspace .agents/.claude
    skill roots as 'project' — the one deliberate byte change #245 ships (the
    executor used to load the catalog without project_cwd, labeling these roots
    'unknown'; skills.py:103-104 documents that project_cwd must match the cwd
    skills_dirs() was built with)."""
    ws, _ = iso
    _mk_skill(ws / ".agents" / "skills", "ws-skill")
    composed = prompt_mod.compose_turn(
        workspace_dir=ws, cwd=str(ws), model_id=None, system_line="S")
    meta = next(m for m in composed.catalog.skills if m.name == "ws-skill")
    assert meta.origin == "project"
    assert "## project" in composed.skills_menu
