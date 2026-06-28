# Router Flows + Lazy Skill Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-architect the router so skills carry an invocation model (model/user-invocable, flow tags), are discovered via a lazy menu + `load_skill` tool instead of all injected as context, flows are enabled per-persona via `persona.toml`, and the default harness ships a curated maturity spine.

**Architecture:** Hybrid runtime — the cheap router scopes the active flow and seeds a menu of skill names+descriptions into the agent prompt; the worker agent pulls full skill bodies on demand via a new `load_skill` tool (same execute→observation path as Read/Write/Edit, verified at `tracing_agent.py:188,194`). Skill metadata moves from a flat `(name, description)` tuple to a structured `SkillMeta`. Each layer is a strict no-op until a skill or persona opts in.

**Tech Stack:** Python 3.10+, pyyaml, tomllib, pytest, the vendored mini-swe-agent engine. Test runner: `.venv/bin/python -m pytest tests/ -q` from the worktree root.

## Global Constraints

- Work ONLY in the worktree `/Users/alberto/Work/Quiubo/harness/.claude/worktrees/router-flows` on branch `router-flows`. Run pytest with the worktree's own `.venv` (editable-install shadowing trap — verified baseline 706 green).
- ZERO upstream edits: never modify `upstream/`. Extend via the existing seams (registry, tracing_agent, base_prompt) only.
- Backward compatibility is a HARD requirement: with no new frontmatter and no `persona.toml` `flows`, behavior must be byte-identical. Every layer ends with a no-op assertion.
- `disable-model-invocation: true` → skill is NOT auto-selectable by the router (only user/explicit). `user-invocable: false` → not exposed as `/name`. Defaults: both true.
- Flow config lives in `persona.toml` (non-model config), NEVER in `done.conf` (model-only, single-homed).
- TDD: failing test first, minimal impl, green, commit. Frequent commits.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `harness/skills.py` | Skill discovery/metadata/compose | Modify: add `SkillMeta`, `_meta_from_frontmatter`, `load_catalog`→structured, `compose_menu` |
| `harness/router.py` | Classify + select skill names | Modify: consume `SkillMeta`, filter to model-invocable |
| `harness/chat_handler.py` | Capability answers from catalog | Modify: consume `SkillMeta` (1 unpack site) |
| `harness/tools/load_skill.py` | Agent-callable skill pull | Create |
| `harness/tools/registry.py` | Live tool list | Modify: optional `skill_roots`, append `LoadSkillTool` |
| `harness/base_prompt.py` | Base system prompt | Modify: optional `skills_menu` section |
| `harness/persona.py` | Per-turn context bundle | Modify: thread menu + flow scope into `TurnContext` |
| `harness/persona_config.py` | persona.toml reader | Modify: add `read_flows` |
| `harness/flows.py` | Flow scoping + map render | Create |
| `harness/run_traced.py`, `harness/acp_agent.py`, `harness/acp_main.py` | Dispatch wiring | Modify: pass roots to registry, build menu, scope flows |
| `harness/skills/<spine>/SKILL.md` | Curated default skills | Create/modify + re-tag |
| `harness/skills/ask-done/SKILL.md` | User-invoked flow map | Create |
| `harness/skills/NOTICE.md` | Attribution | Modify |
| `tests/test_skills.py`, `test_router.py`, `test_load_skill.py`, `test_flows.py`, `test_persona_config.py`, `test_base_prompt.py` | Tests | Create/modify |

---

## LAYER A — Structured catalog + invocation model

### Task A1: `SkillMeta` + `_meta_from_frontmatter`

**Files:**
- Modify: `harness/skills.py`
- Test: `tests/test_skills.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) SkillMeta(name: str, description: str, model_invocable: bool = True, user_invocable: bool = True, flows: tuple[str, ...] = ())`; `_meta_from_frontmatter(data: dict, fallback_name: str) -> SkillMeta`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_skills.py`:
```python
from harness.skills import SkillMeta, _meta_from_frontmatter

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
    assert _meta_from_frontmatter({"name":"x","description":"d","flow":"seo"}, "x").flows == ("seo",)
    assert _meta_from_frontmatter({"name":"x","description":"d","flows":["a","b"]}, "x").flows == ("a","b")
    # non-bool / non-list degrade to defaults, never raise
    g = _meta_from_frontmatter({"name":"x","description":"d",
                                "disable-model-invocation":"yes","flows":"nope"}, "x")
    assert g.model_invocable is True and g.flows == ()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_skills.py -k meta -v`
Expected: FAIL — `cannot import name 'SkillMeta'`.

- [ ] **Step 3: Write minimal implementation**

In `harness/skills.py`, after the `SkillLoad` dataclass (line 27):
```python
@dataclass(frozen=True)
class SkillMeta:
    name: str
    description: str
    model_invocable: bool = True      # False == disable-model-invocation
    user_invocable: bool = True       # False == not exposed as /name
    flows: tuple[str, ...] = ()       # () == global (always available)


