# Persona fleet — architecture pressure-test

**Status:** research / adversarial review (companion to the persona-fleet roadmap)
**Date:** 2026-06-26
**Author:** Alberto Vasquez (with Claude Opus 4.8)
**Reviews:** [`2026-06-26-persona-fleet-design.md`](2026-06-26-persona-fleet-design.md)
**Method:** Treat decisions D1–D5 and the phase plan as hypotheses; attack each
against the *live code seams* the roadmap makes claims about. Seams read at this
date: `harness/acp_agent.py`, `harness/acp_session.py`, `harness/skills.py`,
`harness/tracing_agent.py`, `harness/paths.py`, `harness/router.py`,
`harness/acp_main.py`.

---

## 0. How to read this

This document does **not** reject the persona-fleet design. The spine of D1/D2/D5
is sound and the "one engine, files-as-persona" bet is the right cheap bet. What
follows is a list of places where the roadmap's recurring claim — *"this lands on
an existing seam, no new machinery"* — is **false or unverified** against the
code, plus a phase-ordering inversion. Each finding cites the file/line that
contradicts (or fails to support) the roadmap.

The roadmap should be revised, not abandoned. §6 lists the concrete changes to
fold into the per-phase brainstorm cycles.

---

## 1. Headline finding — the injection seam reaches one of four dispatch paths

**Roadmap claim (§4):** persona injection "becomes a second injected block
*alongside the skill block* — same seam, parallel source," riding the existing
`acp_agent.py` → `TracingAgent(skill_block=…)` machinery with "no new injection
seam."

**What the code actually does:**

- The skill block is composed **only on the agent path**, at `acp_agent.py:162`,
  *after* the router has classified (`:111`) and *after* the chat, clarify, and
  ambiguous branches have already returned (`:132` clarify, `:158` chat).
- It is injected in `tracing_agent.py:46–48`, which appends it **only to**
  `self.config.system_template` — i.e. only inside `TracingAgent`, which **only
  the agent path constructs** (`acp_agent.py:270`).

So a persona block riding "the same seam" would be invisible to three of the four
things a turn can become:

| Dispatch path | Where it runs | Sees `skill_block` / persona? |
|---|---|---|
| `code_*` / `ops_task` (agent) | `_run_agent_turn` (`acp_agent.py:166`) → `TracingAgent` ctor (`:270`) | **yes** |
| `chat_question` | `ChatHandler` (`acp_agent.py:137`) | **no** — never touches `skill_block` or `TracingAgent` |
| `ambiguous` / `needs_clarification` | returns at `acp_agent.py:132` | **no** — returns before composition |
| router classification itself | separate cheap model, fixed prompt (`router.py:56`, `:107`) | **no** — persona-blind by construction |

**Why this is the headline:** it directly contradicts **D3**, the roadmap's
self-described "most important principle." D3 states that the *identical* pipeline
runs for every persona and that character lives entirely in injected text. But the
pipeline has **four entry points and the persona text reaches one.** A persona
whose `SOUL.md` says "be terse, never explain" would be terse when writing code
(agent path) and chatty when answering a chat question (`ChatHandler`), because
`ChatHandler` never sees `SOUL.md`. Persona identity would blink on and off
depending on how a cheap classifier routed the turn. That is not "emergent
character from files" — it is a split personality gated by the router.

**Required decision (Phase A):** "inject alongside skills" is underspecified to
the point of being wrong. Pick one:

- **(a) Pre-router injection** — assemble persona context once, before the
  classify/fork, and thread it into *all* of `ChatHandler`, `TracingAgent`, and
  (optionally) the router prompt. New seam, but honest.
- **(b) Triple injection** — inject persona text at each of the three consumers
  that produce user-visible output (router system prompt, `ChatHandler`,
  `TracingAgent`). More wiring, preserves per-path control.

The roadmap accounts for neither. This is the one finding to force back into
Phase A's brainstorm before any code.

---

## 2. Decision-by-decision

### D1 — persona fleet in the engine — **sound; one-way-door framing risk**

