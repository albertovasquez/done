# EPIC: Get Missions Done

## 1. The thesis

"Get Missions Done" is the claim that a personal agent should be able to take a real, multi-step objective and finish it over time — across process restarts, across a schedule, across parallel work — without a human babysitting every step, and without the user losing the ability to trust, audit, or stop it. Done is uniquely positioned to make that claim because it already owns three of the four primitives the problem requires and they share one storage substrate. A **persona** is a mutable workspace on disk (SOUL/IDENTITY/USER + persistent memory + model binding). **Cron** lets that persona act on a timer as an OS service that survives reboot. A **mission** is a gated, milestone-tracked markdown file that lives *inside the persona workspace*. The only missing piece is **subagent fan-out**. The combination is more than the sum because each primitive cancels the others' worst failure mode: the mission file converts a fragile in-context 20-step chain (which compounds to roughly 20% end-to-end success at 85% per-step reliability) into many short, independently-verified runs that persist; cron supplies the durable clock that lets one validated milestone advance per tick; subagents supply fresh context per milestone so drift cannot accumulate; and the gated mission file is the human-readable trust surface that makes leaving it running overnight a defensible decision rather than a bomb to defuse. No competitor combines file-based mutable persona, in-process subagent fan-out, OS-service cron, and a gated milestone plan in one local product.

## 2. The unique mechanism

The mechanism is a single loop where all four primitives meet on **one durable artifact: the mission file in the persona workspace.**

```
persona (workspace + memory + model)
   │  owns
   ▼
mission file  ──  disjoint milestones, each with a `validate:` assertion,
   │              status frontmatter draft→in_progress→done, [ ]→[x] cursor
   │
   ├── cron tick ──►  executor builds the persona-faithful agent, reads the
   │                  mission file, picks the next [ ] milestone, stamps a
   │                  wall-budget (env._remaining_secs from job.cost.timeout_s)
   │
   ├── subagent fan-out ──►  one milestone → one fresh-context worker (depth-1,
   │                  per-worker step+wall caps, sibling isolation), whose ONLY
   │                  handoff surface is the mission file's milestone text
   │
   ├── validate ──►  worker result checked against the milestone's `validate:`
   │                  assertion; outcome written BACK to the mission file as a
   │                  durable line (pass/fail + one-line result + which worker)
   │
   └── checkpoint ──►  on pass: [ ]→[x] persisted to disk, mission advances.
                       on fail: orchestrator spawns a "fix milestone."
                       on ambiguity / risky grant / low confidence: HOLD —
                       persist checkpoint, escalate to a review queue, resume
                       on the human's answer.
```

What makes this Done's and no one else's: the scheduler, the persona workspace where missions live, and the milestone format already share one filesystem substrate. Factory Droid runs the orchestrator/worker/validator loop, but only *inside one continuous run* — it is not scheduled and not resumable across process restarts; it externalizes state to artifacts within a single 16.5h session. Codex Automations are scheduled, but each run lands in a review queue as a discrete unit with no carried mission state. ChatGPT and Gemini scheduled tasks are stateless check-ins (capped at once/hour and 10 actions respectively, no tools, no files). Done is the only product where a *scheduled* tick can read, advance, validate, and checkpoint a *persisted* mission owned by a *persona* with its *own memory* — and where the same gated file a human reads to approve an interactive mission is the surface an unattended run pauses against. The runtime plumbing is 90% there: the cron executor already builds the persona-faithful agent and already stamps the subagent wall-budget onto a scheduled turn. The missing pieces are a Mission payload, a milestone cursor in JobState, the subagent spawn tool, and the write-back-and-checkpoint step.

## 3. Why now / the wedge

Three verified findings define the white space:

- **Reliability over time — not capability — is the binding constraint.** Temporal's math is the case for the whole epic: even at 85% per-step reliability, a 10-step workflow succeeds end-to-end only ~20% of the time, and longer workflows degrade further ([temporal.io](https://temporal.io/blog/ai-reliability-is-a-decade-old-problem)). Kapoor and Narayanan reframe the gap as reliability science — unpredictable tail-failure, not average success, is what blocks unsupervised completion: "an agent that succeeds on 90% of tasks but fails unpredictably on the remaining 10% may be a useful assistant yet an unacceptable autonomous system" ([Fortune](https://fortune.com/2026/03/24/ai-agents-are-getting-more-capable-but-reliability-is-lagging-narayanan-kapoor/)). A mission that advances one validated, disjoint, fresh-context milestone per tick is reliability-by-architecture — it attacks the compounding-error math directly.

