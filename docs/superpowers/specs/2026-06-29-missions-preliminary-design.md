# Missions — preliminary workflow feature for Done

**Date:** 2026-06-29
**Status:** Design (approved framing, pending spec review)
**Reference implementation copied:** Factory Droid **Missions** (Specification
Mode + mission file + orchestrator/worker/validator). We copy the *shape* of
their Missions model; we do **not** copy their multi-agent runtime yet.

## Goal

Give Done a preliminary "workflow" capability: turn a short ask into a written,
gated, milestone-tracked **mission file**, then execute it. This is the front
half of Factory's Missions ("the planning phase matters most") — the part that
delivers most of the value and needs **no multi-agent machinery**.

Done's engine is single-agent today (tools: bash/read/write/edit/create_job/
load_skill/load_memory; no spawn primitive). So execution is sequential, by one
agent. The mission-file *format* is shaped now so a future worker/validator
fan-out drops in **without reworking the format** ("design the seam, build
single").

Missions are **not coding-specific**. Done's agent is a chief-of-staff persona
("Bob"); a mission may be research, ops, writing, or code. Planning is the
general foundation; code is one instance.

## Non-goals (Phase 2+, documented not built)

- Real worker/validator **subagent spawning** (needs a spawn primitive added to
  the tool registry — the missing engine piece).
- **DecisionPrompt hard gate** for approval (Phase-1.5 hardening; the prose gate
  ships first — see "Approval gate").
- **Headless `--mission -f` auto-approve** runner (thin follow-on once the loop
  is proven; it is the same loop with the gate auto-approved).
- Mission Control / live multi-worker dashboard (Factory's TUI; far future).

## What we copied from Factory Droid (the reference)

| Factory concept | What we take | What we defer |
| --- | --- | --- |
| **Specification Mode** (ask → written spec + plan → approve before any change) | The whole front half: draft a mission file, gate on approval | — |
| **Mission file** (`droid exec --mission -f mission.md`) | A markdown mission file is the interface/handoff surface | the headless runner itself |
| **Orchestrator** (plans, delegates, never does heavy lifting; an agent you can talk to) | The single agent plays orchestrator **and** worker for now | separating the roles |
| **Worker** (`--worker-model`, one slice) | Milestones are disjoint by construction (worker-ready) | actually spawning workers |
| **Validator** (`--validator-model`, higher effort, checks before accept) | Per-milestone `validate:` field; run **inline** today | a dedicated validator agent |
| **Milestones / validation frequency** | Ordered milestones with per-milestone validation | — |

## Decisions (locked in brainstorming)

1. **Both, spec first.** Design Spec Mode + the mission-file format together;
   the mission file is what Spec Mode writes, so the headless run is a thin
   follow-on (deferred).
2. **Design the seam, build single.** Single-agent execution now; file + loop
   shaped so worker/validator fan-out drops in later. Validation runs inline.
3. **Approval gate: prose now, hard later.** The skill instructs the agent to
   write the file, then STOP and ask `approve / edit / cancel` before executing
   (same discipline as create-job's guess-first gate). The DecisionPrompt hard
   gate (reusing PR #75 machinery) is documented as Phase-1.5 hardening.
4. **Files live in the persona workspace.** `<workspace_dir>/missions/<slug>.md`
   — persona-scoped state, consistent with memory. Resolves from
   `state.workspace_dir` at the turn chokepoint, never per-constructor (the
   persona rule). Inherits perm-gate path confinement for free (workspace_dir is
   already in `_allowed_roots`, acp_agent.py:693).
5. **Planning is general, not code-specific.** Rename/generalize
   `planning-before-coding` → `planning-before-doing` (domain-neutral). Missions
   **extends** it: one source of truth for "what a good plan nails", now neutral.
6. **PR1 = all four components** (skill + format leaf + content-gated tool +
   tests) as one coherent unit.

## Architecture — four components

| # | Piece | Where | Mirrors |
| --- | --- | --- | --- |
| 1 | `missions` skill (model-invocable + `/mission`) | `harness/skills/missions/SKILL.md` | sits beside `planning-before-doing`; `mission` flow tag |
| 2 | mission-file format + parser | `harness/missions.py` (new leaf) | `harness/memory.py` (frontmatter, content-gate, slug→path escape-defense) |
| 3 | `mission` tool (write / load / update-milestone), content-gated | `harness/tools/mission.py` + `registry.py` | `harness/tools/load_memory.py` gating on `has_missions` |
| 4 | executor loop = the skill body (prose, no engine code) | inside SKILL.md | `create-job` guess-first gate discipline |
| 0 | generalize planning skill | rename `planning-before-coding` → `planning-before-doing` | — |

### Mission-file format (the fan-out seam)

```markdown
---
mission: slack-notify-on-failure
status: draft        # draft → in_progress → done  (also: cancelled)
created: 2026-06-29
---
# Goal
<2–6 sentences: what done looks like>

## Milestones
- [ ] M1: wire the job-failure hook       | validate: pytest tests/jobs passes
- [ ] M2: slack client + post on failure   | validate: dry-run, no token in logs

## Open questions
- <blocking unknowns surfaced BEFORE approval; empty when none>
```

- `status` frontmatter is the lifecycle (draft → in_progress → done / cancelled).
- `[ ]`→`[x]` checkboxes are the **execution-progress** seam.
- `validate:` per milestone is the **validator** seam (inline today; a validator
  agent later).
- Milestones are **disjoint by construction** (the skill instructs the agent to
  scope them so) — the **worker** seam.
- The file is the **only handoff surface** — a future spawn tool hands a worker
  exactly one milestone and writes one structured result back. Zero format
  rework.

### Data flow (single-agent, today)

```
/mission "<ask>"
  → skill loads → agent drafts <workspace_dir>/missions/<slug>.md (status: draft)
  → PROSE GATE: agent stops, prints the milestones, asks "approve / edit / cancel"
  → user: approve
  → agent sets status: in_progress
  → for each milestone M1..Mn (in order):
        do the work with existing tools
        run the milestone's `validate:` inline
        mark [x] + write a one-line result/validation outcome
  → status: done; agent summarizes
```

Headless (deferred) is the **same loop with the gate auto-approved** — which is
why "both, spec first" is cheap: the file is the interface.

### Resolution & gating (mirrors memory exactly)

- `missions.has_missions(workspace_dir) -> bool` — content-gate. The `mission`
  tool's *resume* affordance is advertised only when missions exist (the
  byte-identical no-op rule: an empty workspace registers no dead tool).
- Resolve `workspace_dir` from `state.workspace_dir` (per-session) at the
  compose chokepoint, mirroring `memory.resolve_memory` (acp_agent.py:361-378).
- slug→path mapping uses `is_relative_to(workspace)` escape-defense
  (memory.py:148-151) — rejects `../` cross-persona escape. Belt-and-suspenders
  over the perm-gate, which already confines writes to the workspace.

## Testing surface (listed before code)

- `missions.py`: parse / round-trip a mission file; `has_missions` true & false;
  slug escape-defense rejects `../`; status transitions; checkbox flip.
- `mission` tool: content-gate (absent when no missions, present when some);
  write rejects paths outside workspace.
- skill/flows: appears under the `mission` flow; reachable via `/mission`;
  `planning-before-doing` still in catalog under its new name.
- **No-op invariant:** a workspace with no missions advertises no mission-resume
  tool and changes nothing.

## Blast radius of the planning rename (mechanical, contained)

`planning-before-coding` → `planning-before-doing` touches:
- `harness/skills/planning-before-coding/SKILL.md` (rename dir + generalize body)
- `harness/skills/NOTICE.md:25`
- `harness/skills/ask-done/SKILL.md:30`
- `docs/router-flows.md:120`
- `tests/test_flows.py:35`, `tests/test_system_skills.py:9`, `tests/fake_agent.py:80`

## Open questions

- None blocking. (Headless runner, hard gate, and real fan-out are explicitly
  deferred above, not open.)
