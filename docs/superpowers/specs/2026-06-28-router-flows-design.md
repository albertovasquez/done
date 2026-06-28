# Design spec: expandable router, pluggable flows, lazy skill discovery, curated maturity spine

**Date:** 2026-06-28
**Branch:** `router-flows`
**Status:** Design — for Codex adversarial review, then implementation plan.
**Supersedes/builds on:** `2026-06-28-router-flows-research.md` (the research brief). Read that for the current-state map and external grounding; this spec is the buildable design.

## Goal (restated)

Re-architect the router so the harness is **robust and expandable**: new flow families (SEO, marketing, copywriting, …) arrive as *data*, not router edits; skills are **discovered and pulled on demand** instead of all injected as context; and the **default harness ships a curated, battle-tested "maturity spine"** of general skills that make the model reframe-before-acting, plan, investigate-before-fixing, review-before-done, and reflect — a professional flow. Personas and user-added skills layer on top; the default set is the focus here.

Two named sources are mined for the curated content (adapted, vendored — not depended on): **GStack** (`garrytan/gstack` — role-based sprint pipeline, 23 skills) and **Matt Pocock's skills** (`mattpocock/skills` — flow-based, doc-building, `ask-matt` router). Both independently converge on the same shape: *force thinking and planning before doing, with explicit gates*. That convergence is the design's backbone.

## Decisions locked (rationale)

| Decision | Choice | Why |
|---|---|---|
| Runtime discovery model | **Hybrid**: cheap router selects the *flow* + seeds a menu; worker agent *pulls skill bodies on demand* via `load_skill` | Keeps the cheap scoping the codebase is built around; gets progressive-disclosure context economy where it pays; handles mid-task "I also need X". |
| Default skill thesis | **Lean maturity spine (~7 skills)**, bodies adapted from GStack + Matt's battle-tested prose | Maximizes professional-flow maturity at minimal context cost — the whole point of lazy discovery. |
| Provenance | **Vendored** into `harness/skills/` with our own frontmatter | Matches "part of the system itself"; versions with the harness; no external drift/licensing surface. Attribution in `harness/skills/NOTICE.md`. |
| Invocation model | `disable-model-invocation` + `user-invocable` (2-axis), `flow` tag; `allowed-tools`/`context:fork` deferred | Anchor primitive the user named, generalized to the proven Anthropic matrix; defers fields that need subagent plumbing. |
| Flow config home | `persona.toml` via new `read_flows()` | `persona.toml` is already the non-model per-persona config and already lists skill roots; flows mirror that exactly. NOT `done.conf` (model-only). |

## Non-goals (YAGNI)

- `allowed-tools` and `context: fork`/subagent skill execution — designed-for but not built now (noted as forward-compatible frontmatter).
- Cross-model benchmarking, browser/QA/deploy/canary skills (GStack-infra-specific, not general).
- Reworking persona/memory resolution — untouched; flows are additive to it.
- A TUI surface for flows/ask-done beyond what already exists — out of scope; CLI/agent behavior first.

---

## Architecture overview

Three layers, each independently shippable and a **strict no-op until a skill/persona opts in** (consistent with how persona/memory/yolo shipped). Data flows:

```
prompt
  │
  ▼
Router.classify ── reads STRUCTURED catalog (name, desc, invocation flags, flow tags)
  │                 • picks task_type
  │                 • selects ACTIVE FLOW (scopes which skills are visible)
  │                 • may pre-seed high-confidence skills (advisory)
  ▼
dispatch ── chat / clarify / agent  (unchanged branch shape)
  │
  ▼ (agent path)
compose menu ── skill NAMES + DESCRIPTIONS for in-flow, model-invocable skills
  │            (bodies NOT injected up front)
  ▼
agent system prompt = base + persona + memory + MENU + base policy on load_skill
  │
  ▼
agent runs ── calls load_skill(name) to pull a body into context ONLY when needed
              (router-preseeded skills can be auto-loaded; rest are pull)
```

`/ask-done` is the human entrance: a `disable-model-invocation: true` skill that renders the enabled flows' map for "what fits here?".

---

## Layer A — Skill invocation model (foundation)

**Files:** `harness/skills.py` (primary), `tests/test_skills.py`.

### A.1 Structured skill metadata

Replace the flat catalog tuple with a structured record. New module-level dataclass in `skills.py`:

```python
@dataclass(frozen=True)
class SkillMeta:
    name: str
    description: str
    model_invocable: bool = True      # router may auto-select; False == disable-model-invocation
    user_invocable: bool = True       # exposed as /name
    flows: tuple[str, ...] = ()       # flow families this skill belongs to ("" / absent = global)
```