- **The handoff is the unsolved product primitive.** "The real skill isn't building the agent — it's designing the handoff" (Whitehead, [Fortune](https://fortune.com/2026/02/23/always-on-ai-agents-openclaw-claude-promise-work-while-sleeping-reality-problems-oversight-guardrails/)). The headless-cron literature names the exact failure mode: feeding an interactive prompt ("confirm first") to a scheduled run *stalls* it because nobody answers ([Hidekazu Konishi](https://hidekazu-konishi.com/entry/claude_code_cicd_and_headless_automation.html)). The Fortune email-agent anecdote — "I had to RUN to my Mac Mini like I was defusing a bomb" — is precisely the missing pause-checkpoint-escalate loop. Done already has the interactive approve/edit/cancel gate; the wedge is making that same gate work *asynchronously* against a durable mission file.

- **The defensible value is the boring reliability layer, and Done already ships it fail-closed on the exact surface that destroyed the leading comp.** Every major assistant shipped scheduling as a thin layer over the same fragile loop; the hard wins are locks, budget caps, and scoped permissions ([Hidekazu Konishi](https://hidekazu-konishi.com/entry/claude_code_cicd_and_headless_automation.html)). Meanwhile OpenClaw went from viral to security crisis in three weeks — CVE-2026-25253, 341 malicious ClawHub skills, 258,305 exposed instances, a CNCERT warning about fail-open file access ([Coral](https://www.coral.inc/blog/2026-03-07-openclaw-security-crisis-2026), [The Hacker News](https://thehackernews.com/2026/03/openclaw-ai-agent-flaws-could-enable.html)). Done's CostGate (timeout, cadence, max-consecutive-failures), per-job Grant (tools/paths/write/exec/network), O_EXCL single-instance daemon lock, and deny-by-default fail-closed path-confinement are the precise mitigations these findings name. "Always-on agent you can trust on your machine" is a credibility moat, not a slogan.

## 4. The phased arc

**Phase 0 — Interactive missions (spec'd, single-agent).**
*Ships:* `/mission` turns a short ask into a gated, milestone-tracked markdown file in the persona workspace (status draft→in_progress→done, disjoint milestones, per-milestone `validate:` field). One agent executes sequentially with an interactive approve/edit/cancel prose-gate.
*Unlocks:* the durable handoff surface and the disjoint-by-construction milestone contract — every later phase writes to this file.
*Depends on:* nothing new; the format is deliberately shaped so worker/validator fan-out drops in with zero rework.

**Phase 1 — Wire mission ↔ subagent fan-out.**
*(Correction vs. the original framing: the subagent primitive is NOT missing — it shipped in PR #173, `harness/tools/subagent.py`, with depth-1 enforcement, per-worker step+wall caps, sibling isolation, and content-gating; verified against live code 2026-06-29. Phase 1 is therefore wiring the existing spawn tool to the mission file, not building the primitive.)*
*Ships:* the orchestrator hands one existing-subagent worker exactly one milestone (depth-1, per-worker step+wall caps, sibling isolation, content-gated, stateless: memory_root=None, skill_roots=None), then **writes one structured result back into the mission file** — pass/fail + one-line result + which worker — and flips `[ ]→[x]` on pass. An inline validator checks the milestone's `validate:` assertion; on fail the orchestrator spawns a "fix milestone."
*Unlocks:* fresh context per milestone (drift cannot accumulate across the mission) and the orchestrator/worker/validator loop **on persisted state** — which neither Factory (no persistence across runs) nor Anthropic Research (ephemeral, "not for code") does. It also answers Anthropic's documented fan-out failure modes (vague handoffs → duplicated/gapped work) because the disjoint milestones *are* the explicit per-worker objective+boundary contract.
*Depends on:* Phase 0's mission format. This is the seam everything points at; today the subagent digest is funneled straight into the parent's context and discarded.

**Phase 2 — Scheduled autonomous missions (cron + mission).**
*Ships:* a Mission payload in the jobs system and a milestone-cursor field in JobState, so a scheduled tick reads the mission file, advances **one validated milestone**, checkpoints to disk, and stops. Plus the async handoff surface: on ambiguity, a risky/irreversible Grant, or low self-confidence, the run **pauses, persists a checkpoint, and escalates to a review queue**, resuming from that checkpoint on approval. Per-milestone autonomy boundary: auto-advance read-only / working-tree milestones; HOLD-and-escalate any milestone whose `validate:` or grant touches irreversible/network/push actions.
*Unlocks:* missions that survive process death and run overnight as an OS service — the literal intersection of all four primitives, which no platform does.
*Depends on:* Phases 0–1 and the existing executor (which already builds the persona-faithful agent and stamps the wall-budget) and the existing Grant/CostGate/lock layer.

**Phase 3 — Multi-persona mission delegation.**
*Ships:* a mission owned by one persona can delegate a milestone to a *different* persona (a specialized workspace with its own SOUL/memory/model), with the result reconciled back into the owning mission file.
*Unlocks:* the "fleet of untyped mutable workspaces" acting as differentiated workers under one mission — model specialization per role (the Factory pattern) but with persistent, switchable, file-based personas.
*Depends on:* Phases 1–2 and the in-process persona switch (already not a re-exec). Gated behind hard evidence that cross-persona delegation beats single-persona fan-out for a given mission, given the cost findings below.

## 5. The hard problems

- **Trust and supervision of unattended missions is the make-or-break, and it is genuinely unsolved.** Calibration — an agent knowing when it is wrong — is where even the best models score 25–52% ([Fortune/Kapoor-Narayanan](https://fortune.com/2026/03/24/ai-agents-are-getting-more-capable-but-reliability-is-lagging-narayanan-kapoor/)). If the HOLD-and-escalate trigger fires too rarely we ship the email-agent bomb; if it fires too often the async review queue becomes notification fatigue and the autonomy is theater. The per-milestone autonomy boundary (read-only/working-tree auto-advances; irreversible/network/push HOLDs) is a *structural* proxy for calibration that does not depend on the model judging its own confidence — but it is only as good as the milestone `validate:` and Grant being honest about what each milestone touches. Irreversibility is the sharp edge: Temporal notes many agent stacks have "no mechanism to recover" from a mid-mission hallucination ([Temporal](https://temporal.io/blog/ai-reliability-is-a-decade-old-problem)). Checkpoint-per-milestone gives us a rollback point; it does not undo an external API call already made. The boundary must keep irreversible actions outside the agent's tool surface with the pipeline mediating them.

- **Drift and self-validation only work if the mission is honestly verifiable.** Fresh-context-per-milestone defeats *conversational* drift, but Factory's documented "Feature retry limit reached" failure shows the limit: self-validating QA needs a scriptable way to exercise the work, and most repos lack it ([Factory docs](https://docs.factory.ai/cli/features/missions)). A `validate:` assertion that cannot actually be checked degrades to a worker grading its own homework. We need the validator to fail loudly (escalate) rather than rubber-stamp, and we need an honest answer for missions whose milestones are not mechanically checkable.

- **Cost, and the single-agent engine limit.** Multi-agent systems use ~15x the tokens of chat and "burn through tokens fast" ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)); Factory's reference mission consumed 778.5M tokens. Fan-out must be justified per mission, and Anthropic's own scoping is sobering: most coding tasks have fewer truly parallelizable subtasks than research, and agents "are not yet great at coordinating in real time." Done's subagent fan-out already exists (PR #173) but is unproven on real missions — the open risk is whether missions actually parallelize, not whether we can spawn. We mitigate with hard caps (the existing CostGate/per-worker wall+step caps, max-consecutive-failures) so a runaway loops fails rather than spends — but if real missions don't parallelize, Phase 1's value collapses to "fresh context per milestone," and Phase 3 must stay gated on evidence it beats single-persona fan-out.

## 6. Success criteria

The epic worked if:

1. **A mission survives process death.** Kill the daemon mid-mission; on the next cron tick the persona resumes from the last checkpointed milestone, not from scratch — verifiable by inspecting `[x]` cursor + result lines in the mission file.
2. **Reliability-by-architecture is measurable.** A mission of N disjoint milestones run as N independently-validated fresh-context runs completes end-to-end at materially higher rate than the same ask run as one in-context sequential burn — directly testing the Temporal compounding-error claim on our own workload.
3. **The async handoff actually fires.** An unattended mission hitting a risky/irreversible Grant or failed `validate:` PAUSES, checkpoints, and lands in the review queue — and resumes correctly from that checkpoint on approval — instead of stalling (the headless-prompt failure) or dead-stopping (CostGate auto-disable). No "defuse the bomb" incident.
4. **The validator loop persists, not just runs.** Worker results and validator outcomes are written back into the mission file as durable, auditable pass/fail + result lines; a failed milestone produces a spawned fix-milestone — and the whole mission is reconstructable by reading one file, with no reliance on funneling everything through the parent's context.
5. **It stays fail-closed under the OpenClaw-class threat.** Across all phases, deny-by-default path confinement and per-job Grant hold: an unattended mission cannot touch paths or perform network/push/exec actions outside its grant, and a malicious skill or injected instruction cannot escalate — verified by adversarial test, on the exact surface that produced the OpenClaw crisis.
6. **Cost is bounded and honest.** Every mission reports tokens/cost per milestone; fan-out is only enabled where it demonstrably beats sequential execution; runaway jobs fail against caps rather than spending unbounded.