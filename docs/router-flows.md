# Router, flows, and lazy skill discovery

How the harness decides what a request needs, which skills it sees, and when their
instructions actually enter the model's context. This replaces the old "router
picks skills → inject all their bodies" model with **progressive disclosure**: the
agent gets a cheap menu and pulls full skill bodies on demand.

## The three moving parts

| Part | Lives in | Job |
|------|----------|-----|
| **Invocation model** | `harness/skills.py` (`SkillMeta`) | Each skill declares how it may be invoked and what flow it belongs to. |
| **Lazy discovery** | `harness/skills.py` (`compose_menu`), `harness/tools/load_skill.py` | The agent sees a menu (names + descriptions) and pulls a body only when it needs it. |
| **Flows** | `harness/flows.py`, `harness/persona_config.py` (`read_flows`) | A named family of skills, enabled per-persona, that scopes what the router and agent see. |

## 1. The invocation model

Every `SKILL.md` carries YAML frontmatter. Beyond `name` and `description`, three
optional fields control invocation (defaults reproduce the pre-flows behavior, so
existing skills need no changes):

```yaml
---
name: deploy
description: Deploy the application to production
disable-model-invocation: true    # the model may NOT auto-pick this; user/explicit only
user-invocable: false             # not exposed as /name
flow: ops                         # belongs to the "ops" flow (or: flows: [ops, release])
---
```

- **`disable-model-invocation: true`** → the router never auto-selects it. The
  skill still appears in the menu so the model *knows it exists*, but only the
  user (or an explicit step) can run it. Use it for side-effecting or
  timing-sensitive work. `ask-done` ships with this set.
- **`user-invocable: false`** → parsed metadata for user-facing command
  surfaces. The current TUI slash menu is hand-written and does not auto-expose
  skills as slash commands.
- **`flow` / `flows`** → which flow family(ies) the skill belongs to. No tag = a
  **global** skill, always available regardless of flow.

These parse into `SkillMeta(name, description, model_invocable, user_invocable,
flows)`. `load_catalog()` returns a list of these; the router only ever
auto-selects `model_invocable` skills.

## 2. Lazy skill discovery (the hybrid runtime)

The agent does **not** receive every selected skill's body up front. Instead:

```
prompt
  │
  ▼
Router.classify ── reads the (flow-scoped) catalog
  │                • task_type + a few high-confidence skill picks (pre-seed)
  ▼
agent system prompt =
    base policy + environment + persona
  + # Skills MENU        ← names + one-line descriptions only (compose_menu)
  + (pre-seeded bodies)  ← the obvious skill(s) the router was confident about
  + load_skill tool
  │
  ▼
agent runs ── calls load_skill("name") to pull a body into context
              ONLY when it decides it needs that skill
```

- **The menu** (`compose_menu`) is cheap: one line per skill. A flow with 40
  skills costs ~40 lines, not 40 bodies.
- **`load_skill`** (`harness/tools/load_skill.py`) is a normal agent tool. Its
  output (the skill body) returns to the model as an observation, the same way a
  file `read` does. Dedup is per-turn (tracked on `env._loaded_skills`, reset at
  the start of each turn in `tracing_agent.run`), so a skill is not re-injected
  mid-turn but can be re-pulled on a later turn.
- **Hybrid** = the cheap router still scopes the flow and may pre-seed an obvious
  skill body; the expensive agent pulls the rest on demand.

The `load_skill` tool is registered only when `build_registry(skill_roots=...)` is
given roots (it is, in both the CLI and ACP dispatch). Without roots the registry
still contains the built-in tools (`bash`, `read`, `write`, `edit`, `create_job`,
`subagent`, `review`); roots add lazy skill loading on top.

## 3. Flows

A **flow** is a named family of skills, defined entirely by data — no router code
changes to add one:

1. Tag the skills: `flow: marketing` (or `flows: [marketing, seo]`) in their
   frontmatter, and/or place them under a skills root.
2. Enable the flow for a persona in its `persona.toml`:

   ```toml
   name = "Copywriter"
   flows = ["copywriting", "seo"]   # this persona sees global + these flows
   ```

3. (Optional) ask the agent what fits the task; the flow map can be rendered
   from the tags by `flows.render_map`.

`flows.scope_catalog(metas, enabled_flows)` keeps **global** skills (no tag) plus
skills in an enabled flow. When a persona sets **no** `flows`, dispatch skips
scoping entirely and uses the full catalog — identical to the pre-flows behavior.

### Adding a new flow family (e.g. SEO)

1. Author the skills with `flow: seo` frontmatter; drop them in a skills root.
2. Add `flows = ["seo"]` to the persona's `persona.toml`.
3. Done. No edit to `router.py`, no new `task_type`, no dispatch branch.

