# Phase 3 — Knowledge/Skills Loading Layer — Design

**Status:** design (pre-plan)
**Date:** 2026-06-25
**Depends on:** Phase 0 (tracer), Phase 1 (AgentRunner), Phase 2 (Router) — all merged to `main`.

## Goal

Make the harness *use* the skills the Router already selects. Today
`Classification.skills` carries validated catalog names and is emitted in the
`task.classified` event, but nothing downstream consumes it. Phase 3 closes
that loop: map each selected skill name to a `SKILL.md` file and inject its
content into the agent's system prompt, so a poker task runs with poker domain
knowledge in context and a testing task runs with testing guidance.

## Guiding decisions (settled during brainstorming, 2026-06-25)

1. **Skills, not task_type, drive the prompt.** `task_type` stays a pure
   routing decision (chat vs. agent vs. clarify). Only the selected `skills`
   change prompt *content*. The cheap router remains a SELECTOR, never an
   author of the capable model's behavior. (User decision; see
   `phase3-prompt-driver-decision` memory.)
2. **Skill format = `SKILL.md` with YAML frontmatter**, one directory per
   skill. The catalog is GENERATED from frontmatter — one source of truth, no
   drift between a hand-kept catalog and the files. Mirrors Claude
   Code / superpowers skill layout.
3. **Inject the full body, verbatim.** No truncation, no summarization. We
   control size by keeping bodies focused. Progressive disclosure
   (summary-then-expand) is a deliberate later refinement, YAGNI for now.
4. **A new module `trace/skills.py` owns content**; the Router stays
   selection-only and consumes the catalog as a parameter (it already accepts
   `catalog=`). Clean separation: selection (router) / content (skills.py) /
   injection (TracingAgent).
   - **Catalog ownership.** `SKILL_CATALOG` is REMOVED from `router.py`. The
     catalog is now produced only by `skills.load_catalog()`. The `Router`
     ctor's `catalog` parameter loses its `=SKILL_CATALOG` default and becomes
     required (no default) — callers always pass a catalog. This makes
     "one source of truth" literally true (no constant left to drift). The
     existing `tests/test_router.py`, which imports `SKILL_CATALOG` and
     constructs `Router(...)` relying on the default, is updated to build small
     inline `[(name, desc)]` catalogs and pass them explicitly — the router's
     selection logic is unchanged, only where the catalog comes from.
5. **Bad skills are skipped AND shown.** A skill that can't load is dropped;
   the agent still runs with whatever loaded. The console prints both injected
   and skipped (with reason) on every run that selects skills; a `skill.load`
   event records the same for the trace. Fail-safe, consistent with the
   router's degrade-never-crash philosophy.
6. **Inject AFTER Jinja rendering.** Skill bodies are appended to the
   already-rendered system message, so a body containing `{{ }}` or `{% %}` is
   literal text and cannot crash `StrictUndefined` rendering.
7. **Injection seam = `TracingAgent._render_template` override.** No upstream
   edits. The block is composed as plain text and appended after
   `super()._render_template()`, matched by identity to the system template
   only.

## Global Constraints

- **Zero upstream edits.** Nothing under `upstream/` changes. Injection lives
  entirely in the `TracingAgent` subclass.
- **Python ≥ 3.10**, run via `.venv/bin/python` (system python3 is 3.9.6).
  Tests run as `.venv/bin/python -m pytest tests/` — scope to `tests/`, NOT
  bare `pytest`: bare collection walks `upstream/tests/` and errors on optional
  deps (portkey, modal). Our suite lives only under `tests/`.
- **Upstream test style** (from `upstream/AGENTS.md`): pytest, no mocking/
  patching unless explicitly required, no trivial tests, `assert func() == b`
  one-liners.
- **No new third-party dependency for frontmatter.** Parse the `---`-delimited
  frontmatter with the already-installed PyYAML (6.0.3 confirmed in the venv;
  already used by `run_traced.py`). `trace/skills.py` adds its own
  `import yaml`. Parsing: split on the leading `---` fence, `yaml.safe_load` the
  header, keep the rest as the body. No `python-frontmatter` package.
- New parameters default to `""`/empty, so the mock path and the agent's
  rendered output are byte-identical when no skill is selected. Two existing
  tests need deliberate, named updates (not regressions) because the *wiring*
  changes — see "Existing tests that change" below. Everything else stays green
  untouched.