def _meta_from_frontmatter(data: dict, fallback_name: str) -> SkillMeta:
    """Build a SkillMeta from a parsed frontmatter dict. Pure; never raises —
    ill-typed flags/flows degrade to defaults so one odd skill can't break the
    catalog. name/description validity is enforced by the caller (load_catalog)."""
    name = data.get("name") or fallback_name
    desc = data.get("description") or ""
    model_inv = data.get("disable-model-invocation") is not True   # only literal True disables
    user_inv = data.get("user-invocable") is not False             # only literal False hides
    raw_flow = data.get("flows", data.get("flow"))
    if isinstance(raw_flow, str):
        flows = (raw_flow,)
    elif isinstance(raw_flow, list):
        flows = tuple(f for f in raw_flow if isinstance(f, str))
    else:
        flows = ()
    return SkillMeta(name=name, description=desc, model_invocable=model_inv,
                     user_invocable=user_inv, flows=flows)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_skills.py -k meta -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add harness/skills.py tests/test_skills.py
git commit -m "feat(skills): SkillMeta + frontmatter invocation model parsing"
```

### Task A2: `load_catalog` returns `list[SkillMeta]`

**Files:**
- Modify: `harness/skills.py:43-68`
- Test: `tests/test_skills.py`

**Interfaces:**
- Consumes: `SkillMeta`, `_meta_from_frontmatter` (A1).
- Produces: `load_catalog(roots: list[Path]) -> list[SkillMeta]` (was `list[tuple[str,str]]`), sorted by name; later root wins by name.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_skills.py` (use the existing tmp-skill helper pattern in that file; if none, create dirs inline):
```python
def test_load_catalog_returns_skillmeta(tmp_path):
    d = tmp_path / "alpha"; d.mkdir()
    (d / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: A\ndisable-model-invocation: true\nflow: seo\n---\nbody\n")
    cat = load_catalog([tmp_path])
    assert cat == [SkillMeta(name="alpha", description="A",
                             model_invocable=False, user_invocable=True, flows=("seo",))]
```
(Import `load_catalog` if not already imported.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_skills.py -k load_catalog_returns -v`
Expected: FAIL — returns tuples, not `SkillMeta`.

- [ ] **Step 3: Write minimal implementation**

Replace `load_catalog` body in `harness/skills.py` (lines 43-68):
```python
def load_catalog(roots: list[Path]) -> list[SkillMeta]:
    """Scan each root's <name>/SKILL.md; later roots override earlier by name.
    Invalid skill dirs are silently omitted (can't select what can't parse)."""
    merged: dict[str, SkillMeta] = {}
    for root in roots:
        if not Path(root).is_dir():
            continue
        for child in sorted(Path(root).iterdir(), key=lambda p: p.name):
            if not child.is_dir():
                continue
            try:
                data, _ = _parse_skill_md(child / "SKILL.md")
                name, desc = data.get("name"), data.get("description")
                if not name or not desc:
                    raise ValueError("frontmatter missing name/description")
                if name != child.name:
                    raise ValueError("name mismatch")
            except (OSError, UnicodeDecodeError, yaml.YAMLError, ValueError) as e:
                logger.warning("skipping skill %s/SKILL.md: %s", child.name, e)
                continue
            merged[name] = _meta_from_frontmatter(data, name)  # later root wins
    return [merged[k] for k in sorted(merged)]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_skills.py -k load_catalog -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/skills.py tests/test_skills.py
git commit -m "feat(skills): load_catalog returns structured SkillMeta list"
```

### Task A3: Router + ChatHandler consume `SkillMeta`; router filters dormant skills

**Files:**
- Modify: `harness/router.py:80,99` and `_system_prompt`
- Modify: `harness/chat_handler.py:46`
- Test: `tests/test_router.py`, `tests/test_chat_handler.py`

**Interfaces:**
- Consumes: `list[SkillMeta]` as the `catalog`.
- Produces: router only auto-selects `model_invocable` skills; `Router.catalog` still returns the `list[SkillMeta]`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_router.py` (the file uses a `_CATALOG` of tuples + a `_stub`):
```python
from harness.skills import SkillMeta

def test_router_drops_non_model_invocable_selection():
    cat = [SkillMeta("a", "desc a", model_invocable=True),
           SkillMeta("deploy", "desc deploy", model_invocable=False)]
    r = Router(_stub(json.dumps({"task_type": "code_fix",
               "skills": ["a", "deploy"], "confidence": 0.9,
               "reasoning": "x", "suggested_model": None})), catalog=cat)
    cls = r.classify("do it")
    assert cls.skills == ["a"]            # 'deploy' is dormant -> dropped
```
Also update the existing `_CATALOG` in this file from tuples to `SkillMeta(...)` and any assertion that compared catalog membership.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_router.py -v`
Expected: FAIL — `for n, d in catalog` unpacks a 5-field dataclass; and the filter doesn't exist.

- [ ] **Step 3: Write minimal implementation**

In `harness/router.py`:
- `_system_prompt(catalog)` line 80 change to only list model-invocable skills:
```python
        + "\n".join(f"  {m.name}: {m.description}"
                    for m in catalog if m.model_invocable)
```
- `__init__` line 99:
```python
        self._catalog_names = {m.name for m in catalog if m.model_invocable}
```
(`self._catalog` stays the full `list[SkillMeta]`; the `catalog` property is unchanged.)

In `harness/chat_handler.py` line 46:
```python
    lines += [f"- **{m.name}** — {m.description}" for m in catalog]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_router.py tests/test_chat_handler.py -v`
Expected: PASS.

- [ ] **Step 5: Run the FULL suite — no-op proof for Layer A**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS, count >= 706 (existing tests for the 4 default skills must still pass — they have no new frontmatter, so all-default catalog behaves identically).

- [ ] **Step 6: Commit**

```bash
git add harness/router.py harness/chat_handler.py tests/
git commit -m "feat(router): consume SkillMeta; never auto-select disable-model-invocation skills"
```

---

## LAYER B — Lazy discovery + `load_skill` tool (hybrid)

### Task B1: `compose_menu` (names+descriptions, no bodies)

**Files:**
- Modify: `harness/skills.py`
- Test: `tests/test_skills.py`

**Interfaces:**
- Consumes: `list[SkillMeta]` (A2).
- Produces: `compose_menu(metas: list[SkillMeta]) -> str` — a `# Skills` section listing name + description, or `""` when empty.

- [ ] **Step 1: Write the failing test**

```python
from harness.skills import compose_menu

def test_compose_menu_lists_names_not_bodies():
    metas = [SkillMeta("a", "does A"), SkillMeta("b", "does B")]
    out = compose_menu(metas)
    assert "does A" in out and "**a**" in out and "load_skill" in out
    assert "body" not in out   # bodies are NOT in the menu

def test_compose_menu_empty_is_blank():
    assert compose_menu([]) == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_skills.py -k compose_menu -v`
Expected: FAIL — no `compose_menu`.

- [ ] **Step 3: Write minimal implementation**

Add to `harness/skills.py`:
```python
def compose_menu(metas: list[SkillMeta]) -> str:
    """A lightweight skill MENU (names + one-line descriptions, no bodies) for
    the agent prompt. The agent pulls a body with load_skill when it needs it."""
    if not metas:
        return ""
    lines = "\n".join(f"- **{m.name}** — {m.description}" for m in metas)
    return ("\n\n# Skills\n\n"
            "These skills are available. Their full instructions are NOT loaded "
            "yet. Before doing work a skill governs, call the `load_skill` tool "
            "with its name to read its instructions. Don't load skills you won't "
            "use.\n\n" + lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_skills.py -k compose_menu -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/skills.py tests/test_skills.py
git commit -m "feat(skills): compose_menu — lazy skill menu (names, no bodies)"
```

### Task B2: `LoadSkillTool` + registry wiring

**Files:**
- Create: `harness/tools/load_skill.py`
- Modify: `harness/tools/registry.py`
- Test: `tests/test_load_skill.py`

**Interfaces:**
- Consumes: `skills.compose(roots, [name])` (existing).
- Produces: `LoadSkillTool(roots: list[Path])` with `.name == "load_skill"`, `.schema`, `.execute(args, env) -> dict`. `build_registry(skill_roots: list[Path] | None = None) -> list[Tool]` appends it only when roots given.
- Per-turn dedup home: the tool reads/writes a `set` on `env` (turn-scoped) keyed `_loaded_skills`, falling back to a private set if `env` has no attribute slot.

- [ ] **Step 1: Write the failing test**

Create `tests/test_load_skill.py`:
```python
from pathlib import Path
from types import SimpleNamespace
from harness.tools.load_skill import LoadSkillTool
from harness.tools.registry import build_registry

def _skill(tmp_path, name, body="do the thing"):
    d = tmp_path / name; d.mkdir()
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\n{body}\n")

def _env(tmp_path):
    return SimpleNamespace(config=SimpleNamespace(cwd=str(tmp_path)))

def test_load_skill_returns_body(tmp_path):
    _skill(tmp_path, "alpha", "ALPHA BODY")
    tool = LoadSkillTool([tmp_path])
    out = tool.execute({"skill_name": "alpha"}, _env(tmp_path))
    assert out["returncode"] == 0 and "ALPHA BODY" in out["output"]

def test_load_skill_unknown_lists_available(tmp_path):
    _skill(tmp_path, "alpha")
    out = LoadSkillTool([tmp_path]).execute({"skill_name": "ghost"}, _env(tmp_path))
    assert out["returncode"] == 1 and "alpha" in out["output"]

def test_load_skill_duplicate_is_short_circuited(tmp_path):
    _skill(tmp_path, "alpha", "ALPHA BODY")
    env = _env(tmp_path); tool = LoadSkillTool([tmp_path])
    tool.execute({"skill_name": "alpha"}, env)
    out2 = tool.execute({"skill_name": "alpha"}, env)
    assert "already loaded" in out2["output"].lower() and "ALPHA BODY" not in out2["output"]

def test_registry_no_op_without_roots():
    assert [t.name for t in build_registry()] == ["bash", "read", "write", "edit"]

def test_registry_appends_load_skill_with_roots(tmp_path):
    assert "load_skill" in [t.name for t in build_registry(skill_roots=[tmp_path])]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_load_skill.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Write minimal implementation**

Create `harness/tools/load_skill.py`:
```python
"""LoadSkillTool: the agent pulls one skill's full body into context on demand.
Same execute->observation path as Read/Write/Edit (tracing_agent.execute_actions).
Per-turn dedup lives on `env` so a long-lived ACP session doesn't re-inject a
body the agent already has this turn."""

from __future__ import annotations

from pathlib import Path

from harness import skills

LOAD_SKILL_TOOL = {
    "type": "function",
    "function": {
        "name": "load_skill",
        "description": ("Load a skill's full instructions into context. Call this "
                        "before doing work a skill from the # Skills menu governs."),
        "parameters": {
            "type": "object",
            "properties": {
                "skill_name": {"type": "string",
                               "description": "Name of the skill from the # Skills menu."}
            },
            "required": ["skill_name"],
        },
    },
}


class LoadSkillTool:
    name = "load_skill"
    schema = LOAD_SKILL_TOOL

    def __init__(self, roots: list[Path]):
        self._roots = roots
        self._fallback_loaded: set[str] = set()

    def display_label(self, args: dict) -> str:
        return f"load_skill {args.get('skill_name', '')}"

    def _loaded(self, env) -> set:
        loaded = getattr(env, "_loaded_skills", None)
        if loaded is None:
            loaded = self._fallback_loaded
        return loaded

    def execute(self, args: dict, env) -> dict:
        name = args.get("skill_name", "")
        loaded = self._loaded(env)
        if name in loaded:
            return {"output": f"Skill '{name}' is already loaded this turn.",
                    "returncode": 0, "exception_info": None}
        load = skills.compose(self._roots, [name])
        if not load.injected:
            avail = skills.load_catalog(self._roots)
            names = ", ".join(m.name for m in avail) or "(none)"
            return {"output": f"Unknown skill '{name}'. Available: {names}.",
                    "returncode": 1, "exception_info": None}
        loaded.add(name)
        return {"output": load.block, "returncode": 0, "exception_info": None}
```

Modify `harness/tools/registry.py`:
```python
from __future__ import annotations

from pathlib import Path

from harness.tools.base import Tool
from harness.tools.bash import BashTool
from harness.tools.edit import EditTool
from harness.tools.load_skill import LoadSkillTool
from harness.tools.read import ReadTool
from harness.tools.write import WriteTool


def build_registry(skill_roots: list[Path] | None = None) -> list[Tool]:
    tools: list[Tool] = [BashTool(), ReadTool(), WriteTool(), EditTool()]
    if skill_roots:
        tools.append(LoadSkillTool(skill_roots))
    return tools
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_load_skill.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add harness/tools/load_skill.py harness/tools/registry.py tests/test_load_skill.py
git commit -m "feat(tools): load_skill tool + opt-in registry wiring"
```

### Task B3: per-turn `_loaded_skills` reset on the engine env

**Files:**
- Modify: `harness/tracing_agent.py` (set `env._loaded_skills = set()` at the start of each prompt/turn)
- Test: `tests/test_tui_trace_write.py` or a focused `tests/test_load_skill.py` integration

**Interfaces:**
- Consumes: `LoadSkillTool._loaded(env)` reads `env._loaded_skills`.
- Produces: a fresh `env._loaded_skills` set per agent run so dedup is turn-scoped, not session-lifetime.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_load_skill.py`:
```python
def test_loaded_set_is_reset_between_turns(tmp_path):
    _skill(tmp_path, "alpha", "ALPHA BODY")
    env = _env(tmp_path); tool = LoadSkillTool([tmp_path])
    env._loaded_skills = set()
    tool.execute({"skill_name": "alpha"}, env)
    # simulate a new turn resetting the env slot
    env._loaded_skills = set()
    out = tool.execute({"skill_name": "alpha"}, env)
    assert "ALPHA BODY" in out["output"]   # reloads after reset
```

- [ ] **Step 2: Run test to verify it fails (or passes trivially) then wire the engine**

Run: `.venv/bin/python -m pytest tests/test_load_skill.py -k reset -v`
Expected: PASS at the unit level (proves the contract). Then wire the reset: locate the per-prompt entry in `tracing_agent.py` (the `run`/`step` entry that starts a turn) and set `self.env._loaded_skills = set()` there. Confirm via grep that `run(` resets it.

- [ ] **Step 3: Implement the reset**

In `harness/tracing_agent.py`, in the method that begins a turn (the public `run`/equivalent entry — confirm the name in the file), add near the top:
```python
        # load_skill dedup is per-turn: fresh set each prompt so a long-lived
        # ACP session can re-pull a skill on a later turn.
        try:
            self.env._loaded_skills = set()
        except Exception:
            pass
```

- [ ] **Step 4: Run full suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS (>=711 with new tests).

- [ ] **Step 5: Commit**

```bash
git add harness/tracing_agent.py tests/test_load_skill.py
git commit -m "feat(engine): reset load_skill dedup per turn"
```

### Task B4: base_prompt menu + dispatch wiring (hybrid: menu + pre-seed)

**Files:**
- Modify: `harness/base_prompt.py` (add `skills_menu`)
- Modify: `harness/persona.py` (`TurnContext.skills_menu`, `compose_context` builds menu)
- Modify: `harness/run_traced.py`, `harness/acp_agent.py`, `harness/acp_main.py` (pass `skill_roots` to `build_registry`; pass `skills_menu` to `render_base_prompt`)
- Test: `tests/test_base_prompt.py`, `tests/test_run_traced.py`, `tests/test_acp_session_context.py`

**Interfaces:**
- Consumes: `compose_menu` (B1), `build_registry(skill_roots=...)` (B2).
- Produces: `render_base_prompt(..., skills_menu: str | None = None)`; `TurnContext.skills_menu: str`; dispatch builds the flow-scoped menu and passes it. Pre-seed: router-selected (high-confidence) skills still eager-composed into `skill_block` via existing `compose`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_base_prompt.py`:
```python
def test_base_prompt_omits_menu_when_none():
    a = render_base_prompt(model_id="m", cwd="/x", system_line="os")
    b = render_base_prompt(model_id="m", cwd="/x", system_line="os", skills_menu=None)
    assert a == b and "# Skills" not in a       # byte-identical no-op

def test_base_prompt_appends_menu():
    out = render_base_prompt(model_id="m", cwd="/x", system_line="os",
                             skills_menu="\n\n# Skills\n\n- **a** — d")
    assert out.endswith("- **a** — d") and "# Skills" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_base_prompt.py -k menu -v`
Expected: FAIL — unexpected `skills_menu` kwarg.

- [ ] **Step 3: Write minimal implementation**

`harness/base_prompt.py` — add param + append (after the persona block, before return):
```python
def render_base_prompt(*, model_id: str, cwd: str, system_line: str,
                       cutoff: str = KNOWLEDGE_CUTOFF,
                       persona_id: str | None = None,
                       persona_dir: str | None = None,
                       skills_menu: str | None = None) -> str:
    ...
    return BASE_POLICY + env + persona + (skills_menu or "")
```

`harness/persona.py` — add `skills_menu: str = ""` to `TurnContext`; in `compose_context`, after composing skills, build the menu from the scoped catalog. Minimal version (menu of injected skills' metas — full flow scope arrives in Layer C):
```python
@dataclass
class TurnContext:
    persona_block: str = ""
    memory_block: str = ""
    skill_block: str = ""
    skills_menu: str = ""
    skills: "skills.SkillLoad" = field(default_factory=lambda: skills.SkillLoad())

def compose_context(persona_block, memory_block, skill_roots, skill_names,
                    menu_metas=None):
    skill_load = skills.compose(skill_roots, skill_names)
    menu = skills.compose_menu(menu_metas) if menu_metas else ""
    return TurnContext(persona_block=persona_block, memory_block=memory_block,
                       skill_block=skill_load.block, skills_menu=menu, skills=skill_load)
```

Dispatch sites:
- `run_traced._build_vibeproxy_model` and the ACP model build: change `build_registry()` → `build_registry(skill_roots=skills_roots)` (the roots already resolved via `paths.skills_dirs()`).
- `run_traced.py` and `acp_agent.py` agent path: compute `menu_metas = skills.load_catalog(skills_roots)` (Layer C scopes this), pass to `compose_context`, and pass `skills_menu=ctx.skills_menu` into `render_base_prompt`.

- [ ] **Step 4: Run targeted + full suite**

Run: `.venv/bin/python -m pytest tests/test_base_prompt.py tests/test_run_traced.py tests/test_acp_session_context.py -v`
Then: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS. Existing dispatch tests must stay green (menu append is additive; registry gains load_skill only because roots are now passed — confirm no test asserts an exact 4-tool registry from the dispatch path; if one does, update it to expect load_skill, documenting the intentional change).

- [ ] **Step 5: Commit**

```bash
git add harness/base_prompt.py harness/persona.py harness/run_traced.py harness/acp_agent.py harness/acp_main.py tests/
git commit -m "feat: hybrid lazy skills — menu in prompt + load_skill registered in dispatch"
```

---

## LAYER C — Flows + ask-done + curated spine

### Task C1: `read_flows` in persona_config

**Files:**
- Modify: `harness/persona_config.py`
- Test: `tests/test_persona_config.py`

**Interfaces:**
- Produces: `read_flows(workspace_dir: Path | None) -> list[str]` — best-effort, mirrors `read_skills`; missing/corrupt/ill-typed → `[]`.

- [ ] **Step 1: Write the failing test**

```python
from harness.persona_config import read_flows

def test_read_flows_happy(tmp_path):
    (tmp_path / "persona.toml").write_text('flows = ["seo", "marketing"]\n')
    assert read_flows(tmp_path) == ["seo", "marketing"]

def test_read_flows_absent_or_garbage(tmp_path):
    assert read_flows(tmp_path) == []                 # no file
    (tmp_path / "persona.toml").write_text('flows = "nope"\n')
    assert read_flows(tmp_path) == []                 # not a list
    assert read_flows(None) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_persona_config.py -k flows -v`
Expected: FAIL — no `read_flows`.

- [ ] **Step 3: Write minimal implementation**

Add to `harness/persona_config.py` (mirror `read_skills`):
```python
def read_flows(workspace_dir: Path | None) -> list[str]:
    """Flow families this persona enables, from <workspace_dir>/persona.toml
    `flows`. [] when the dir/file is absent, unreadable, corrupt, or the key is
    missing or not a list of strings (== no flow gating: all global skills)."""
    if workspace_dir is None:
        return []
    path = workspace_dir / PERSONA_TOML
    try:
        raw = path.read_bytes()
    except OSError:
        return []
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        return []
    flows = data.get("flows")
    if not isinstance(flows, list):
        return []
    return [f for f in flows if isinstance(f, str)]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_persona_config.py -k flows -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/persona_config.py tests/test_persona_config.py
git commit -m "feat(persona): read_flows from persona.toml"
```

### Task C2: `flows.py` — scope_catalog + render_map

**Files:**
- Create: `harness/flows.py`
- Test: `tests/test_flows.py`

**Interfaces:**
- Consumes: `list[SkillMeta]` (A2).
- Produces: `scope_catalog(metas, enabled_flows) -> list[SkillMeta]`; `render_map(metas, enabled_flows) -> str`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_flows.py`:
```python
from harness.skills import SkillMeta
from harness.flows import scope_catalog, render_map

A = SkillMeta("a", "global")                       # no flow -> always in
B = SkillMeta("b", "seo skill", flows=("seo",))
C = SkillMeta("c", "mktg skill", flows=("marketing",))

def test_scope_keeps_global_and_enabled_only():
    out = scope_catalog([A, B, C], ["seo"])
    assert {m.name for m in out} == {"a", "b"}

def test_scope_empty_flows_keeps_only_global():
    assert {m.name for m in scope_catalog([A, B, C], [])} == {"a"}  # gating on

def test_render_map_groups_and_lists():
    out = render_map([A, B], ["seo"])
    assert "seo" in out and "**b**" in out and "**a**" in out
```
Note the deliberate semantics: enabling a flow scopes to global + that flow. With `[]` enabled, the dispatch layer (C3) decides whether to gate; `scope_catalog([], )` itself with `enabled=[]` returns only globals. (Dispatch passes the full catalog when a persona sets no flows — see C3 — preserving the no-op.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_flows.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Write minimal implementation**

Create `harness/flows.py`:
```python
"""Flows: a flow is a named family of skills (by SkillMeta.flows tag). Enabling
flows per-persona (persona.toml) scopes which skills the router/agent see, so the
context stays lean as skill families grow. Pure functions; data-driven dispatch —
new flow families need no router edits."""

from __future__ import annotations

from harness.skills import SkillMeta


def scope_catalog(metas: list[SkillMeta], enabled_flows: list[str]) -> list[SkillMeta]:
    """Keep global skills (no flow tag) plus skills in an enabled flow."""
    enabled = set(enabled_flows)
    return [m for m in metas if not m.flows or (set(m.flows) & enabled)]


def render_map(metas: list[SkillMeta], enabled_flows: list[str]) -> str:
    """The /ask-done narrative: skills grouped by flow (global first), each with
    its description; user-only skills marked with /name."""
    scoped = scope_catalog(metas, enabled_flows)
    groups: dict[str, list[SkillMeta]] = {}
    for m in scoped:
        key = m.flows[0] if m.flows else "general"
        groups.setdefault(key, []).append(m)
    out = ["# Flows and skills\n"]
    for flow in sorted(groups):
        out.append(f"\n## {flow}\n")
        for m in sorted(groups[flow], key=lambda x: x.name):
            tag = " (use /{0})".format(m.name) if not m.model_invocable else ""
            out.append(f"- **{m.name}** — {m.description}{tag}")
    return "\n".join(out)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_flows.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/flows.py tests/test_flows.py
git commit -m "feat(flows): scope_catalog + render_map (data-driven flow scoping)"
```

### Task C3: wire flow scoping into dispatch

**Files:**
- Modify: `harness/acp_agent.py`, `harness/run_traced.py`
- Test: `tests/test_acp_session_context.py`, `tests/test_run_traced.py`

**Interfaces:**
- Consumes: `persona_config.read_flows` (C1), `flows.scope_catalog` (C2), `compose_menu` (B1).
- Produces: the router catalog AND the menu are flow-scoped when the persona sets `flows`; full catalog (no gating) when `flows == []` (no-op).

- [ ] **Step 1: Write the failing test**

Add an integration test asserting: with a persona whose `persona.toml` has `flows = ["seo"]`, a skill tagged `flow: marketing` is absent from the menu/catalog; with no `flows`, all skills present. (Use the existing ACP/run_traced test harness fixtures; assert on the composed menu string or `Router` catalog passed.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_run_traced.py -k flow -v`
Expected: FAIL — scoping not wired.

- [ ] **Step 3: Write minimal implementation**

In each dispatch entry, before building the router catalog / menu:
```python
enabled_flows = persona_config.read_flows(workspace_dir)   # [] for non-persona / no flows
full_catalog = skills.load_catalog(skills_roots)
scoped = flows.scope_catalog(full_catalog, enabled_flows) if enabled_flows else full_catalog
# Router gets `scoped`; menu_metas = scoped
```
Pass `scoped` to `Router(catalog=...)` and as `menu_metas` to `compose_context`. When `enabled_flows == []`, `scoped is full_catalog` → identical to today (no-op).

- [ ] **Step 4: Run full suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS. No-op holds for the default persona until C4 enables `engineering`.

- [ ] **Step 5: Commit**

```bash
git add harness/acp_agent.py harness/run_traced.py tests/
git commit -m "feat(dispatch): flow-scope the router catalog + menu per persona"
```

### Task C4: curated maturity spine — vendored skills + re-tag + ask-done + default persona flow

**Files:**
- Create: `harness/skills/clarify-before-acting/SKILL.md`, `harness/skills/plan-review/SKILL.md`, `harness/skills/review-before-done/SKILL.md`, `harness/skills/reflect-and-learn/SKILL.md`, `harness/skills/ask-done/SKILL.md`
- Modify: existing `harness/skills/{systematic-debugging,test-driven-development,receiving-code-review,verification-before-completion}/SKILL.md` — add `flow: engineering` (sharpen systematic-debugging with the Iron Law 3-strike rule; keep others)
- Modify: `harness/skills/NOTICE.md` (attribution); seed default persona `persona.toml` with `flows = ["engineering"]`
- Test: `tests/test_skills.py` (spine parses + tagged), `tests/test_flows.py` (ask-done is disable-model-invocation)

**Interfaces:**
- Produces: the default `engineering` flow's skills, all `flow: engineering`, model+user invocable except `ask-done` (`disable-model-invocation: true`).

- [ ] **Step 1: Write the failing test**

```python
def test_spine_skills_present_and_tagged(tmp_path):
    from harness import paths, skills
    cat = {m.name: m for m in skills.load_catalog(paths.skills_dirs())}
    for n in ["clarify-before-acting", "plan-review", "review-before-done",
              "reflect-and-learn", "systematic-debugging",
              "test-driven-development", "ask-done"]:
        assert n in cat, n
    assert "engineering" in cat["clarify-before-acting"].flows
    assert cat["ask-done"].model_invocable is False        # user-only
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_skills.py -k spine -v`
Expected: FAIL — new skills don't exist.

- [ ] **Step 3: Write the skill files**

Each new `SKILL.md` frontmatter `name`/`description`/`flow: engineering` (+ `disable-model-invocation: true` for ask-done). Bodies adapted (re-authored, not copied) from the cited sources:
- `clarify-before-acting` — forcing questions that separate a *question* from a *work order*; answer/scope before editing; push back on framing. (GStack /office-hours + Matt grill-with-docs.) **The answer-vs-act gate.**
- `plan-review` — before code: architecture, data flow, failure modes, edge cases, trust boundaries, test matrix; "draw the system — diagrams force hidden assumptions into the open." (GStack /plan-eng-review.)
- `review-before-done` — find bugs that pass CI but blow up in prod; imagine the production incident; no flattery; flag completeness gaps; references (not duplicates) `receiving-code-review` + `verification-before-completion`. (GStack /review.)
- `reflect-and-learn` — capture durable, confidence-scored, file-attributed lessons via the existing persona-memory write path; apply prior insight on future turns. (GStack /learn + our memory.)
- `ask-done` — `disable-model-invocation: true`; instruct the model to read the flow map (rendered into context on invocation) and recommend a flow/skill/next step. (Matt /ask-matt.)
Re-tag the 4 existing skills with `flow: engineering`; sharpen `systematic-debugging` with the Iron Law + "stop after 3 failed fixes, question the architecture." Update `NOTICE.md` to attribute garrytan/gstack + mattpocock/skills as inspiration (re-authored, not redistributed). Seed default `persona.toml` `flows = ["engineering"]`.

- [ ] **Step 4: Run test + full suite**

Run: `.venv/bin/python -m pytest tests/test_skills.py -k spine -v && .venv/bin/python -m pytest tests/ -q`
Expected: PASS. (Default persona now enables `engineering`; assert the menu lists the spine.)

- [ ] **Step 5: Commit**

```bash
git add harness/skills/ tests/
git commit -m "feat(skills): curated maturity spine + ask-done; default persona enables engineering flow"
```

---

## DOCS + FINALIZE

### Task D1: docs + final verification + PR

**Files:**
- Create: `docs/router-flows.md` (how the system works)
- Test: full suite + manual smoke

- [ ] **Step 1: Write `docs/router-flows.md`** — explain: the invocation model (disable-model-invocation/user-invocable/flow), the hybrid lazy-discovery runtime (router scopes flow → menu → load_skill pull), per-persona flows in persona.toml, the curated spine + how to add a flow (tag skills + enable in persona.toml + optional map), and the no-op guarantee. Include a diagram of the dispatch flow.

- [ ] **Step 2: Full suite green**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS, count > 706.

- [ ] **Step 3: Smoke the agent path** — run a mock-model dispatch (`dn --model mock` or the test entry) and confirm the base prompt contains `# Skills` and `load_skill` is in the tool list; confirm a `disable-model-invocation` skill is absent from the router's selectable set.

- [ ] **Step 4: Final review pass** — request a code review (requesting-code-review) and a caveman-review on the diff for terse signal; fold fixes.

- [ ] **Step 5: Commit docs + open PR**

```bash
git add docs/router-flows.md
git commit -m "docs: how the router/flows/lazy-skill system works"
git push -u origin router-flows
gh pr create --title "Expandable router: flows + lazy skill discovery + maturity spine" --body "..."
```

---

## Self-Review

**Spec coverage:** Layer A (A1-A3) = invocation model + structured catalog + router filter. Layer B (B1-B4) = menu + load_skill + per-turn reset + dispatch wiring (hybrid). Layer C (C1-C4) = read_flows + scope/map + dispatch scoping + curated spine + ask-done. Docs = D1. All spec sections mapped.

**Placeholder scan:** No TBD/TODO. Every code step shows code. Skill bodies (C4 Step 3) are described by adapted-source + gate rather than full prose — acceptable because authoring long markdown bodies is content work, not code; the gate + source per skill is unambiguous. The PR body `"..."` is filled at D1 Step 5.

**Type consistency:** `SkillMeta` fields (name, description, model_invocable, user_invocable, flows) used identically in A1/A2/A3/B1/C2/C3. `compose_menu(metas)`, `build_registry(skill_roots=...)`, `read_flows(workspace_dir)`, `scope_catalog(metas, enabled_flows)`, `render_map(metas, enabled_flows)` consistent across tasks. `TurnContext.skills_menu` defined B4, consumed B4 dispatch.

**Risk folded from Codex/self-verification:** tuple→SkillMeta migration is exactly 3 unpack sites (router.py:80,99; chat_handler.py:46) — handled in A3. load_skill execute→observation path verified (tracing_agent.py:188,194). Per-turn dedup home = `env._loaded_skills`, reset in B3. No-op asserted at end of each layer.
