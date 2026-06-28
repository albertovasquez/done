# Design spec: Agent Skills standard — project roots + portability (PR1)

**Date:** 2026-06-28
**Status:** Design — for review, then implementation plan.
**Scope:** PR1 of adopting the Agent Skills open standard. **Skill-resolution roots only.** The install command (`/skill add`) is a separate follow-on brainstorm. Grounded in the deep-research report (`skills-standard-research` memory; run wf_250f048e-79a, 24/25 claims verified).

## Goal

Make Done's skill resolution conform to the cross-tool convention so (a) Done picks up **project-scoped** skills, and (b) Done consumes the **existing ecosystem's** skills for free — without adding complexity beyond what portability requires.

Today `skills_dirs()` = `[bundled, ~/.config/harness/skills]` — no project scope, no ecosystem compat. Every major harness (Codex, Cursor, Claude Code, OpenClaw) resolves skills from a project+user+bundled hierarchy under the shared `.agents/skills` (and legacy `.claude/skills`) directories. This spec adds those roots.

## Decisions locked (robust-minimal lens)

| Question | Decision | Why |
|---|---|---|
| **Flows encoding** | Keep `flow:` in Done's SKILL.md frontmatter as a **Done-only extension**; document it. | The standard ignores unknown frontmatter keys, so skills stay 100% portable AND flows keep working with zero new machinery. `metadata.done.flow` is ceremony; `context:fork` loses portability. |
| **Collisions** | Keep deterministic **later-root-wins** by name; **surface a "shadowed" notice** (extends #87). | Namespacing is real complexity that only pays off with a marketplace Done doesn't have. |
| **Trust model** | Done does **not** execute skill `scripts/` (it only injects SKILL.md text) — so **add no execution feature here**; future install confirms contents. | The ToxicSkills data (13.4% critical) demands a guard, but the robust-minimal guard is "don't auto-run untrusted code" — which is already true because we don't run skill code at all. |
| **Registry** | **None.** Consume git/directories only (future install = git-clone). | A registry is a product surface with no demand; git is the common denominator across all harnesses. |
| **Install UX** | **Deferred** to a separate spec. | Has its own UX surface (TUI command, confirm flow). Keep PR1 tight. |

## Non-goals (YAGNI — explicit)

- `/skill add` install command (separate brainstorm).
- Registry / marketplace / plugin packaging (`.claude-plugin/plugin.json`).
- Namespacing of installed skills.
- `scripts/` execution, `allowed-tools` enforcement, `context:fork`/`agent:`.
- A startup metadata budget cap (only matters at large skill counts; note, don't build — Done's lazy menu already keeps bodies out of context).
- Upward directory walk for the project root (consistent with AGENTS.md: launch-cwd only).

## Architecture

### The change: `skills_dirs()` gains project roots (threaded `cwd`)

Today `skills_dirs()` takes no arguments. To add project-relative roots it must learn the project `cwd` — exactly the pattern AGENTS.md used (`resolve_agents(project_cwd=...)`). New signature:

```python
def skills_dirs(project_cwd: str | Path | None = None) -> list[Path]:
    """Ordered LOWEST precedence first (later roots win by name). Project roots are
    added only when project_cwd is given; absent roots are kept (load_catalog/compose
    skip non-dirs). project_cwd=None reproduces the pre-project behavior."""
```

**Precedence (lowest → highest, later wins):**

```
bundled                          (shipped maturity spine)
~/.claude/skills                 (ecosystem user compat — read-only consume)
~/.config/harness/skills         (Done user dir — native, outranks compat)
<cwd>/.claude/skills             (ecosystem project compat)
<cwd>/.agents/skills             (the cross-tool project standard — highest)
```

Rationale for the ordering: **native Done dirs outrank ecosystem-compat dirs at the same scope** (a user's deliberate Done skill should win over a borrowed one), and **project outranks global** (the repo you're in is more specific than your home). `.agents/skills` is highest because it is THE emerging cross-tool standard the ecosystem is converging on; `.claude/skills` is the legacy-compat read. Per-persona `persona.toml` `skills` roots continue to append on top (unchanged).

**Behavior is one clear rule:** the two **user-scope** roots (`~/.claude/skills` compat, `~/.config/harness/skills` native) are always in the list; the two **project-scope** roots (`<cwd>/.claude/skills`, `<cwd>/.agents/skills`) are added only when `project_cwd` is given. So `skills_dirs()` (no arg) = `[bundled, ~/.claude/skills, ~/.config/harness/skills]`. Adding `~/.claude/skills` is a true no-op for everyone who doesn't have that dir (it's skipped as a non-dir), so we do not gate it — keeping the rule simple beats a conditional that guards an absent directory.

### Components