## Architecture

```
skills/<name>/SKILL.md  ──load_catalog()──►  [(name, desc)]  ──►  Router(catalog=…)
        │                                                              │ classify(prompt)
        │                                                              ▼  cls.skills (validated)
        └──compose(selected_names)──► SkillLoad(block, injected, skipped)
                                                  │
                       skill.load event + console line (injected/skipped)
                                                  │ block
                                                  ▼
        run_agent(prompt, skill_block=block) → MiniSweAgentRunner.run(…, skill_block)
                                                  → TracingAgent(skill_block=…)
                                                      _render_template: super() + block
                                                          ▼
                                                  system message (agent run)
```

Three responsibilities, three owners:
- **Selection** — `Router` (unchanged logic; now fed a generated catalog).
- **Content** — `trace/skills.py` (NEW): discover, parse, compose.
- **Injection** — `TracingAgent` (extended): append block after render.

Invariant: `task_type` never touches the prompt; only `skills` does.

## Components

### 1. `skills/<name>/SKILL.md` (on-disk format)

```
---
name: poker-domain-rules
description: Poker rake/rakeback math and PPPoker domain logic
---
# Poker Domain Rules

<markdown body — injected verbatim into the system prompt>
```

- Frontmatter (`name`, `description`) is the single source of truth for the
  catalog.
- `name` MUST equal the directory name; a mismatch is a load error (skipped,
  surfaced).
- For Phase 3 we author **one real SKILL.md** end-to-end —
  `python-testing` (the sample-repo demo is a pytest fix, so this skill is
  exercised by the existing demo) — plus a small number of stubs to prove the
  catalog scans more than one entry. Remaining catalog names land as needed.

### 2. `trace/skills.py` (NEW — content layer)

```python
from dataclasses import dataclass, field
from pathlib import Path

@dataclass
class SkillLoad:
    block: str = ""                                  # composed text; "" if nothing injected
    injected: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (name, reason)

def load_catalog(skills_dir: Path) -> list[tuple[str, str]]:
    """Scan skills_dir for <name>/SKILL.md, parse frontmatter → [(name, desc)].
    A dir whose SKILL.md is missing/malformed/name-mismatched is skipped
    (logged), not fatal. Absent skills_dir → []. Deterministic order
    (sorted by name)."""

def compose(skills_dir: Path, names: list[str]) -> SkillLoad:
    """For each selected name (in selection order), read SKILL.md and append
    its body to the block. Missing file / bad frontmatter / name-mismatch →
    recorded in skipped with a reason; the good ones still inject. Empty names
    → empty SkillLoad."""
```

Block format (fixed):
```
\n\n# Available Skills\n\nThe following skills apply to this task. Follow them.\n\n## <name>\n<body>\n\n## <name>\n<body>
```

`compose` returns one object carrying both the injectable block and the report
(injected + skipped/why), so the caller has everything for the console line and
the `skill.load` event in one pass.

### 3. `TracingAgent` (extended — injection seam)

```python
def __init__(self, model, env, *, emitter, skill_block: str = "", **kwargs):
    super().__init__(model, env, **kwargs)
    self._emitter = emitter
    self._skill_block = skill_block
    ...

def _render_template(self, template: str) -> str:
    out = super()._render_template(template)
    if self._skill_block and template is self.config.system_template:
        out += self._skill_block          # appended AFTER Jinja — bodies never evaluated
    return out
```

- `skill_block` defaults to `""` → existing tests/mock path unchanged.
- Identity match (`template is self.config.system_template`) so only the system
  message gets skills, never `instance_template` (which also flows through
  `_render_template`).
- Bodies bypass Jinja entirely (appended post-render), so `{{ }}` / `{% %}` in a
  body is literal text.

### 4. `MiniSweAgentRunner` (extended — passthrough)

`run(self, task, *, skill_block: str = "", **kwargs)` passes `skill_block` into
the **`TracingAgent(...)` constructor** and does NOT forward it through
`**kwargs` into `agent.run(task, **kwargs)`. This is load-bearing: `runner.py`
currently does `agent.run(task, **kwargs)` (runner.py:92), and upstream
`DefaultAgent.run` merges its run-kwargs into `extra_template_vars`
(default.py:90). If `skill_block` leaked into `**kwargs`, it would become a
template variable instead of an injected block — a silent bug. So `skill_block`
is a named keyword consumed by `run`, kept out of the `**kwargs` dict. Default
`""` keeps the Phase-1 interface and all existing callers working.

