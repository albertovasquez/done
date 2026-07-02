# compose_turn — one prompt-composition interface (#245) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract the ~25-line prompt-assembly recipe (skill roots → catalog → flow scoping → menu → AGENTS.md tiers → base_block + env_block) — today hand-transcribed at `acp_agent.py`, `run_traced.py`, and `jobs/executor.py` — into one `harness/prompt.py:compose_turn()` seam, and give the cron copy the real execution test it has never had.

**Architecture:** New pure module `harness/prompt.py` exposing `compose_turn(...) -> ComposedPrompt`. Persona/memory blocks are **inputs** (the ACP path session-caches them; run_traced/cron resolve fresh — the seam must not own either lifecycle). The three call sites shrink to one call + unpacking into their existing local variable names, so all downstream code is untouched. A new capture test executes the cron `run_turn` closure end-to-end and asserts its blocks equal `compose_turn`'s output — parity becomes structural, not comment-enforced.

**Tech Stack:** Python 3.11+, pytest, dataclasses. No new dependencies.

## Global Constraints

- **Byte-identical refactor** for the ACP and run_traced paths — the #139 cache invariants (byte-stable system prompt, env-at-tail, session-cached persona/memory) must not move. ONE deliberate byte change is authorized, on the **cron path only**: `load_catalog_with_skips` now receives `project_cwd` (the executor omitted it, violating skills.py:103-104's documented contract "pass the same cwd skills_dirs() was built with"), so workspace `.agents/.claude` skill roots classify as `project` instead of `unknown` in cron menus. Call this out in the PR body.
- **NEVER weaken** `tests/test_prompt_cache_stability.py`, `tests/test_acp_history_boundary.py`, `tests/test_base_prompt.py`, `tests/test_history_view.py`, `tests/test_prompt_hash.py`. A failure there is a real regression in your change, not a test to fix.
- **No perf work**: #148 (catalog double disk-walk) and #153 (AGENTS.md re-read per turn) are noted as follow-ups in the module docstring, not implemented.
- **No skills.py API changes**: `compose_menu` stays public (`persona.py:226` and `memory.py:296` mirror/call it — the issue's original "make it internal" idea is superseded by the broadened scope).
- Never modify `upstream/`. Subagent workers (`tools/subagent.py`) are deliberately divergent — out of scope.
- Work from the worktree root `/Users/alberto/Work/Quiubo/harness/.claude/worktrees/compose-turn-245`. Test command (run from worktree root): `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest <files> -q`.
- Full-suite baseline on clean main: up to SIX order-pollution failures (spine, completed_turn_ordering, 3× jobs_table snapshots, agent_dashboard_screen) plus `test_pilot_streams_deltas_into_one_markdown_widget` flaking ~1/5. All pass isolated. Verify isolated before blaming your diff.

## File Structure

- Create: `harness/prompt.py` — the seam. `ComposedPrompt` (frozen dataclass) + `compose_turn()` (pure recipe; only the filesystem reads the steps already perform).
- Create: `tests/test_compose_turn.py` — definition tests (bytes match hand-built recipe), invariants (menu-once, env split, hash_inputs), wiring-shape parity (ACP shape = headless shape + persona-files section; cron origin classification).
- Create: `tests/jobs/test_executor_compose.py` — executes the real cron `run_turn` closure and asserts its blocks against `compose_turn` (closes the issue's sharpest test gap).
- Modify: `harness/run_traced.py:186-215` — replace inline recipe with one call.
- Modify: `harness/acp_agent.py:513-555` — replace inline recipe + hand-listed hash dict.
- Modify: `harness/jobs/executor.py:107-206` — replace inline recipe in `run_turn`; trim orphaned imports; update the parity prose in the `_default_deps` docstring.

---

### Task 1: `harness/prompt.py` — ComposedPrompt + compose_turn, with contract tests

**Files:**
- Create: `harness/prompt.py`
- Test: `tests/test_compose_turn.py`

**Interfaces:**
- Consumes (existing, verified against main @ 581a3c7): `paths.skills_dirs(project_cwd)`, `skills.load_catalog_with_skips(roots, project_cwd)`, `persona_config.read_flows(workspace_dir)`, `flows.scope_catalog(metas, enabled_flows)`, `skills.compose_menu(metas)`, `agents.resolve_agents(*, persona_dir, project_cwd, global_dir)`, `base_prompt.render_base_prompt(*, persona_id, persona_dir, skills_menu, agents_block)`, `base_prompt.render_env_block(*, model_id, cwd, system_line)`, `paths.config_dir()`.
- Produces (Tasks 2-4 rely on these exact names):
  - `compose_turn(*, workspace_dir: Path | None, cwd: str | Path | None, model_id: str | None, system_line: str, persona_block: str = "", memory_block: str = "", advertise_persona_files: bool = False) -> ComposedPrompt`
  - `ComposedPrompt` fields: `skill_roots: list[Path]`, `catalog: skills.CatalogLoad`, `menu_metas: list[skills.SkillMeta]`, `skills_menu: str`, `base_block: str`, `env_block: str`, `persona_block: str`, `memory_block: str`; property `hash_inputs -> dict[str, str]` with keys exactly `{"base", "persona", "memory", "env"}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_compose_turn.py` with exactly:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_compose_turn.py -q`
Expected: collection error — `ImportError: cannot import name 'prompt' from 'harness'` (module does not exist yet).

- [ ] **Step 3: Write the implementation**

Create `harness/prompt.py` with exactly:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_compose_turn.py -q`
Expected: `9 passed`.

- [ ] **Step 5: Commit**

```bash
git add harness/prompt.py tests/test_compose_turn.py
git commit -m "feat(prompt): compose_turn — one prompt-composition seam (#245)"
```

---

### Task 2: Switch `run_traced.py` to compose_turn

**Files:**
- Modify: `harness/run_traced.py:186-215`
- Test (existing, must stay green): `tests/test_run_traced.py`, `tests/test_prompt_cache_stability.py`

**Interfaces:**
- Consumes: `harness.prompt.compose_turn` / `ComposedPrompt` from Task 1.
- Produces: nothing new — downstream code keeps using the existing local names `skills_roots`, `_skipped_skills`, `_shadowed_skills`, `_menu_metas`, `base_block`, `env_block` (Router at :243, ChatHandler at :247-252, `load_skills` lambda at :255).

- [ ] **Step 1: Replace the inline recipe**

In `harness/run_traced.py`, replace lines 186-215 — currently:

```python
    from datetime import date
    from harness import paths as _paths
    from harness import flows as _flows
    from harness import persona_config as _persona_config
    persona_block = _persona.resolve_persona(workspace_dir).block
    memory_block = _memory.resolve_memory(workspace_dir, today=date.today()).block
    # Lazy skill discovery: the agent gets a flow-scoped MENU (names+descriptions)
    # and pulls bodies on demand via load_skill. No flows on the persona => full
    # catalog, no gating (no-op vs. before).
    skills_roots = _paths.skills_dirs(project_cwd=args.cwd)   # project .agents/.claude skills too
    _catalog_load = skills.load_catalog_with_skips(skills_roots, project_cwd=args.cwd)
    _full_catalog = _catalog_load.skills
    _skipped_skills = _catalog_load.skipped       # surfaced in the capability answer
    _shadowed_skills = _catalog_load.shadowed     # name clashes across roots (later won)
    _enabled_flows = _persona_config.read_flows(workspace_dir)
    _menu_metas = (_flows.scope_catalog(_full_catalog, _enabled_flows)
                   if _enabled_flows else _full_catalog)
    # Three-tier AGENTS.md (persona > project > global), folded into base_block so
    # both the agent runner and the chat handler inherit it. No-op when no files.
    from harness import agents as _agents
    _agents_block = _agents.resolve_agents(
        persona_dir=workspace_dir, project_cwd=args.cwd,
        global_dir=_paths.config_dir()).block
    base_block = base_prompt.render_base_prompt(
        skills_menu=skills.compose_menu(_menu_metas),
        agents_block=_agents_block)
    env_block = base_prompt.render_env_block(
        model_id=(worker_model_id or "mock"),
        cwd=args.cwd,
        system_line=platform.platform())
```

with:

```python
    from datetime import date
    from harness import prompt as _prompt
    persona_block = _persona.resolve_persona(workspace_dir).block
    memory_block = _memory.resolve_memory(workspace_dir, today=date.today()).block
    # One composition seam (#245): roots → catalog → flow scoping → menu →
    # AGENTS.md tiers → base_block + env_block, shared with acp_agent.prompt
    # and jobs.executor. persona/memory resolve FRESH here (per invocation);
    # the seam takes them as inputs.
    composed = _prompt.compose_turn(
        workspace_dir=workspace_dir, cwd=args.cwd,
        model_id=worker_model_id, system_line=platform.platform(),
        persona_block=persona_block, memory_block=memory_block)
    skills_roots = composed.skill_roots
    _skipped_skills = composed.catalog.skipped    # surfaced in the capability answer
    _shadowed_skills = composed.catalog.shadowed  # name clashes across roots (later won)
    _menu_metas = composed.menu_metas
    base_block = composed.base_block
    env_block = composed.env_block
```

- [ ] **Step 2: Remove imports your change orphaned**

Run: `grep -n "base_prompt\.\|_paths\.\|_flows\.\|_persona_config\.\|_agents\." harness/run_traced.py`
For each module with NO remaining uses in the file, delete its import (check the file-top imports too — `base_prompt` is imported at the top; `_paths`/`_flows`/`_persona_config`/`_agents` were function-local imports in the replaced block). Do NOT remove imports still used elsewhere (`skills` IS still used at :255 `skills.compose(skills_roots, names)`; `platform` is still used in the new block).

- [ ] **Step 3: Run the tests**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_run_traced.py tests/test_prompt_cache_stability.py tests/test_compose_turn.py -q`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add harness/run_traced.py
git commit -m "refactor(prompt): run_traced composes via compose_turn (#245)"
```

---

### Task 3: Switch `acp_agent.py` to compose_turn

**Files:**
- Modify: `harness/acp_agent.py:513-560`
- Test (existing, must stay green): `tests/test_acp_smoke.py`, `tests/test_acp_agent.py`, `tests/test_acp_session_context.py`, `tests/test_acp_history_boundary.py`, `tests/test_prompt_cache_stability.py`

**Interfaces:**
- Consumes: `harness.prompt.compose_turn` / `ComposedPrompt.hash_inputs` from Task 1.
- Produces: nothing new — downstream code (chat branch, tool-probe, `compose_context` at :714-716, `_fixed_overhead` at :583) keeps using the existing local names `_skill_roots`, `_catalog_load`, `base_block`, `env_block`.
- CRITICAL: `state.persona_block`/`state.memory_block` stay session-cached at :399-423 — compose_turn receives them as inputs. Do NOT move that cache.

- [ ] **Step 1: Replace the inline recipe**

In `harness/acp_agent.py`, replace lines 513-544 — currently:

```python
        ws = state.workspace_dir
        # Lazy skill discovery: a flow-scoped MENU (names+descriptions) in the
        # prompt; the agent pulls bodies on demand via load_skill. Resolve roots
        # PER TURN from the session cwd so this session's project .agents/.claude
        # skills are included (the router's startup catalog is global-only).
        from harness import flows as _flows
        from harness import persona_config as _persona_config
        from harness import paths as _paths
        _skill_roots = _paths.skills_dirs(project_cwd=state.cwd)
        _catalog_load = skills.load_catalog_with_skips(_skill_roots, project_cwd=state.cwd)
        _enabled_flows = _persona_config.read_flows(ws)
        _menu_metas = (_flows.scope_catalog(_catalog_load.skills, _enabled_flows)
                       if _enabled_flows else _catalog_load.skills)
        _skills_menu = skills.compose_menu(_menu_metas)
        # Three-tier AGENTS.md (persona > project > global), folded into base_block
        # so BOTH the chat branch and the agent branch below inherit it (both consume
        # base_block). No-op when no AGENTS.md files exist.
        from harness import agents as _agents
        _agents_block = _agents.resolve_agents(
            persona_dir=ws,
            project_cwd=Path(state.cwd) if state.cwd else None,
            global_dir=_paths.config_dir()).block
        # Absolute path so the agent's Edit tool (which requires absolute paths) can
        # act on it; .resolve() also guards a relative XDG_CONFIG_HOME (Codex).
        base_block = base_prompt.render_base_prompt(
            persona_id=(ws.name if ws else None),
            persona_dir=(str(ws.resolve()) if ws else None),
            skills_menu=_skills_menu,
            agents_block=_agents_block)
        env_block = base_prompt.render_env_block(
            model_id=(model_id or "mock"),
            cwd=state.cwd, system_line=platform.platform())
```

with:

```python
        ws = state.workspace_dir
        # One composition seam (#245): skill roots (PER TURN from the session cwd
        # so this session's project .agents/.claude skills are included — the
        # router's startup catalog is global-only) → catalog → flow scoping →
        # menu → AGENTS.md tiers → base_block + env_block (env rendered
        # separately, appended at the system-prompt TAIL — #139). persona/memory
        # ride in from the per-SESSION cache above — compose_turn never resolves
        # them (fresh resolution here would break mid-session byte-stability).
        # advertise_persona_files: the interactive shape names the persona dir so
        # the agent's Edit tool (absolute paths) can act on it.
        from harness import prompt as _prompt
        composed = _prompt.compose_turn(
            workspace_dir=ws, cwd=state.cwd,
            model_id=model_id, system_line=platform.platform(),
            persona_block=state.persona_block or "",
            memory_block=state.memory_block or "",
            advertise_persona_files=True)
        _skill_roots = composed.skill_roots
        _catalog_load = composed.catalog
        base_block = composed.base_block
        env_block = composed.env_block
```

- [ ] **Step 2: Switch the hash dict to the seam's property**

Still in `harness/acp_agent.py` (immediately below), replace:

```python
        from harness import prompt_hash as _prompt_hash
        _hashes = _prompt_hash.block_hashes({
            "base": base_block,
            "persona": state.persona_block or "",
            "memory": state.memory_block or "",
            "env": env_block,
        })
```

with:

```python
        from harness import prompt_hash as _prompt_hash
        _hashes = _prompt_hash.block_hashes(composed.hash_inputs)
```

(The `# cache.boundary: ...` comment above this block stays.)

- [ ] **Step 3: Remove references your change orphaned**

Run: `grep -n "_skills_menu\|_menu_metas\|_enabled_flows\|_agents_block\|_flows\.\|_persona_config\.\|_paths\.\|base_prompt\." harness/acp_agent.py`
The replaced block was the only definition site of `_skills_menu`/`_menu_metas`/`_enabled_flows`/`_agents_block` inside `prompt()` — confirm no remaining reads (there are none on main @ 581a3c7). Remove the file-top `base_prompt` import ONLY if the grep shows no other use in the file; leave `skills`/`platform`/`Path` imports (still used elsewhere).

- [ ] **Step 4: Run the tests**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_acp_smoke.py tests/test_acp_agent.py tests/test_acp_session_context.py tests/test_acp_history_boundary.py tests/test_prompt_cache_stability.py tests/test_compose_turn.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add harness/acp_agent.py
git commit -m "refactor(prompt): acp_agent composes via compose_turn (#245)"
```

---

### Task 4: Switch `jobs/executor.py` to compose_turn + the cron capture test

The sharpest gap in #245: the cron composition closure has never been executed by a test. This task writes that test FIRST — it must fail against the current executor (which omits `project_cwd` when loading the catalog, so workspace project-root skills label `unknown` instead of `project`) — then switches the executor to the seam, turning the test green. That red→green transition IS the parity fix.

**Files:**
- Modify: `harness/jobs/executor.py:107-206` (`_default_deps` docstring, imports, `run_turn` body)
- Test: `tests/jobs/test_executor_compose.py` (new)
- Test (existing, must stay green): `tests/jobs/test_executor.py`, `tests/jobs/test_executor_budget.py`, `tests/jobs/test_executor_override.py`, `tests/jobs/test_executor_permission_gate.py`, `tests/jobs/test_executor_persona_stamp.py`

**Interfaces:**
- Consumes: `harness.prompt.compose_turn` / `ComposedPrompt` from Task 1; `harness.agent_build.build_persona_agent` (monkeypatched in the test).
- Produces: `run_turn` keeps its exact signature and return (`env._next_run_override` pass-through) — `run_headless_turn` and `Deps` are untouched.

- [ ] **Step 1: Write the failing capture test**

Create `tests/jobs/test_executor_compose.py` with exactly:

```python
"""The cron composition closure, actually executed (#245): run the real
_default_deps().run_turn and assert the blocks it hands the runner equal the
shared seam's output for the same inputs. Parity with the interactive path is
structural now — before #245 no test executed this closure at all
(test_executor.py injects fake run_turn lambdas)."""
import platform
from types import SimpleNamespace

import harness.jobs.executor as ex
from harness import prompt as prompt_mod


def _mk_skill(root, name):
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: a test skill\n---\nbody\n",
        encoding="utf-8")


def test_run_turn_composes_via_the_shared_seam(tmp_path, monkeypatch):
    (tmp_path / "home").mkdir()
    (tmp_path / "cfg").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    ws = tmp_path / "agents" / "bob"
    ws.mkdir(parents=True)
    # A workspace project-root skill makes origin labeling observable: the seam
    # classifies it 'project'; the pre-#245 executor (catalog loaded without
    # project_cwd) labeled it 'unknown' — cron menus diverged from run_traced's.
    _mk_skill(ws / ".agents" / "skills", "ws-skill")

    captured = {}

    def fake_build(*, agent_id, model_name, skill_roots, memory_root,
                   agent_cfg, cwd):
        captured["skill_roots"] = skill_roots
        runner = SimpleNamespace(_env=SimpleNamespace())

        def run(message, **kwargs):
            captured["message"] = message
            captured.update(kwargs)
            return iter(())

        runner.run = run
        return runner, None

    import harness.agent_build
    monkeypatch.setattr(harness.agent_build, "build_persona_agent", fake_build)

    deps = ex._default_deps()
    deps.run_turn(model_id=None, workspace=ws, persona_block="P",
                  memory_block="M", message="do the thing")

    expected = prompt_mod.compose_turn(
        workspace_dir=ws, cwd=str(ws), model_id=None,
        system_line=platform.platform(), persona_block="P", memory_block="M")
    assert captured["base_block"] == expected.base_block
    assert captured["env_block"] == expected.env_block
    assert captured["persona_block"] == "P"
    assert captured["memory_block"] == "M"
    assert captured["skill_block"] == ""     # no router-seeded skills on cron
    assert captured["skill_roots"] == expected.skill_roots
    assert "## project" in captured["base_block"]
```

- [ ] **Step 2: Run it to verify it fails against the current executor**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/jobs/test_executor_compose.py -q`
Expected: FAIL on `captured["base_block"] == expected.base_block` (the current executor's menu says `## unknown` where the seam says `## project`). If it PASSES here, STOP — the premise is wrong; re-read the current executor before proceeding.

- [ ] **Step 3: Switch the executor to the seam**

In `harness/jobs/executor.py`:

3a. In the `_default_deps` docstring (lines 108-117), replace the sentence block:

```python
    """Wire the real harness functions.

    The cron turn is composed IDENTICALLY to the interactive/run_traced path
    (spec §6: the daemon never short-circuits compose_context). compose() resolves
    persona+memory; run_turn() builds the skill spine via persona.compose_context
    and the base/AGENTS.md block via base_prompt.render_base_prompt, then runs the
    turn with the REAL skill_block + base_block — not "" as before.

    All live-source symbols verified against run_traced.py + persona_sessions.py
    (see inline comments).
    """
```

with:

```python
    """Wire the real harness functions.

    The cron turn is composed via the SAME compose_turn seam the interactive
    (acp_agent.prompt) and run_traced paths call (#245) — parity is structural
    now, asserted by tests/jobs/test_executor_compose.py, not comment-enforced.
    compose() resolves persona+memory (fresh per invocation — spec §6: the
    daemon never short-circuits compose_context); run_turn() assembles the rest
    through harness.prompt.compose_turn and runs the turn with the REAL
    skill_block + base_block.
    """
```

3b. Replace the harness-import block inside `_default_deps` (lines 122-131) — currently:

```python
    from harness import agents as _agents       # resolve_agents: agents.py:56
    from harness import base_prompt as _base_prompt  # render_base_prompt: base_prompt.py:47
    from harness import flows as _flows         # scope_catalog: flows.py:11
    from harness import memory as _memory     # resolve_memory: memory.py
    from harness import paths as _paths        # skills_dirs/config_dir: paths.py:50/16
    from harness import persona as _persona   # resolve_persona / compose_context: persona.py:102/111
    from harness import persona_config as _persona_config  # read_flows: persona_config.py:38
    from harness import persona_sessions as _ps   # resolve_session_model: persona_sessions.py:20
    from harness import skills as _skills      # load_catalog_with_skips: skills.py:85
    from harness import vibeproxy
```

with:

```python
    from harness import memory as _memory     # resolve_memory: memory.py
    from harness import paths as _paths        # mini_yaml_path: paths.py:113
    from harness import persona as _persona   # resolve_persona / compose_context: persona.py:207/216
    from harness import persona_sessions as _ps   # resolve_session_model: persona_sessions.py:20
    from harness import prompt as _prompt     # compose_turn: prompt.py (#245)
    from harness import vibeproxy
```

(deleted: `_agents`, `_base_prompt`, `_flows`, `_persona_config`, `_skills` — all only used by the old inline recipe; kept: the surrounding `import platform`, `from datetime import date`, `import yaml`, `import os` lines, all still used.)

3c. Replace the `run_turn` closure body (lines 148-206) with:

```python
    def run_turn(*, model_id: str | None, workspace: Path, persona_block: str,
                 memory_block: str, message: str, wall_budget: int | None = None,
                 mode: str | None = None) -> None:
        # One composition seam (#245), shared with acp_agent.prompt and
        # run_traced — the cron turn is indistinguishable from the persona
        # typing live (spec §6). cwd for a cron job IS the workspace.
        composed = _prompt.compose_turn(
            workspace_dir=workspace, cwd=str(workspace),
            model_id=model_id, system_line=platform.platform(),
            persona_block=persona_block, memory_block=memory_block)
        # compose_context bundles persona+memory+skills via the SAME chokepoint
        # the ACP path uses. skill_names=[] => no router-seeded skill bodies;
        # the lazy menu rides composed.base_block (compose_turn owns menu-once).
        ctx = _persona.compose_context(persona_block, memory_block,
                                       composed.skill_roots, [])
        # Construction via the shared chokepoint (harness/agent_build.py). Cron
        # passes model_name=None for mock, else the qualified model; the builder
        # stamps env._active_persona = agent_id so env-bound tools resolve.
        from harness.agent_build import build_persona_agent
        runner, _registry = build_persona_agent(
            agent_id=workspace.name,
            model_name=(None if model_id is None else model_id),
            skill_roots=composed.skill_roots,
            memory_root=workspace,
            agent_cfg=_observe_or_default_cfg(_load_agent_cfg(), mode),
            cwd=str(workspace),
        )
        # #168: this is a HEADLESS path (no elicitation channel), so file tools must
        # be gated + confined to the job's workspace — risky/out-of-root ops fail
        # CLOSED. Same chokepoint machinery as the ACP path, deny-by-default policy.
        # Applied to the env the builder constructed (runner._env).
        stamp_headless_gate(runner._env, workspace)
        # Cron budget (Task 8): stamp the job's configured timeout onto the env so
        # any subagent worker the turn spawns caps its wall-time at this budget
        # (subagent.py reads env._remaining_secs). Static upper bound, not a live
        # countdown, in v1. The interactive path never passes wall_budget, so it
        # leaves _remaining_secs unset (None) — behavior-preserving.
        if wall_budget:
            runner._env._remaining_secs = wall_budget
        # Pass the REAL skill_block + base_block (run_traced parity via the seam).
        for _ in runner.run(message, skill_block=ctx.skill_block,
                            persona_block=ctx.persona_block,
                            memory_block=ctx.memory_block, base_block=composed.base_block,
                            env_block=composed.env_block):
            pass
        # A self-paced (Dynamic) loop turn calls set_next_run, which stamps
        # env._next_run_override. Surface it so ops.run can arm the next run.
        return getattr(runner._env, "_next_run_override", None)
```

Note the ONE behavior change vs. the old body, which is the point of Step 2's red: the catalog is now loaded with `project_cwd` (inside compose_turn), matching skills.py's documented contract and the other two sites. Everything else is byte-identical (`menu_metas` no longer passed to `compose_context` — its `skills_menu` field was only ever used to feed `render_base_prompt`, which compose_turn now owns).

- [ ] **Step 4: Run the tests to verify green**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/jobs/ tests/test_compose_turn.py -q`
Expected: all pass — including the Step 1 test and `test_default_deps_constructs`.

- [ ] **Step 5: Commit**

```bash
git add harness/jobs/executor.py tests/jobs/test_executor_compose.py
git commit -m "refactor(prompt): cron executor composes via compose_turn; first real run_turn test (#245)"
```

---

### Task 5: Whole-branch verification

**Files:** none modified — verification only.

- [ ] **Step 1: Confirm the safety net is untouched**

Run: `git diff main --stat -- tests/test_prompt_cache_stability.py tests/test_acp_history_boundary.py tests/test_base_prompt.py tests/test_history_view.py tests/test_prompt_hash.py`
Expected: empty output (zero changes to the contract tests).

- [ ] **Step 2: Full suite**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/ -q`
Expected: only the known order-pollution baseline may fail (up to six: spine, completed_turn_ordering, 3× jobs_table snapshots, agent_dashboard_screen; plus the ~1/5 pilot-streams flake). ANY other failure is a regression from this branch — investigate before proceeding.

- [ ] **Step 3: Re-run any baseline failures isolated**

Run each failing file alone, e.g.: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_spine.py -q` (adjust to the actual failing files).
Expected: all pass isolated.
