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