## Data flow (`run_traced.py`)

**Startup (`main`):**
```python
SKILLS_DIR = REPO_ROOT / "skills"
catalog = skills.load_catalog(SKILLS_DIR)     # replaces hardcoded SKILL_CATALOG
router = Router(complete, catalog=catalog)
```

**At agent dispatch (inside `route_and_dispatch`, only on the run_agent path,
after classify + clarification settle):**
```python
load = load_skills(cls.skills)                  # injected collaborator, NOT a direct skills.compose()
emitter.emit("skill.load", injected=load.injected, skipped=load.skipped)
if cls.skills:                                 # silent when none selected
    echo(f"skills: injected {load.injected}, skipped {load.skipped}")
run_agent(prompt, skill_block=load.block)
```

**Injection-as-collaborator (testability).** `route_and_dispatch` gains a
`load_skills: Callable[[list[str]], SkillLoad]` parameter, injected exactly like
the existing `router` / `make_chat_handler` / `run_agent` / `ask_user` / `echo`
collaborators. It MUST NOT call `skills.compose(SKILLS_DIR, …)` directly — that
would touch the filesystem and break the existing collaborator-injected unit
tests (`tests/test_run_traced.py` calls `route_and_dispatch` with stubs and no
files). `main()` supplies the real binding:
`load_skills=lambda names: skills.compose(SKILLS_DIR, names)`.

**The `load_skills` parameter MUST be keyword-only with a safe default** —
`load_skills: Callable[[list[str]], SkillLoad] = lambda names: SkillLoad()`
(empty load, no skills). There are **8 existing `route_and_dispatch` call
sites** in `tests/test_run_traced.py` (the calls at lines ~47, 58, 69, 84, 97,
117, 134, 198). A required parameter would break all 8 even though only the
event-ordering tests care about skills. A defaulted parameter means the 7 calls
that do NOT assert on `skill.load` need ZERO changes; only the ordering-
sensitive test(s) and the new skill tests pass an explicit `load_skills` stub.
This matches how every Phase-1/2 collaborator was added (defaulted, non-
breaking). `run_agent` gains a `skill_block` keyword and forwards it to
`MiniSweAgentRunner.run`.

**Caveat on the default emitting `skill.load`.** With the default empty
`load_skills`, every agent-path call still emits a `skill.load` event (empty
lists). That changes the event stream for ANY test that runs the agent path and
asserts on event order/count — not just `test_4`. See "Existing tests that
change" for the full, verified list.

**Ordering guarantees:**
1. Skills load only for agent tasks — `chat_question` and `ambiguous` return
   before this code.
2. Re-classification respected — `cls.skills` is the post-clarification
   selection (dispatch already uses the final `cls`).
3. `skill.load` emitted through the CLI `Emitter` (a pre-run routing fact, like
   `task.classified`), then `run_agent`'s renumbered runner events follow — no
   seq collision (`write_renumbered` already handles the runner stream).
4. `skill.load` fires on every agent run (empty lists if nothing selected), so
   the trace always answers "what knowledge did this run have?"

## Error handling

| Failure | Behavior |
|---|---|
| `skills/` dir absent | `load_catalog` → `[]`; router selects nothing; harness == Phase 2. |
| SKILL.md missing/malformed at catalog time | skill omitted from catalog; logged. |
| Selected skill missing at compose time | skipped, reason `"no SKILL.md"`; agent still runs. |
| Frontmatter unparseable | skipped, reason `"bad frontmatter"`; never injected. |
| Frontmatter parses but is not a dict / missing `name` or `description` | skipped, reason `"frontmatter missing name/description"`. |
| `name` in frontmatter ≠ dirname | skipped, reason `"name mismatch"`; never injected. |
| `SKILL.md` is a directory / unreadable / not UTF-8 | skipped, reason from the caught error; never fatal. |
| Empty body (frontmatter only) | injects an empty section (header + name, no body) — harmless; not an error. |
| Duplicate skill dirs (same `name`) | `load_catalog` keeps the first by sorted order, logs the dupe; can't happen via dirname since dirs are unique, but a frontmatter `name` collision across two dirs is logged. |
| Body contains `{{ }}` / `{% %}` | harmless — injected post-render, literal text. |
| Nothing selected | empty block; `skill.load` still emitted (empty lists); no console line. |