No structural objection. Caveat: Phase A puts persona-file *reading* (and a fixed
opinion about `SOUL.md` / `IDENTITY.md` / … layout) inside the engine. Because
"the engine is the product and clients consume it over ACP" (AGENTS.md #6), every
future ACP consumer inherits that workspace-layout opinion. That is a one-way
door — fine if intended, but name it before Phase A.

### D2 — persona = workspace, one engine — **sound and genuinely cheap**

The single-process model holds; no supervisor/subprocess needed for isolation.

**But:** the roadmap's §4 lists "SessionStore + paths.py gain a persona/workspace
dimension" as an *extension*. In reality `SessionStore` is **purely in-memory** —
a plain `dict` with no disk layer at all (`acp_session.py:21`). Per-persona
session **and memory** isolation (Phase C) is therefore not "extend SessionStore";
it is "give SessionStore the persistence layer it does not have." That is a
materially bigger Phase C than the roadmap implies (see §3).

### D3 — untyped, mutable personas — **principle right; mechanism unverified**

Two problems:

1. **Injection reach** (see §1) — the mechanism that is supposed to make all
   personas run an identical pipeline only reaches the agent path.
2. **"Memory is the evolution mechanism" collides with first-turn-only
   injection.** The roadmap injects persona + memory on the **first turn of a
   session only** (its own §4) and sessions are in-memory (`acp_session.py`). So a
   write to `MEMORY.md` at turn 5 never re-injects within that session — the
   persona cannot *learn mid-session*; it only sees the new memory on the **next**
   session (a fresh first turn). That may be acceptable, but the roadmap presents
   memory-as-evolution as a live feature without noting that its evolution
   latency is "one full session," and that "evolving" really means "re-read on the
   next process/session start."

### D4 — `persona.toml` + fallback layer — **concrete precedence bug waiting**

Today the worker model has **two** writers already:

- live hot-swap via `harness/set_model` (`acp_agent.py:55`), which also
- **persists to `done.conf`** (`acp_agent.py:60`).

D4 adds a **third** writer: a persona's `persona.toml` preferred model. The
roadmap says "unset fields fall back to engine/global defaults" but never answers:
when a user `/models`-swaps *inside* a persona that pins a model, where does the
swap land? If it writes `done.conf`, the next session re-reads `persona.toml` and
**clobbers the swap** ("why did my model reset?"). If it writes `persona.toml`,
a live experiment silently rewrites the persona's committed config. This is the
exact shape of a precedence bug.

**Required (Phase A/D4):** write the precedence ladder explicitly
(`persona.toml` ↔ live `set_model` ↔ `done.conf` ↔ `--model`/env default) **and**
state where a live swap persists. Note CLAUDE.md flags model-persistence and
config-precedence as expensive-to-get-wrong; this is Codex-review territory.

### D5 — no channels; crons in a client — **mostly sound; one unsolved snag**

Crons-as-ACP-consumer mirrors the existing TUI-as-consumer pattern — good, keeps
the engine scheduler-free per AGENTS.md #6.

**Snag:** a cron consumer fires a prompt with **no user present**. Every current
dispatch path that runs a command assumes either a client that can answer
permission prompts (`acp_agent.py:204`, gated on `elicitation` capability) or an
auto-allow fallback. A headless cron consumer has no one to approve a command, so
Phase E inherits an unsolved choice: either every cron run is implicitly `--yolo`
(`acp_agent.py:42`, a security smell the project explicitly routes to Codex
review) or crons can only run personas whose tasks never need permission. The
roadmap does not mention this. **Name it in Phase E scope.**

---

## 3. Phase-ordering attack

The dependency graph `A → B → C → {D,E}` has one inversion and one deferred
safety item.

### B (memory) depends on isolation that lives in C

Phase B builds persistent memory. But *meaningful* memory is **per-persona and
persisted** — and both persistence and per-persona isolation are **Phase C**
(D2 note above: SessionStore has no disk layer today). So "Phase B on top of
Phase A" yields a **single global `MEMORY.md` for the one default persona, with no
isolation**, and then Phase C must retrofit isolation *underneath an
already-shipped memory writer*. Either memory gets built twice or B is explicitly
a single-persona throwaway prototype.

**Options:**

- Fold the **persistence + per-persona core of C into B** (give SessionStore its
  disk/isolation layer as the first thing B does, since B needs it anyway), or
- Explicitly relabel B as "single-persona memory only; isolation arrives in C"
  and accept the rework.

The roadmap's rationale ("memory early because it is the evolution substrate") is
philosophically right; the mechanical dependency (isolation) is just mis-placed a
phase late.

### D (attestation) is data-loss protection arriving after the data exists

`BOOTSTRAP.md` attestation exists to **refuse to re-seed a wiped-but-real
workspace** — i.e. it is clobber/data-loss protection. The roadmap defers it to
Phase D, but the clobber risk is born the moment **Phase C** lets users create
real workspaces with real memory. Attestation arriving a phase after the data it
protects is a gap. **Move clobber-protection up to whenever real persisted
workspaces first exist (C).**

---

## 4. Smaller but real

- **No persistence story for the in-memory store anywhere in the roadmap.**
  Sessions vanish on process exit today (`acp_session.py`, in-memory dict). The
  fleet stacks long-lived, file-backed personas on a session layer that forgets
  everything on restart. The roadmap never reconciles "durable persona" with
  "ephemeral session." Decide before C.

- **Trim/truncate "ports directly" is asserted, not verified.** §4 says
  OpenClaw's blank-skip and trim-truncate rules "port directly" to the injection
  seam. `skills.compose` has **no size limiting** — it concatenates whole skill
  bodies (`skills.py:84–89`). The seam persona injection is supposed to reuse does
  not provide truncation; it would have to be built. Another "lands on existing
  machinery" claim the machinery does not back.

- **The router is permanently persona-blind.** Its system prompt is fixed
  (`router.py:56`) and it runs on a separate cheap model (`router.py:27`,
  `ROUTER_MODEL`). A persona meant to "always treat my input as code work" cannot
  bias the one component that decides *which path runs*. D3 says specialization is
  emergent, but the routing decision can never be part of that emergence. This is
  a hard ceiling on how far a persona can "drift," and it interacts with §1: even
  if persona text reached all paths, the *choice* of path stays persona-blind.

---

## 5. What holds up

To keep this honest — these roadmap claims survived the pressure-test:

- **D2's single-process isolation model.** Correct that no supervisor /
  subprocess-per-agent is needed; one engine + selected workspace is sufficient.
- **D5's crons-as-separate-consumer** (modulo the permission snag) is consistent
  with the existing TUI-as-consumer architecture and keeps the engine
  scheduler-free.
- **The general "files-as-persona, untyped, no type enum" stance (D3)** is the
  right anti-pattern-avoiding choice; it mirrors the committed Phase-3 decision
  that skills are prompt-driven and `task_type` must never branch behavior. The
  *principle* is sound; only its *injection mechanism* is underspecified.
- **Phase F (sub-agents) deferral** is correctly prioritized last and correctly
  flagged to co-design with the GitHub-PR-worker track.

---

## 6. Changes to fold into the per-phase brainstorms

1. **Phase A — re-open the injection question.** "Second block beside skills" is
   wrong as written (reaches 1 of 4 paths, §1). Decide pre-router injection vs
   triple injection; whichever, persona text must reach `ChatHandler` and the
   agent path at minimum, and the truncation mechanism (§4) must be built, not
   assumed.
2. **Phase A/B — give `SessionStore` a persistence + per-persona layer as its own
   foundational piece**, and pull it *into or before* memory (B), not after (C).
   SessionStore is in-memory-only today (§2 D2).
3. **Phase C — move attestation / clobber-protection up** to whenever real
   persisted workspaces first exist (§3).
4. **Phase A/D4 — write the model-precedence ladder explicitly**, including where
   a live `/models` swap persists relative to `persona.toml` and `done.conf`
   (§2 D4).
5. **Phase E — name the cron permission-delegation problem** in scope: a headless
   consumer has no one to approve commands (§2 D5).
6. **Cross-cutting — state the mid-session evolution latency** ("memory re-reads
   on the next session, not mid-turn") wherever the roadmap calls memory the
   evolution substrate (§2 D3).

None of these is fatal. The pattern to internalize: D2's process model genuinely
*does* land on existing seams; injection, truncation, and persistence are
presented as free and are **not**. Budget for them.