Frontmatter keys parsed (all optional; defaults preserve today's behavior exactly):
- `disable-model-invocation: true` → `model_invocable = False`
- `user-invocable: false` → `user_invocable = False`
- `flow: <str>` or `flows: [<str>, ...]` → `flows`
- (forward-compat, parsed-and-ignored for now: `allowed-tools`, `context`, `agent`)

`_parse_skill_md` is unchanged (already returns the full dict). A new pure helper `_meta_from_frontmatter(data: dict, fallback_name: str) -> SkillMeta` centralizes the field reads with safe coercion (a non-bool `disable-model-invocation` degrades to default; a scalar `flow` becomes a 1-tuple; a non-list `flows` → `()`).

### A.2 Catalog becomes structured

`load_catalog(roots) -> list[SkillMeta]` (was `list[tuple[str,str]]`). Same scan/precedence/skip-on-error logic; returns `SkillMeta` sorted by name.

**Backward-compat shim (critical — many call sites consume tuples):** keep the tuple shape available. Two options evaluated; chosen: **add `catalog_pairs(metas) -> list[tuple[str,str]]`** and have the Router/ChatHandler accept `list[SkillMeta]` while internally deriving pairs where they only need name+desc. This avoids a big-bang change to every consumer in one commit. The Router gains a filter: `model_invocable` skills only are offered for auto-selection; dormant skills still appear in the user-facing/`ask-done` listing.

### A.3 Router filtering

`router.py` `_system_prompt(catalog)` now receives `SkillMeta`. Two changes:
1. Only `model_invocable` skills are listed as *selectable* by the router (dormant ones are never auto-picked — the `disable-model-invocation` guarantee).
2. When flows are active (Layer C), the catalog handed to the router is already flow-scoped, so the prompt naturally shrinks.

`Classification.skills` semantics unchanged (list of selected names); the router simply can't select a non-model-invocable skill.

### A.4 No-op proof

The 4 bundled skills carry no new frontmatter → all default to `model_invocable=True, user_invocable=True, flows=()` → catalog content identical to today (modulo type). Tests assert byte-identical router prompt for an all-default catalog.

---

## Layer B — Lazy discovery + `load_skill` (the payoff, hybrid)

**Files:** `harness/skills.py`, `harness/tools/load_skill.py` (new), `harness/tools/registry.py`, `harness/base_prompt.py`, dispatch sites (`run_traced.py`, `acp_agent.py`, `persona.py`), tests.

### B.1 The menu (cheap context)

New `skills.compose_menu(metas: list[SkillMeta]) -> str`: renders an `# Available skills` section of **names + one-line descriptions only** (no bodies), plus a one-line instruction: *"Load a skill's full instructions with the `load_skill` tool before doing work it governs."* This is what Anthropic keeps in context; bodies stay on disk.

`compose()` (eager body injection) is **retained** for the pre-seed path (router high-confidence picks) but is no longer the default for the whole catalog. The menu replaces the "dump every selected body" behavior.

### B.2 `load_skill` tool

New tool in the registry (`tools/registry.py` `build_registry()` gains `LoadSkillTool(roots)`):

```python
class LoadSkillTool:
    name = "load_skill"
    schema = {...: {"skill_name": str (required)}}
    def __init__(self, roots): self._roots = roots
    def display_label(self, args): return f"load_skill({args.get('skill_name')})"
    def execute(self, args, env) -> dict:
        # reuse skills.compose(roots, [name]) → return the composed body as output
        # guardrails below
```

- Reuses `skills.compose(self._roots, [name])` to read+format one body (single source of formatting).
- Returns `{"output": <body or error>, "returncode": 0/1, "exception_info": None}` per the Tool protocol.
- **Guardrails (anti over-pull):**
  - Unknown name → returns a helpful error listing available names (no crash).
  - Already-loaded names tracked per turn (the tool instance or env carries a `set`); a repeat load returns a short "already loaded this turn" note instead of re-injecting the full body.
  - A `user_invocable=False`-and-`model_invocable=False` skill cannot be loaded by the agent (defensive; shouldn't be in the menu anyway).
  - Optional soft budget: log when >N skills loaded in one turn (telemetry, not a hard block) — surfaces over-pull without breaking flow.

**Registry wiring:** `build_registry()` currently takes no args. It gains an optional `skill_roots: list[Path] | None = None`; when provided, appends `LoadSkillTool`. Call sites (`run_traced._build_vibeproxy_model`, the ACP model build) pass `paths.skills_dirs()` (+ persona roots). When `None`, registry is exactly today's `[Bash, Read, Write, Edit]` (no-op for any caller that doesn't opt in).

### B.3 Base prompt teaches the mechanism

`render_base_prompt` gains an optional `skills_menu: str | None = None`. When present, appends a `# Skills` section: the menu + the standing rule *"Skills are loaded on demand. Before doing work a skill governs, call `load_skill(name)` to read its full instructions. Don't load skills you won't use."* Pure/omit-when-absent (same pattern as the persona section). When `None`, base prompt is byte-identical to today.

### B.4 Hybrid dispatch

In the agent path (`run_traced.py` and `acp_agent.py`):
- Router selects flow + (optional) high-confidence skill names.
- Build `menu = compose_menu(flow_scoped_metas)`; pass to `render_base_prompt(skills_menu=menu)`.
- Pre-seed: for high-confidence selected skills, still eager-`compose()` their bodies into `skill_block` (so the agent doesn't have to round-trip for the obvious one). Everything else is pull-only via `load_skill`.
- `persona.compose_context` extended: it already composes the skill block; it gains the menu + pre-seed split. `TurnContext` gains `skills_menu: str`.

**Net effect:** a flow with 40 skills costs ~40 description lines + the 0–2 pre-seeded bodies, not 40 bodies. Exactly the asked-for property.

### B.5 No-op proof

`build_registry()` with no roots, `render_base_prompt` with no menu, and `compose_context` with an empty/global catalog all reduce to current behavior. Tests assert the registry list, base prompt, and composed block are unchanged when nothing opts in.

---

## Layer C — Pluggable flows + `ask-done` + curated spine

**Files:** `harness/persona_config.py` (add `read_flows`), `harness/skills.py` (flow scoping helper), `harness/flows.py` (new, small — flow resolution + map rendering), the curated `harness/skills/*` bodies, `harness/skills/ask-done/SKILL.md` (new), `harness/skills/NOTICE.md` (attribution), tests.

### C.1 What a flow is

A **flow** = a named family of skills, defined purely by data:
- Membership: a skill's `flow:`/`flows:` frontmatter (and/or living in a `skills/<flow>/` subdir — both supported; frontmatter is canonical).
- A flow is **enabled** per-persona via `persona.toml`. Skills tagged to a *disabled* flow are not shown to the router/agent (kept lean); global skills (no flow tag) are always available.
- Optional curated **map**: an `ask-matt`-style narrative (main flow → on-ramps → standalone) rendered by `/ask-done`. Derived from frontmatter where possible; minimal hand-prose to avoid drift.

### C.2 `persona.toml` gains `flows`

`persona_config.read_flows(workspace_dir) -> list[str]` (mirrors `read_skills`/`read_name` exactly: best-effort, missing/corrupt/ill-typed → `[]`). Example:

```toml
name = "Copywriter"
flows = ["copywriting", "seo"]      # NEW: which flow families this persona enables
skills = ["~/my-extra-skills"]      # existing: extra skill roots
```

`[]` (or absent) = **default behavior: all global skills, no flow gating** → no-op for existing personas. The default persona enables the `engineering` flow (where the spine lives) via its seeded `persona.toml`.

### C.3 Flow resolution

`flows.py`:
- `scope_catalog(metas, enabled_flows) -> list[SkillMeta]`: keep skills whose `flows` is empty (global) OR intersects `enabled_flows`. Pure, tested.
- `render_map(metas, enabled_flows) -> str`: the `/ask-done` narrative from the scoped metas (groups by flow, lists name+desc, marks user-invocable-only with `/name`).
- Dispatch threads `enabled_flows` from `persona.toml` → `scope_catalog` before building the router catalog and the menu.

### C.4 `/ask-done` skill

`harness/skills/ask-done/SKILL.md`, frontmatter `disable-model-invocation: true`, `user-invocable: true`. Body: instructs the model to read the rendered flow map (passed in context when the skill is invoked) and recommend a flow / skill / next step — the `ask-matt` pattern, over *our* flows. Because it's `disable-model-invocation`, the router never auto-runs it; the user calls `/ask-done`.

### C.5 The curated maturity spine (default `engineering` flow)

Seven general skills, vendored with our frontmatter. Each is adapted (re-authored in our voice, not copy-pasted) from the cited source's battle-tested philosophy. **The harness already ships 4 of these conceptually** — those are kept/sharpened, not duplicated.

| Skill (ours) | Gate it enforces | Adapted from | Core principle (carried over) |
|---|---|---|---|
| `clarify-before-acting` | **Answer-vs-act** — distinguish a *question* from a *work order*; answer/scope before editing | GStack `/office-hours` (forcing questions, reframe before code) + Matt `grill-with-docs` (interview) | Push back on framing; don't write code to answer a question. **This is the fix for "dn jumps to work."** |
| `brainstorming` *(reuse superpowers if present, else vendor)* | **Plan before building** | superpowers brainstorming + GStack `/spec` | Design → approval before implementation. |
| `plan-review` | **Architecture/edge-cases/tests surfaced before code** | GStack `/plan-eng-review` | "Diagrams force hidden assumptions into the open"; name failure modes, trust boundaries, test matrix. |
| `systematic-debugging` *(existing — sharpen)* | **Investigate before fixing** | GStack `/investigate` Iron Law + existing skill | No fix without root cause; trace data flow; **stop after 3 failed fixes** and question architecture. |
| `test-driven-development` *(existing — keep)* | **Test-first build** | existing | RED→GREEN→REFACTOR. |
| `review-before-done` | **Bugs that pass CI** caught before "done"; completeness gaps flagged | GStack `/review` + existing `receiving-code-review` + `verification-before-completion` | "Imagine the production incident before it happens. No flattery." Evidence before claiming done. |
| `reflect-and-learn` | **Capture durable lessons** across sessions | GStack `/learn` + our existing memory system | Confidence-scored, file-attributed learnings; future turns apply prior insight. (Integrates with the harness's existing persona memory rather than a parallel store.) |

Notes:
- `clarify-before-acting`, `plan-review`, `review-before-done`, `reflect-and-learn` are **new** vendored bodies. `systematic-debugging`, `test-driven-development`, `receiving-code-review`, `verification-before-completion` already exist — we keep them, fold `review-before-done` to reference rather than duplicate them, and re-tag all with `flow: engineering`.
- All spine skills are **model-invocable** (the router/agent can pull them) and `user-invocable` (also `/name`). Only `ask-done` is `disable-model-invocation`.
- Bodies stay <500 lines; long reference moves to sibling files (progressive disclosure within a skill).

### C.6 Attribution

`harness/skills/NOTICE.md` updated: these skills are adaptations inspired by `garrytan/gstack` and `mattpocock/skills`; link both; note they are re-authored, not redistributed.

---

## How the goals are met (traceability)

| Goal | Mechanism |
|---|---|
| Robust/expandable router | Flows are data (frontmatter tag + `persona.toml` line + optional map). New family = add skills + tag + enable. **No `task_type` enum or dispatch edits.** |
| Skills not all in context | Lazy menu + `load_skill` pull (Layer B). Flow scoping (Layer C) shrinks the menu itself. |
| Discoverability ("ask-matt-docs entrance") | `/ask-done` renders the flow map; the agent menu makes skills self-describing. |
| Maturity / professional flow | Curated spine enforces reframe→plan→investigate→test→review→reflect, adapted from two proven systems. |
| "dn jumps to work on a question" | `clarify-before-acting` gate + answer-first menu (no eager bodies forcing the work frame). |
| Personas/user skills still work | `persona.toml` `flows`/`skills` additive; defaults = today's behavior. |

---

## Risks & mitigations

- **Big consumer churn from tuple→SkillMeta.** Mitigation: `catalog_pairs()` shim; migrate consumers behind the structured type incrementally; tests pin each call site.
- **Agent over-pulls skills.** Mitigation: per-turn already-loaded set, helpful errors, soft budget telemetry, and the standing "don't load skills you won't use" rule.
- **Map ↔ frontmatter drift.** Mitigation: derive the map from metas; keep hand-prose minimal.
- **Backward compatibility.** Mitigation: every layer proven no-op for all-default skills + personas with no `flows`. The 4 existing skills must pass unchanged; assert it.
- **Editable-install shadowing** (known trap). Mitigation: run the worktree suite with the worktree venv + worktree cwd (already set up; 706 baseline green).
- **Reflect-and-learn vs existing memory.** Mitigation: it *uses* the existing persona-memory write path; it is a skill that guides capture, not a second store. No new persistence system.

## Test strategy

- Layer A: frontmatter parsing matrix (each flag, coercions, missing/garbage); catalog structured output; router can't select dormant skill; no-op router prompt for all-default catalog.
- Layer B: `load_skill` happy/unknown/duplicate/dormant; menu rendering; base-prompt menu append/omit; registry no-op without roots; dispatch builds menu + pre-seed correctly.
- Layer C: `read_flows` best-effort matrix; `scope_catalog` (global vs tagged vs disabled); `render_map`; `/ask-done` frontmatter is `disable-model-invocation`; default persona enables `engineering`; spine skills parse and are flow-tagged.
- Integration: a flow with many skills injects menu (not bodies); pre-seeded skill present; `load_skill` pulls a non-preseeded body; existing-skill suites stay green.
- Full suite green (`.venv/bin/python -m pytest tests/ -q`) at each layer boundary.

## Rollout sequencing

A (foundation) → B (payoff) → C (flows + spine + ask-done) → docs. Each merged-or-mergeable independently; the no-op invariant means partial rollout is safe.