**Non-fatal guarantee — implementation note.** `compose` (and `load_catalog`)
wrap each per-skill read in a `try/except` catching the realistic failure set —
`OSError` (missing / is-a-dir / permission / unreadable), `UnicodeDecodeError`
(non-UTF-8), and `yaml.YAMLError` plus a shape check (result is a dict with
`name` and `description`). Any caught failure → that skill is recorded in
`skipped` with a short reason; the loop continues. No exception from one bad
skill may abort the run — that is the "skip and show" contract from decision 5.

Defense-in-depth: the router already filters `cls.skills` to catalog names, so
a "selected but missing at compose" skip is rare — but the catalog is read at
startup and a file could vanish mid-session, so `compose` handles it anyway.

## Testing

### New tests

`tests/test_skills.py` (real files via `tmp_path`, no mocks):
- `load_catalog`: parses frontmatter → `[(name, desc)]`; skips dir with no
  SKILL.md; skips malformed frontmatter; skips frontmatter missing
  `name`/`description`; skips name/dir mismatch; absent dir → `[]`;
  deterministic (sorted) order.
- `compose`: injects one body; injects multiple in selection order; missing
  file → skipped+reason while the good one still injects; mismatch → skipped;
  empty selection → empty block; a body with `{{ x }}` survives verbatim; a
  non-UTF-8 / unreadable SKILL.md → skipped with a reason (never raises).

`tests/test_tracing_agent_skills.py`:
- `_render_template` appends the block to the system template only, not
  `instance_template` (identity-match guarantee).
- Empty `skill_block` → render byte-identical to upstream (no regression).
- A `TracingAgent` built with a `skill_block` places it in the first system
  message.

### Existing tests that change (deliberate, not regressions)

This list was verified by reading every `route_and_dispatch` call site and
event assertion in `tests/test_run_traced.py`, not estimated.

- `tests/test_router.py` — currently imports `SKILL_CATALOG` (test_router.py:6)
  and relies on `Router`'s default catalog. Updated to build small inline
  `[(name, desc)]` catalogs and pass `catalog=` explicitly. Selection assertions
  unchanged.
- `tests/test_run_traced.py::test_4` — currently asserts `rec[1]` is
  `run.started` (test_run_traced.py:171). Because `skill.load` is now emitted
  between classification and the runner events, the stream becomes: `rec[0]`
  `task.classified`, `rec[1]` `skill.load`, `rec[2]` `run.started`, last
  `run.finished`. Update the `rec[1]` assertion accordingly; the contiguous-seq
  assertion (`:168`) and `rec[0]`/`rec[-1]` assertions are unchanged. test_4
  goes through `main()`, which supplies the real `load_skills`; with no `skills/`
  dir present in the temp cwd, `skill.load` carries empty lists — still emitted.

**Verified to survive UNCHANGED (documented so the implementer knows they were
checked, not missed):**
- `test_9` (`:142-143`) asserts `rec[0]=="task.classified"` and contiguous seq
  only — it does not pin `rec[1]`, and its `run_agent` stub fires after the
  `skill.load` emit, so inserting `skill.load` at `rec[1]` keeps both assertions
  true. No change needed.
- `test_10` (`:207-209`) filters events to `task.classified` and counts them
  (`==2`); `skill.load` is a different type, so the count is unaffected. No
  change needed.
- The 7 non-ordering `route_and_dispatch` call sites (test_4-second-call, 5, 6,
  7, 8, and the two clarification tests' calls) are protected by the
  **defaulted** `load_skills` param and need no edits.

All other Phase 0–2 tests remain green untouched (new params default to
`""`/empty).

## Out of scope (deferred)

- Progressive disclosure (summary-then-expand, on-demand body reads).
- Per-skill assets (examples, sub-files) — directory layout allows it later.
- Skill *authoring* tooling. Phase 3 authors skills by hand.
- DRY-ing the litellm env-wiring sites (router.complete, _build_vibeproxy_model,
  ChatHandler) — a known cleanup; tackle when a fourth consumer appears, not
  forced by this phase.

## Phase-4 hand-off note

After Phase 3, the agent runs with selected knowledge injected and the
`skill.load` event makes it observable. The next consumer is the CLI (Phase 4),
which will render these events for the user. Nothing in Phase 3 should assume a
particular client.