- **`harness/paths.py`** — `skills_dirs(project_cwd=None)` returns the ordered roots above. Pure path construction; no I/O (existing callers tolerate absent roots).
- **Dispatch sites** — `run_traced.py` and `acp_main.py`/`acp_agent.py` pass the project cwd (`args.cwd` / `state.cwd`) into `skills_dirs(...)` wherever they call it today.
- **Collision notice** — `load_catalog_with_skips` (from #87) gains shadow tracking: when a later root overrides an earlier skill of the same name, record `(name, "shadowed by <root>")` in a new `shadowed` list (separate from `skipped`, since a shadow is not an error). Surface it in the capability answer like skips.
- **Docs** — `docs/agents-md.md` has a sibling note, and `docs/router-flows.md` documents `flow:` as a Done extension + the new precedence table.

### Data flow

```
dispatch (knows project cwd)
  ▼
paths.skills_dirs(project_cwd=cwd)  →  [bundled, ~/.claude/skills, ~/.config/harness/skills,
                                         <cwd>/.claude/skills, <cwd>/.agents/skills]
  ▼
load_catalog_with_skips(roots)  →  CatalogLoad(skills, skipped, shadowed)
  ▼  (later root wins; shadows recorded)
router catalog + skills menu + load_skill  (unchanged downstream)
```

## Error handling

- Absent roots are skipped (existing `load_catalog`/`compose` behavior — they check `is_dir()`).
- Malformed skills in any new root surface via #87's `skipped` (already built).
- A shadow is informational, never an error.
- No new failure modes; resolution never raises.

## The no-op guarantee

- `skills_dirs()` with no `project_cwd` adds only the `~/.claude/skills` compat root, which is an absent dir for nearly all users → skipped → catalog identical. Callers that pass `project_cwd` get the project roots, also absent unless the user created them.
- Existing tests that assert `skills_dirs() == [bundled, config/skills]` **will change** — they must be updated to the new ordered list (enumerable, few sites). This is an intended, documented behavior change, not a regression.
- The bundled spine and all current behavior are unchanged when no new dirs exist.
- **Caveman-review follow-up:** a Claude Code user who already has `~/.claude/skills` gets a behavior change on upgrade (their skills are now consumed). This is intended portability, but it is NOT silent-safe for them — add a one-line **release note** ("Done now reads `.claude/skills` / `.agents/skills`") in addition to the docs.

## Security callout (elevated from caveman-review) 🔴

Reading `<cwd>/.claude/skills` (and `.agents/skills`) means **when Done runs inside a cloned repo, that repo's skill instructions enter Done's prompt** — attacker-controllable content if the repo is untrusted. The mitigation holds because Done **only injects SKILL.md text and never executes skill `scripts/`** (no execution path exists), so the blast radius is "prompt injection," identical to the repo's own code/AGENTS.md that Done already reads. Document that **a project skills dir is trusted to the same degree as that repo's code** — cloning and running Done in an untrusted repo is the user's trust boundary, unchanged by this PR. (No new execution; the install command, when built, adds the explicit confirm step.)

## Testing strategy

- `paths.skills_dirs(project_cwd=...)` returns the exact ordered list; `None` omits the two PROJECT roots but keeps the user roots; ordering is lowest-precedence-first.
- `load_catalog_with_skips` across roots: later root wins by name; a shadowed skill is recorded in `shadowed` with the winning root; a skill present in only one root is not shadowed.
- A skill dropped into `<cwd>/.agents/skills` appears in the catalog; one in `<cwd>/.claude/skills` appears (compat).
- **The subtle tie-break (explicit test):** a same-named skill in BOTH `~/.config/harness/skills` (native) and `~/.claude/skills` (compat) → the native Done one wins; assert it by name+description.
- **`CatalogLoad` field guard:** adding `shadowed` is a 3rd field — assert no caller positionally-unpacks `CatalogLoad` (grep: only `.skills`/`.skipped` attribute access in run_traced/acp; keep it that way).
- Capability answer surfaces shadows alongside skips.
- Dispatch threads `project_cwd` (run_traced + acp).
- Audit tests asserting the old `skills_dirs()` shape; update to the new list.
- Full suite green (`.venv/bin/python -m pytest tests/ -q`), no regression to the 752 baseline beyond the intended `skills_dirs` shape updates.

## Risks & mitigations

- **`skills_dirs()` signature change ripples to callers.** Mitigation: default `project_cwd=None` keeps positional callers working; enumerate + update the call sites + the shape-asserting tests.
- **Consuming third-party `.claude/skills` could inject untrusted instructions.** Mitigation (this PR): we only read SKILL.md *text* (no execution); the future install command adds the confirm step. Document that project `.agents/skills` from a cloned repo is trusted to the same degree as that repo's code.
- **Precedence surprise.** Mitigation: publish the precedence table in docs + assert it in a test (the standing "make the implicit explicit" rule).
- **Reading `~/.claude/skills` changes behavior for Claude Code users.** Intended (free portability); documented; absent for everyone else.

## Rollout

Single tight PR: `skills_dirs(project_cwd=)` + dispatch threading + `shadowed` tracking + the precedence/`flow:` docs + tests. Then a **separate brainstorm** for the `/skill add` install UX. Registry/packaging remain deferred.
