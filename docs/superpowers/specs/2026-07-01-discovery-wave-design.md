# Discovery-Wave — fan-out at the front of brainstorming

**Date:** 2026-07-01
**Status:** Design (brainstorming → spec). Adversarially reviewed (Codex, 5
findings — all incorporated: fail-open labeling, `context-only` traceability,
width caps, absence-claim search-scope, form recommendation flipped to
enforced-script-first). §5 form decision **resolved** (2026-07-01): form (c),
orchestrator-driven — see §5. Awaiting user review, then writing-plans.
**Author:** brainstormed with Claude Code (Opus 4.8)

---

## 1. Problem

The front of `superpowers:brainstorming` is single-threaded by design. Two steps
in its checklist generate all the value and both run in one head:

- **Step 1 — "explore project context"** (discovery): read files/docs/commits serially.
- **Step 4 — "propose 2–3 approaches"** (creativity): generate options from one context.

The user wants planning to be **richer and more creative up front** by fanning
out both steps, WAVES-style (Workers · Aggregate · Verify · Extend), *before any
code is written*.

**Why this is the right place to fan out.** Brainstorming has a HARD-GATE: no
code, no scaffolding, no implementation skill until the design is approved. So the
entire phase we are parallelizing is **pure read / research / analysis** — the one
case where WAVES has no downside (its only hard rule is "never parallelize writes
to shared files / shared mutable state"). We fan out exactly where it is safe.

**Explicit non-goal.** This is NOT for `/loop`, `/goal`, or cron **missions**.
Those are *stateful, durable, long-lived* orchestrations (durable mission state
across ticks, supervision/handoff, validator write-back). WAVES is *stateless and
bounded*. Bolting a bounded-round pattern onto a durable-state orchestrator fights
both designs. The target is the brainstorm front-end **only**.

## 2. Success criteria (priority ranking — the north star)

Optimize in this order; when they trade off, sacrifice the lowest first:

1. **Fewer downstream surprises** (plan correctness) — *dominates.*
2. **Better options** (creativity).
3. **Faster discovery** (coverage / speed) — *expendable.*

**Load-bearing implication:** because #1 beats #3, a discovery wave that returns
fast but with *unverified* claims is a net **negative** — it manufactures
confident-but-wrong facts that poison the spec. Verification is therefore not an
optional QA pass; it is the load-bearing wall. Speed yields to it.

## 3. Design

Two bounded waves run in sequence at the front of brainstorming: a **discovery
wave** (facts) then an **approach wave** (creativity built on those facts). The
orchestrator (main thread) plans, reads handoffs, and synthesizes; it does not do
the heavy lifting.

### 3.1 Wave 1 — Discovery (facts)

1. **Decompose** the goal into *disjoint, read-only* slices. Typical slices:
   affected subsystems, prior-art-in-this-codebase (does it already exist?),
   constraints, external research.
2. **W — Finders** fan out, one per slice. Reuse the read-only `Explore` agent
   (it reads excerpts, locates code, cannot write). Each finder returns a
   structured handoff; every claim carries:
   - a tag: `will-shape-design | context-only`
   - a confidence: `high | med | low`
   - cite-or-drop: a `file:line`, URL, or metric, or the claim is dropped.
3. **A — Aggregate** at a barrier: wait for all finders, merge handoffs.
4. **V — Verify (default-on adversarial moat).** This is the priority-#1 wall.
   - Only `will-shape-design` claims are verified (bound by **relevance**).
   - Those claims are **batched by slice/file** into adversarial **verifier**
     workers (see §3.4 for the hard count caps). A verifier seeing related claims
     can also catch **contradictions between them**.
   - Verifiers are prompted to **refute**, defaulting to `unverified` when they
     cannot confirm the cited evidence says what the claim says. A verifier's
     handoff must state *what it checked* (the file:line it re-read, the command
     it re-ran) — a bare `verified` with no evidence trail is treated as
     `unverified`. (Answers "who verifies the verifier": the evidence trail does;
     the orchestrator can spot-audit any `verified` claim against its trail.)
   - **Absence / negative claims** ("this capability does not already exist") are
     the highest-value discovery claims *and* the ones a fresh verifier is worst
     at — it can miss a differently-named module and stamp `verified`, producing
     exactly the surprise #1 exists to prevent. So a negative claim may be marked
     `verified` **only** when the verifier states its **search scope** (the terms,
     paths, and synonyms searched) and that scope is broad enough to be credible.
     Otherwise it stays `unverified` and, if design-shaping, feeds the Extend
     trigger (§3.1.5-B).
   - `context-only` claims skip verification but appear in the brief **flagged
     `unverified`**, so they can never silently become spec assumptions. But the
     tag is a *guess made before the design exists* — see the traceability rule
     (§3.3) that pulls any such claim back through verification if it later shapes
     the design.
   - **Why dedicated verifiers, not orchestrator-inline:** the orchestrator is the
     same context that decomposed the slices and will write the spec — it anchors
     toward its own conclusion. A fresh, isolated skeptic with no stake in the
     design is structurally more likely to catch the "this already exists"
     surprise. Verify is a real fan-out stage, not an inline pass.
5. **E — Extend (bounded).** Fire **at most one** narrow re-wave, and only for:
   - **(A) a coverage gap** — a slice returned nothing, or a design-shaping
     question the first wave did not answer; or
   - **(B) a refuted / contested design-shaping claim** — a verifier refuted a
     claim the design leans on, or two verifiers conflict.
   Hard cap: **max 2 discovery waves total.** Everything else (context-only
   unknowns, low-stakes gaps) is flagged `unverified` in the brief as an explicit
   design risk the user sees — not chased automatically.

   → **Output: a verified discovery brief.** Every design-shaping fact is either
   `verified` or explicitly `unverified / contested`.

### 3.2 Wave 2 — Approach (creativity, built on the brief)

6. The orchestrator reads the verified brief and **derives the 2–3 real tensions**
   for *this* problem — the axes where the problem actually forks (e.g.
   "sync vs async", "buy vs build the verifier"). Frames fit the problem, not a
   template.
   - **Checkpoint:** the derived frames are **shown to the user before the
     generators fan out.** Frame-derivation is a single orchestrator judgment; if
     the read is wrong, the whole approach wave points at the wrong forks. Showing
     the frames catches a bad read for the price of one message instead of a
     wasted wave.
   - **Fallback (A):** if the brief yields no obvious tension, fall back to fixed
     lenses — *MVP-first* (smallest thing that works), *risk-first* (what breaks,
     design around it), *reuse-first* (maximize existing harness machinery),
     *greenfield* (ignore constraints, ideal design).
7. **W — Generators** fan out, one per tension-pole, **isolated** (isolation gives
   divergence for free — generators cannot anchor on each other).
8. The orchestrator **synthesizes** the returns into the 2–3 options that
   brainstorming's checklist step 4 expects, then hands back to the normal
   `present design → write spec → writing-plans` flow.

### 3.3 Traceability rule (closes the `context-only` loop)

The `will-shape-design | context-only` tag is a **guess made before the design
exists**. A finder can tag a claim `context-only` (so it skips verification), yet
the orchestrator may later lean on that same claim to derive an approach frame
(§3.2) or write the final spec — at which point it *became* design-shaping after
the tag was assigned. Without a rule, an unverified claim silently reaches the
spec, defeating priority #1.

**Rule:** *any* brief claim that ends up cited in a derived approach frame or in
the final spec MUST be `verified`. If such a claim was tagged `context-only` (and
therefore unverified), it is **pulled back through the verifier** (a targeted
mini-verify, counts against the §3.4 caps) before it may shape the design. The
orchestrator maintains a claim→spec trace so this check is mechanical, not
memory-based: every spec assumption resolves to a `verified` brief entry, or it
does not enter the spec.

### 3.4 Hard cost caps (bounds **width**, not just depth)

"Bounded by design" must cap agent *count*, not only round count. The Extend cap
(§3.1.5) bounds **depth** (≤2 waves); these bound **width**. Concrete governors
(defaults; tune per persona):

- **Slices per wave:** ≤ 8 finders. A goal that decomposes into more is *too
  broad for one wave* — the orchestrator narrows scope or picks the top-8 slices
  by design-relevance and `log`s what was deferred (no silent truncation).
- **Verifiers per wave:** ≤ 8 (design-shaping claims batched by slice/file into at
  most this many adversarial verifiers).
- **Re-wave:** the single permitted re-wave (§3.1.5) is *narrow* — ≤ 3 finders +
  ≤ 3 verifiers targeting only the gap/contested area.
- **Tension-poles (approach wave):** ≤ 4 generators.
- **Overall:** worst-case total ≈ 8+8 (wave 1) + 3+3 (re-wave) + 4 (approach) ≈
  **26 agents**, a hard ceiling. Optionally enforce via the `Workflow`
  token-budget pattern (scale caps down when the turn's budget is small).

### 3.5 Degradation (fail-open — matches the harness router philosophy)

The router is "best-effort triage, NOT a hard gate; degrade to the worker rather
than refuse the turn." The discovery wave inherits that stance — **but** fail-open
must not smuggle unverified facts past the priority-#1 wall:

| Failure | Behavior |
|---|---|
| A slice returns nothing | Treated as a coverage gap (feeds the Extend trigger). |
| Verifiers disagree | Claim = `contested / unverified`; **never** treated as fact. |
| Whole wave fails | Fall back to **linear** brainstorming — AND the entire resulting brief is stamped **`unverified — degraded mode`**, so no fact from it is treated as verified downstream (§7.1 still applies to it). |

The wave never blocks the user; worst case it costs nothing over the status quo —
but a degraded run is *labeled* degraded, not silently trusted.

## 4. Reuse (no new infrastructure)

- **`Explore` agent** — read-only finders (reads excerpts, locates code, cannot write).
- **`Workflow` tool** — orchestration. The canonical `pipeline(finders) → verify`
  pattern (fan out → adversarial-verify each finding as its review completes) is
  a direct fit. Bound the verifier fan-out per §3.4.
- **`flows.py`** — wiring is pure data (a frontmatter `flows` tag + a `persona.toml`
  line). New flow families need **no router edits**.

## 5. Open decision — the form (deferred: "spec first, decide later")

How this lives in the harness. Three options; recommendation follows.

- **(a) New `discovery-wave` skill, tagged into a flow.** `superpowers:brainstorming`
  (or the local `planning-before-coding` skill) invokes it at the explore/approach
  steps. Cleanest separation; wired via `flows.py` as pure data, no router edits.
  Reusable outside brainstorming (audits, pre-mission scoping).
- **(b) Edit `planning-before-coding` directly.** Bake the fan-out into the
  existing planning skill. Fewer moving parts, but couples the pattern to one skill
  and is not reusable elsewhere.
- **(c) Reusable `Workflow` script** the orchestrator runs at brainstorm time. Most
  flexible, least automatic — nothing invokes it on its own; the human/agent must
  remember to run it.

**Recommendation: (c) first, graduating to (a).** The safety argument in §1 rests
on brainstorming's HARD-GATE (no writes) — that is what makes fanning out safe.
But (a) advertises reuse in "audits, pre-mission scoping," contexts where that
gate may be **absent**, so recommending the most-reusable/automatic form *before*
the caps (§3.4), the read-only finder guarantee, and the traceability rule (§3.3)
are **mechanically enforced** is a trap: it exports the fan-out to gate-less
contexts on the honor system. The `Workflow` script (c) is where read-only
(via the `Explore` agent), the §3.4 count caps, and the token-budget governor are
*enforced in code*, not in prose. So: ship (c) as the enforced building block;
**graduate to (a)** (flow-tagged skill wrapping that script) once those
constraints are mechanical and proven, at which point reuse in audits/pre-mission
scoping is actually safe. (b) is rejected — it couples the pattern to one skill
and is not reusable.

**RESOLVED (2026-07-01):** Form = (c) orchestrator-driven, built on the existing
`subagent` tool (no `Workflow` engine exists in the harness). The `discovery-wave`
skill encodes the protocol; caps are prompt-enforced; token budget deferred. See
`docs/superpowers/plans/2026-07-01-discovery-wave.md`. Graduation to a richer
form (a) remains available since the skill is already flow-tagged.

## 6. Out of scope

- Any change to `/loop`, `/goal`, cron, or missions.
- Parallelizing the *write* phase of development (violates WAVES' one hard rule).
- Installing the upstream Cursor `/waves` skill as-is (Cursor-flavored, sits
  outside this flow; we reuse the *pattern*, not the packaging).

## 7. Verification / success test

The addition is a win under the ranking iff, on a representative planning task:

1. **(#1)** Every fact that shapes the resulting spec resolves (via the §3.3 trace)
   to a `verified` brief entry, or is explicitly flagged `unverified/contested` —
   no unlabeled assumption reaches the spec. This holds even for claims originally
   tagged `context-only`. *(Primary gate.)*
2. **(#2)** The approach wave surfaces ≥1 option along a tension the orchestrator
   would not have reached in a single linear pass.
3. **(#3)** Discovery coverage (slices checked) meets or exceeds a linear pass —
   without violating #1, and within the §3.4 width caps.
4. **Fail-open:** killing the wave mid-run drops cleanly back to linear
   brainstorming with no user-visible breakage — **and** the degraded brief is
   stamped `unverified — degraded mode` (§3.5), so #1 is not silently bypassed by
   the fallback path.