## The curated maturity spine (default)

The harness ships a small set of **global** skills that make the model work like a
professional — always available, persona-tweakable, and joined by user-added
skills later. They are adapted (re-authored) from
[garrytan/gstack](https://github.com/garrytan/gstack) and
[mattpocock/skills](https://github.com/mattpocock/skills) (see
`harness/skills/NOTICE.md` for attribution):

| Skill | Gate it enforces |
|-------|------------------|
| `clarify-before-acting` | Tell a **question** from a **work order** — answer/scope before editing. Fixes "the agent rewrites code when you only asked about it." |
| `planning-before-coding` | Architecture, edge cases, failure modes, and the test surface **before** implementation. |
| `systematic-debugging` | Root cause before any fix (the Iron Law). |
| `test-driven-development` | Failing test first, then minimal code. |
| `verification-before-completion` | Prove it works before claiming done. |
| `receiving-code-review` | Fold feedback with rigor, not reflexive agreement. |
| `ask-done` | **Model-disabled** (`disable-model-invocation`) advisory router over the skills/flows — "what fits here?" |

These ship **global** (no flow tag) so maturity is always on. The flow machinery
(`engineering`, `seo`, `marketing`, …) is reserved for future *specialized*
families, which arrive as data without touching the router.

## The no-op guarantee

Every layer is additive. With no new frontmatter and no `persona.toml flows`:

- `SkillMeta` defaults to `model_invocable=True, user_invocable=True, flows=()` →
  the catalog behaves like the old `(name, description)` list.
- `render_base_prompt(skills_menu=None)` is byte-identical to before.
- `build_registry()` with no roots still skips `load_skill`; built-in tools such
  as `create_job`, `subagent`, and `review` remain registered.
- `scope_catalog` is skipped when no flows are enabled.

The change is opt-in: a skill or persona gets the new behavior only by declaring it.

## Where to look in the code

- Invocation model + catalog + menu: `harness/skills.py`
- The pull tool: `harness/tools/load_skill.py`, registered in `harness/tools/registry.py`
- Per-turn dedup reset: `harness/tracing_agent.py` (`run`)
- Menu in the prompt: `harness/base_prompt.py` (`skills_menu`)
- Flow scoping: `harness/flows.py`, `harness/persona_config.py` (`read_flows`)
- Dispatch wiring: `harness/run_traced.py`, `harness/acp_agent.py`, `harness/acp_main.py`
- The spine: `harness/skills/*/SKILL.md`
- Design + research: `docs/superpowers/specs/2026-06-28-router-flows-*.md`

## Skill roots & the Agent Skills standard

Done resolves skills from multiple roots, ordered **lowest precedence first (later
root wins by name)** — aligned with the cross-tool [Agent Skills standard](https://agentskills.io):

```
bundled                       (shipped maturity spine)
~/.claude/skills              (ecosystem USER skills — consumed for free)
~/.config/harness/skills      (your Done USER skills — native, outranks compat)
<cwd>/.claude/skills          (ecosystem PROJECT skills, when run in that repo)
<cwd>/.agents/skills          (the cross-tool PROJECT standard — highest)
```

Per-persona extra roots from `persona.toml` `skills = [...]` are not currently
loaded by the runtime; put custom skills in one of the roots above.

- **Native Done dirs outrank ecosystem-compat dirs** at the same scope (a deliberate
  Done skill beats a borrowed one); **project outranks global**.
- **`.agents/skills`** is the emerging standard every major harness (Codex, Cursor)
  is converging on; **`.claude/skills`** is read for compatibility so Done consumes
  the largest existing skill ecosystem with no porting.
- A **name clash across roots** is surfaced (not silent): the capability answer
  ("what skills do we have?") notes which copy is active. Malformed skills are
  surfaced with their reason (see #87).

### `flow:` is a Done extension

Done's `flow:` frontmatter tag (and the invocation keys `disable-model-invocation`
/ `user-invocable`) are layered **on top of** the standard. The standard ignores
unknown frontmatter keys, so a Done skill stays portable to other tools — they just
don't act on `flow:`. Done reads it for per-persona flow scoping (see above).

### Release note

Done now reads `.claude/skills` and `.agents/skills` (project and `~`). If you use
Claude Code or another Agent-Skills tool, your existing `~/.claude/skills` are now
available in Done automatically.

### Security note

A project skills dir (`<cwd>/.agents/skills`, `<cwd>/.claude/skills`) is trusted to
the same degree as that repo's code: running Done inside an untrusted cloned repo
means that repo's skill *instructions* can enter the prompt. Done never executes
skill `scripts/` — it only injects SKILL.md text — so the blast radius is the same
as the repo's own code and `AGENTS.md`, which Done already reads.
