"""One prompt-composition interface (#245): the per-turn assembly recipe —
skill roots → catalog → flow scoping → menu → AGENTS.md tiers → base_block +
env_block — owned here instead of hand-transcribed at the three assembly
sites (acp_agent.prompt, run_traced.main, jobs.executor run_turn).

Persona and memory blocks are INPUTS, never resolved here: the ACP path
caches them once per session on SessionState (mid-session byte-stability,
#139) while run_traced/cron resolve them fresh per invocation — resolving
inside this seam would break one lifecycle or the other.

Invariants owned here:
- the skills menu appears exactly once, inside base_block; ComposedPrompt
  exposes menu_metas/skills_menu as DATA for consumers (router catalog, chat
  handler) that must not render it into the prompt a second time;
- the volatile # Environment block is rendered separately so callers keep it
  at the system-prompt TAIL, out of the cacheable prefix (#139).

Follow-ups with a single home now: #148 (catalog double disk-walk per turn),
#153 (AGENTS.md tiers re-read per turn). Deliberately not addressed — this
seam ships byte-identical to the recipes it replaces.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from harness import agents, base_prompt, flows, paths, persona_config, skills


@dataclass(frozen=True)
class ComposedPrompt:
    """Everything the assembly sites previously hand-built, in one bundle."""
    skill_roots: list[Path]
    catalog: skills.CatalogLoad          # full (unscoped) catalog + skipped/shadowed
    menu_metas: list[skills.SkillMeta]   # flow-scoped metas (router/menu view)
    skills_menu: str                     # rendered menu — already inside base_block
    base_block: str
    env_block: str
    persona_block: str                   # pass-through (caller-resolved, #139 cache)
    memory_block: str                    # pass-through (caller-resolved, #139 cache)

    @property
    def hash_inputs(self) -> dict[str, str]:
        """The named blocks prompt_hash.block_hashes consumes for the
        cache.boundary trace (#139 PR2) — previously hand-listed at the ACP
        call site."""
        return {"base": self.base_block, "persona": self.persona_block,
                "memory": self.memory_block, "env": self.env_block}


def compose_turn(*, workspace_dir: Path | None, cwd: str | Path | None,
                 model_id: str | None, system_line: str,
                 persona_block: str = "", memory_block: str = "",
                 advertise_persona_files: bool = False) -> ComposedPrompt:
    """Compose one turn's prompt blocks. Performs only the filesystem reads the
    recipe steps already do (catalog scan, persona.toml flows, AGENTS.md
    tiers); no caching, no LLM calls.

    cwd is the PROJECT directory (session cwd / --cwd / the job's workspace):
    it anchors the two project skill roots, classifies their origin, scopes
    the AGENTS.md Project tier, and is printed in the env block.
    advertise_persona_files appends the # Persona files section naming
    workspace_dir — the ACP (interactive) shape; headless shapes omit it."""
    skill_roots = paths.skills_dirs(project_cwd=cwd)
    catalog = skills.load_catalog_with_skips(skill_roots, project_cwd=cwd)
    enabled_flows = persona_config.read_flows(workspace_dir)
    menu_metas = (flows.scope_catalog(catalog.skills, enabled_flows)
                  if enabled_flows else catalog.skills)
    skills_menu = skills.compose_menu(menu_metas)
    agents_block = agents.resolve_agents(
        persona_dir=workspace_dir,
        project_cwd=Path(cwd) if cwd else None,
        global_dir=paths.config_dir()).block
    show_persona_files = advertise_persona_files and workspace_dir is not None
    base_block = base_prompt.render_base_prompt(
        persona_id=(workspace_dir.name if show_persona_files else None),
        persona_dir=(str(workspace_dir.resolve()) if show_persona_files else None),
        skills_menu=skills_menu,
        agents_block=agents_block)
    env_block = base_prompt.render_env_block(
        model_id=(model_id or "mock"), cwd=cwd, system_line=system_line)
    return ComposedPrompt(
        skill_roots=skill_roots, catalog=catalog, menu_metas=menu_metas,
        skills_menu=skills_menu, base_block=base_block, env_block=env_block,
        persona_block=persona_block, memory_block=memory_block)
