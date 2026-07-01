# Discovery-Wave Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `discovery-wave` skill that teaches the orchestrator persona to run two bounded fan-out rounds (discovery → approach) at the front of brainstorming, using the harness's existing `subagent` tool.

**Architecture:** The feature is a **prompt/protocol skill**, not new Python infrastructure. The harness has no multi-stage `Workflow` engine — it has a single-level `subagent` tool (an LLM-invoked tool, `harness/tools/subagent.py`) that fans out read-only workers in parallel and returns a digest. The orchestrator (main-thread persona) drives the stages *sequentially* by calling `subagent` once per stage: finders, then verifiers, then generators. Caps and the verify protocol are enforced by the skill's instructions (the orchestrator is a capable model following a checklist), not by new code. The skill is wired into a `discovery` flow via the existing `flows.py` data path — no router edits.

**Tech Stack:** Python 3.11+, existing `SubagentTool` / `build_registry` / `flows.py` / `SkillMeta` machinery, pytest. Skill authored as a `SKILL.md` markdown file with frontmatter.

## Reality reconciliation (spec §3–§5 vs. verified codebase)

The merged spec (`docs/superpowers/specs/2026-07-01-discovery-wave-design.md`, PR #262) assumed a `Workflow` engine and typed caps. Codebase verification (3 read-only finders) found otherwise. This plan implements the spec's *intent* on real primitives; the deltas:

| Spec says | Reality | This plan |
|---|---|---|
| Reuse a `Workflow` multi-stage engine | No such engine in the harness (that's an outer-tool capability). Only single-level `subagent`. | Orchestrator calls `subagent` once per stage, sequentially. |
| Reuse an `Explore` read-only agent | No `Explore` agent type. Read-only is enforced via `DEFAULT_WORKER_TOOLSET={"read","bash"}` filtered at registry build (`registry.py:61-62`). | Finders/verifiers use the *default* toolset (omit `tools`) → read-only, code-enforced. |
| §3.4 typed caps: ≤8 finders + ≤8 verifiers + ≤4 generators | Single global cap `subagent_max_concurrent()` (default 4); `MAX_TASKS_PER_CALL=16`. No per-type caps. | Skill instructs the orchestrator to cap *task counts* per stage (≤8/≤8/≤4). Concurrency is bounded globally by config; task count is bounded by the prompt. |
| §3.4 ~26-agent token budget | No token budget; only wall-time (`_remaining_secs`). | Deferred (documented as future work in the skill). Wall-time still applies per worker. |
| Depth-1 (workers can't spawn workers) | Hard-enforced (`registry.py:51-58`). | *Required* by the design — the orchestrator must drive all stages. Satisfied. |
| §5(a) flow-tag = frontmatter + persona.toml, no router edits | Verified literally true (`skills.py:61`, `persona_config.py:38`, `flows.py:11`). | Skill tagged `flows: discovery`; persona opts in via `flows = ["discovery"]`. |

## Global Constraints

- Python floor: **>= 3.11** (per pyproject; `tomllib` is stdlib).
- Test command from a worktree root: `.venv/bin/python -m pytest tests/ -q` (target `tests/` only).
- Skills live under `harness/skills/<name>/SKILL.md`; frontmatter parsed by `harness/skills.py` (`flows`, `disable-model-invocation`).
- Read-only workers: **omit** the `tools` field in a `subagent` task → default `{"read","bash"}` (never grant `write`/`edit` to finders/verifiers/generators).
- No new Python infrastructure unless a test proves the skill can't express a required behavior. This is a prompt-first feature.
- Do NOT modify `harness/router.py`, `/loop`, `/goal`, cron, or missions (spec §6).

---

### Task 1: Author the discovery-wave skill (the protocol)

The core deliverable: a `SKILL.md` that encodes the two-wave protocol the orchestrator follows. No Python. This is the whole feature's behavior.

**Files:**
- Create: `harness/skills/discovery-wave/SKILL.md`

**Interfaces:**
- Consumes: the harness `subagent` tool (schema in `harness/tools/subagent.py:24-55`) — the orchestrator calls it with `{"tasks": [{"goal","context"}, ...]}`.
- Produces: a named skill `discovery-wave` with `flows=("discovery",)`, `model_invocable=True` (orchestrator can invoke it), discoverable via the catalog when the `discovery` flow is enabled.

- [ ] **Step 1: Write the SKILL.md frontmatter + protocol body**

Create `harness/skills/discovery-wave/SKILL.md` with this exact content:

```markdown
---
name: discovery-wave
description: Fan out parallel read-only discovery then divergent approach-generation at the front of brainstorming. Use when a planning task is broad enough that a single linear exploration pass would miss prior art, constraints, or non-obvious solution approaches — BEFORE writing a spec. Bounded and read-only by design.
flows: discovery
---

# Discovery-Wave

Two bounded fan-out rounds at the front of planning: a **discovery wave** (facts)
then an **approach wave** (creativity built on those facts). You are the
orchestrator — you decompose, dispatch, verify, and synthesize. Workers are
read-only and isolated; they return one structured handoff each.

Priority ranking (optimize in order; sacrifice the last first):
1. **Fewer downstream surprises** (plan correctness) — dominates.
2. **Better options** (creativity).
3. **Faster discovery** (speed) — expendable.

Because #1 beats #3, an unverified fact is worse than a missing one. Verification
is the load-bearing step, not optional QA.

## Wave 1 — Discovery (facts)

1. **Decompose** the goal into ≤ 8 *disjoint, read-only* slices (subsystems,
   prior-art-in-this-codebase, constraints, external research). If it needs more
   than 8, the goal is too broad — narrow scope or pick the top 8 by design
   relevance and state which you deferred.
2. **Fan out finders** in ONE `subagent` call, one task per slice. Do NOT pass a
   `tools` field — the default `{read, bash}` toolset is read-only and is enforced
   in code (a finder cannot write). Each finder's task `context` must instruct it
   to return, for every claim: a tag `will-shape-design | context-only`, a
   confidence `high|med|low`, and a citation (`file:line`, URL, or metric) — or
   drop the claim.
3. **Read the digest.** Merge the handoffs.
4. **Verify (adversarial, default-on).** Only `will-shape-design` claims are
   verified. Batch them by slice/file into a SECOND `subagent` call of ≤ 8
   verifier tasks (default read-only toolset). Each verifier is told to *refute*:
   confirm the citation says what the claim says, and default to `unverified`
   when it cannot. A verifier must state what it checked (the file:line re-read /
   command re-run); a bare "verified" with no trail is treated as `unverified`.
   - **Absence/negative claims** ("X does not already exist") may be marked
     `verified` ONLY if the verifier states its search scope (terms, paths,
     synonyms). Otherwise `unverified`.
   - `context-only` claims are NOT verified but are labeled `unverified` in the
     brief so they can't silently become assumptions.
5. **Extend (bounded).** Fire AT MOST ONE narrow re-wave (≤ 3 finders + ≤ 3
   verifiers) and only for: (A) a coverage gap (empty slice / unanswered
   design-shaping question), or (B) a refuted/contested design-shaping claim.
   Hard cap: 2 discovery waves total. Everything else is flagged `unverified` in
   the brief as an explicit design risk.

Output: a **verified discovery brief** — every design-shaping fact is `verified`
or explicitly `unverified/contested`.

## Traceability rule

The `will-shape-design | context-only` tag is a guess made before the design
exists. RULE: any claim you cite in a derived approach frame or in the final spec
MUST be `verified`. If it was `context-only`, pull it back through a verifier
(counts against the caps) before it shapes the design. Keep a claim→spec trace so
every spec assumption resolves to a verified brief entry.

## Wave 2 — Approach (creativity)

6. Read the verified brief and derive the 2–3 real tensions for THIS problem (the
   axes where it forks — e.g. sync/async, buy/build). **Show the derived frames to
   the user before fanning generators** — a bad read costs one message, not a
   wasted wave. If the brief yields no tension, fall back to fixed lenses:
   MVP-first, risk-first, reuse-first, greenfield.
7. **Fan out generators** in ONE `subagent` call, ≤ 4 tasks, one per tension-pole
   (default read-only toolset — generators propose, they do not write code). Each
   is blind to the others (isolation → divergence).
8. **Synthesize** the returns into the 2–3 options brainstorming expects, then
   hand back to the normal `present design → write spec → writing-plans` flow.

## Degradation (fail-open)

Never block the user. A slice returning nothing → a coverage gap (feeds Extend).
Verifiers disagreeing → the claim is `contested/unverified`, never fact. If the
whole wave can't run (tool errors), fall back to linear brainstorming AND stamp
the resulting brief `unverified — degraded mode` so no fact from it is trusted
downstream.

## Caps (stated so they're checkable)

≤ 8 finders, ≤ 8 verifiers per wave; ≤ 3 + 3 for the single re-wave; ≤ 4
generators. Global concurrency is bounded by `[subagent].max_concurrent` (done.conf).
A per-run *token* budget is future work — not enforced yet; per-worker wall-time
still applies.
```

- [ ] **Step 2: Verify the skill parses and is flow-tagged**

Run: `.venv/bin/python -c "from harness.skills import load_catalog; import pathlib; ms={m.name:m for m in load_catalog([pathlib.Path('harness/skills')])}; m=ms['discovery-wave']; print(m.flows, m.model_invocable)"`

Expected output: `('discovery',) True`

(Loader is `harness.skills.load_catalog(roots, project_cwd=None) -> list[SkillMeta]`, confirmed at `harness/skills.py:133`.)

- [ ] **Step 3: Commit**

```bash
git add harness/skills/discovery-wave/SKILL.md
git commit -m "feat(skills): add discovery-wave two-wave brainstorming protocol"
```

---

### Task 2: Test that the skill is catalogued and flow-scoped correctly

Prove the skill loads with the right metadata and that flow-scoping includes/excludes it as designed. This is the behavioral contract that Task 1 relies on.

**Files:**
- Create: `tests/skills/test_discovery_wave_skill.py`

**Interfaces:**
- Consumes: `harness.skills` loader (Task 1's SKILL.md), `harness.flows.scope_catalog`, `harness.skills.SkillMeta`.
- Produces: regression coverage pinning `flows=("discovery",)` and scope behavior.

- [ ] **Step 1: Write the failing test**

Create `tests/skills/test_discovery_wave_skill.py`:

```python
import pathlib

from harness.flows import scope_catalog
from harness.skills import load_catalog


SKILLS_ROOT = pathlib.Path(__file__).resolve().parents[2] / "harness" / "skills"


def _load():
    return {m.name: m for m in load_catalog([SKILLS_ROOT])}


def test_discovery_wave_is_flow_tagged_and_model_invocable():
    meta = _load()["discovery-wave"]
    assert meta.flows == ("discovery",)
    assert meta.model_invocable is True


def test_discovery_wave_hidden_unless_discovery_flow_enabled():
    metas = list(_load().values())
    # Not in scope when a different flow is enabled...
    scoped_other = {m.name for m in scope_catalog(metas, ["seo"])}
    assert "discovery-wave" not in scoped_other
    # ...in scope when the discovery flow is enabled.
    scoped_disc = {m.name for m in scope_catalog(metas, ["discovery"])}
    assert "discovery-wave" in scoped_disc
```

- [ ] **Step 2: Run to verify it passes (skill already exists from Task 1)**

Run: `.venv/bin/python -m pytest tests/skills/test_discovery_wave_skill.py -q`
Expected: 2 passed. (Task 1 created the skill; this test pins its contract. If the loader import name is wrong, the collection error tells you — fix the import to the real symbol and re-run.)

- [ ] **Step 3: Confirm no regression in the flows suite**

Run: `.venv/bin/python -m pytest tests/test_flows.py tests/skills/ -q`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add tests/skills/test_discovery_wave_skill.py
git commit -m "test(skills): pin discovery-wave flow-tag and scope contract"
```

---

### Task 3: Document the discovery flow for personas + link the spec

Make the feature usable: document how a persona opts into the `discovery` flow, and mark the spec's open §5 decision as resolved (form (c), orchestrator-driven).

**Files:**
- Modify: `docs/superpowers/specs/2026-07-01-discovery-wave-design.md` (status + §5 resolution note)
- Modify: `README.md` OR `AGENTS.md` — whichever documents flows/skills for personas (grep first; see Step 1)

**Interfaces:**
- Consumes: nothing new.
- Produces: user-facing docs; no code contract.

- [ ] **Step 1: Find where flows/skills are documented for users**

Run: `grep -rniE "flows *=|persona.toml|flow famil" README.md AGENTS.md docs/ | head`
Pick the file that documents persona configuration. If none documents flows yet, add a short subsection to `AGENTS.md` (it's the shared operating-standards doc).

- [ ] **Step 2: Add the opt-in doc**

Add this snippet to the chosen doc (adjust heading level to context):

```markdown
### Enabling the discovery flow

The `discovery-wave` skill fans out parallel read-only discovery + divergent
approach-generation at the front of planning. It is scoped to the `discovery`
flow, so it stays out of a persona's catalog until opted in. To enable it, add to
the persona's `persona.toml`:

    flows = ["discovery"]

With no `flows` key, all global (untagged) skills remain available and nothing is
scoped out (pre-flows behavior). The orchestrator invokes `discovery-wave`
directly; there is no `/`-only restriction on it.
```

- [ ] **Step 3: Resolve the spec's §5 open decision**

In `docs/superpowers/specs/2026-07-01-discovery-wave-design.md`, update the `**Status:**` line to note the form is decided, and append to §5:

```markdown

**RESOLVED (2026-07-01):** Form = (c) orchestrator-driven, built on the existing
`subagent` tool (no `Workflow` engine exists in the harness). The `discovery-wave`
skill encodes the protocol; caps are prompt-enforced; token budget deferred. See
`docs/superpowers/plans/2026-07-01-discovery-wave.md`. Graduation to a richer
form (a) remains available since the skill is already flow-tagged.
```

- [ ] **Step 4: Verify docs render (no broken markdown) and spec still self-consistent**

Run: `grep -n "RESOLVED" docs/superpowers/specs/2026-07-01-discovery-wave-design.md`
Expected: one match, in §5.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/specs/2026-07-01-discovery-wave-design.md README.md AGENTS.md
git commit -m "docs(discovery-wave): resolve spec form decision + persona opt-in guide"
```

---

## Self-Review

**Spec coverage** (against `2026-07-01-discovery-wave-design.md`):
- §3.1 discovery wave (finders/verify/extend) → Task 1 protocol, steps 1–5 of the skill body. ✓
- §3.2 approach wave (derive frames + checkpoint + generators) → Task 1, steps 6–8. ✓
- §3.3 traceability rule → Task 1 "Traceability rule" section. ✓
- §3.4 caps → Task 1 "Caps" section (prompt-enforced) + reconciliation table. Token budget explicitly deferred. ✓ (delta documented)
- §3.5 degradation → Task 1 "Degradation" section, incl. `unverified — degraded mode` stamp. ✓
- §4 reuse → uses `subagent` (default read-only toolset); reconciliation notes `Workflow`/`Explore` absence. ✓
- §5 form → Task 3 Step 3 resolves to (c). ✓
- §6 out-of-scope (no router/loop/goal/cron/mission edits) → Global Constraints forbid it; no task touches them. ✓
- §7 success test → the skill's protocol enforces #1 (verify), #2 (divergent frames), fail-open (#4). #2/#3 are runtime-observable, not unit-testable; Task 2 pins the loadable contract. ✓

**Placeholder scan:** No "TBD/TODO/handle edge cases" — the skill body is complete prose; the one deferral (token budget) is explicit and intentional, not a placeholder. ✓

**Type consistency:** The loader symbol `load_catalog` (verified real, `skills.py:133`) is used identically in Task 1 Step 2 and Task 2. `flows=("discovery",)`, `model_invocable`, `scope_catalog(metas, [flow])` match the verified signatures (`skills.py:39-67`, `flows.py:11-16`). ✓

**Note on TDD ordering:** This feature's core deliverable (Task 1) is a *prompt*, which has no unit-testable behavior on its own — so Task 1 ships the artifact and Task 2 is the test that pins its loadable contract. The runtime behavior (does the orchestrator actually fan out and verify) is validated by *using* the skill in a real brainstorm, not by a unit test; that's the manual acceptance step, noted in §7.
