# Persona fleet — research & phased roadmap

**Status:** research / roadmap (no implementation in this doc)
**Date:** 2026-06-26
**Author:** Alberto Vasquez (with Claude Opus 4.8)
**Reference studied:** [OpenClaw](https://github.com/openclaw/openclaw) — docs at
`docs.openclaw.ai` (concepts/multi-agent, concepts/agent, reference/AGENTS.default)

---

## 1. Purpose

Research what **multi-agent support** would look like for DoneDone (`dn`), using
OpenClaw as the reference implementation, and lay out a phased plan to get there.

The decided target shape (see §3) is a **persona fleet**: DoneDone grows from a
single agent into a set of personas, each defined by plain-text workspace files,
each with its own memory, optionally its own crons — ported *into* our ACP engine
rather than bolted onto a client.

This document takes positions on the open architecture questions and lays out the
phases with dependencies and a recommended sequence. Each phase is its own
`brainstorming → spec → Codex review → writing-plans → subagent-driven-development
→ finishing-a-development-branch` cycle. **No code is written from this doc.**

---

## 2. What OpenClaw actually is (and why it matters)

OpenClaw is **not a coding engine**. It is a *personal-assistant framework*:

- A local-first **Gateway** is the control plane for sessions, channels, tools,
  and events.
- A **multi-channel inbox** (WhatsApp, Slack, Telegram, Discord, …) feeds inbound
  messages in.
- **Multi-agent routing** maps a channel account (a "binding") → one of several
  **isolated agents**, each with its own workspace, state directory (`agentDir` at
  `~/.openclaw/agents/<agentId>/`), auth profiles, model registry, and session
  store.
- **An agent *is* a set of plain-text workspace files** (see §2.1), injected into
  the system prompt on the **first turn of a session**.
- Crucially, OpenClaw lists **"ACP agents — running external coding harnesses"** as
  one of its *tools*, and **"sub-agents — spawning background agent runs"** as
  another.

That last point is the key framing: **OpenClaw is, in part, a *consumer* of ACP
coding engines like ours.** We are not adopting OpenClaw wholesale. We are porting
its *persona / memory / cron* model into our engine while keeping the standard that
the **engine is the product and clients consume it over ACP** (`AGENTS.md` #6).

### 2.1 OpenClaw workspace files (the persona-as-files model)

A workspace directory holds these user-editable files; on the **first turn of a new
session**, OpenClaw injects their contents into the system prompt's "Project
Context". Blank files are skipped; large files are trimmed/truncated with a marker
so prompts stay lean; a missing file becomes a single "missing file" marker line.

| File | Role |
|---|---|
| `SOUL.md` | Persona, boundaries, tone — "who are you?" |
| `IDENTITY.md` | Agent name / vibe / emoji |
| `AGENTS.md` | Operating manual — procedures, session behavior, memory rules, multi-agent handoff. "What do you do and how?" |
| `USER.md` | User profile + preferred address (static context the user writes) |
| `TOOLS.md` | User-maintained tool notes / conventions |
| `BOOTSTRAP.md` | One-time first-run ritual; self-deletes after completion |
| `MEMORY.md` | Long-term memory the agent *grows* over time |
| `memory/YYYY-MM-DD.md` | Daily working notes |
| `HEARTBEAT.md` | Cron-in-plain-English: recurring/proactive tasks |

**Memory protocol** (from `AGENTS.default`): on session start, read today +
yesterday + `MEMORY.md`; read-before-write; write only concrete updates, never
empty placeholders; capture decisions, preferences, constraints, open loops.

**Bootstrap protocol:** `BOOTSTRAP.md` is created only for a brand-new workspace.
While pending, it stays in Project Context with extra system-prompt guidance for
the ritual. After a workspace is observed, OpenClaw keeps a **state-dir attestation
marker**; if a recently attested workspace disappears, startup refuses to silently
re-seed `BOOTSTRAP.md` (prevents clobbering a wiped-but-real workspace).

---

## 3. Decisions that shape this design

These were settled during brainstorming and are load-bearing for everything below.

### D1 — Target shape: persona fleet *in the engine*

Port OpenClaw's persona model **into DoneDone's engine**: multiple isolated
personas, each defined by workspace files, each with its own memory and (optional)
crons. (Rejected alternatives: collaborating coding sub-agents on one task; making
DoneDone merely *drivable* by an external OpenClaw. Sub-agents survive as a
deferred phase — see Phase F.)

### D2 — Isolation model: persona = workspace, one engine

A persona is a **selected workspace directory** loaded per session. One engine
process. Switching persona = switching which workspace the session bootstraps from.
**No supervisor, no subprocess-per-agent, no gateway** (yet). This is the cheapest
model and preserves "the engine is the product." OpenClaw's per-agent subprocess
isolation is explicitly *not* adopted at this stage.

### D3 — Personas are **untyped and mutable** (the most important principle)

A persona is **not** "a coding agent" or "an assistant agent." It is a workspace of
plain-text files that *accumulates a character over time*. The same persona can
drift toward coding, toward personal-assistant work, or sit anywhere between —
depending on what its files say and what it has learned.

Consequences (these are constraints on every phase):

- **No persona "type" field anywhere.** No enum, no `kind: coding|assistant`, no
  code that branches on persona type. This mirrors the Phase-3 decision that skills
  are prompt-driven and `task_type` must never branch behavior — behavior emerges
  from injected text, not code branches.
- **The Router stays per-request, not per-persona.** It still classifies each
  individual request (chat / code / ambiguous) and selects skills. A persona that
  has "learned to code" and one that hasn't run the *identical* pipeline; the
  difference lives entirely in `MEMORY.md` / `SOUL.md` / `AGENTS.md`.
- **Memory is the evolution mechanism.** A persona becomes "more for coding" by
  accumulating coding decisions/patterns in `MEMORY.md` and procedures in
  `AGENTS.md`. Memory is therefore *the substrate of persona evolution*, not a
  late-stage nicety — which is why it is an early phase (Phase B).
- **Capabilities are opt-in by description, never gated by type.** Every persona
  *can* use crons, sub-agents, coding tools; whether it does is a function of its
  files, not a category permission.

One-line version: **a persona is a mutable workspace, not a class.** The engine
treats all personas identically; identity and specialization are emergent
properties of files the persona can itself rewrite.

### D4 — Persona config: files + optional overrides

A persona is its workspace `.md` files **plus** an optional config (e.g.
`persona.toml`) carrying a preferred model and extra skill dirs. **Unset fields
fall back to engine/global defaults.** The existing `--model` flag and
`~/.config/harness/skills/` override dir become the *fallback layer* beneath a
persona's own config. The optional config travels *with* the workspace folder, so
"copy the folder, get the agent" portability is preserved.

### D5 — Two deliberate divergences from OpenClaw

1. **No channels.** We have no chat inbox, so OpenClaw's channel→agent "bindings"
   collapse to **explicit persona selection** (`--persona` flag / `/persona` TUI
   picker). Channel routing is out of scope.
2. **Crons live in a *client*, not the engine.** OpenClaw runs heartbeats inside
   its Gateway; we keep the engine free of a scheduler and build crons as a
   **separate ACP consumer** of the engine — honoring `AGENTS.md` #6
   (clients consume the engine; the engine does not grow a scheduler inside it).

---

## 4. How the pieces map onto DoneDone's existing seams

The point of this section: **almost nothing here needs a new architectural seam.**
The persona model lands on machinery that already exists.

| OpenClaw concept | DoneDone seam it lands on |
|---|---|
| Workspace files injected on first turn | The existing skill-block injection (`acp_agent.py` `skills.compose` ~L156 → `TracingAgent(skill_block=…)` ~L265, injected after Jinja render). Persona context becomes a **second injected block alongside** the skill block — same seam, parallel source. |
| `agentDir` / per-agent session store | `SessionStore` (`acp_session.py`) + `paths.py` gain a persona/workspace dimension. |
| Channel→agent bindings | New, *collapsed*: a persona selector (`--persona` / `/persona`). No channel layer. |
| `MEMORY.md` + `memory/YYYY-MM-DD.md` | New memory module: read on session start, inject alongside persona files; a write tool/skill appends per `AGENTS.md` rules. |
| `HEARTBEAT.md` + cron tool | New scheduler **client** (separate ACP consumer of the engine). |
| `BOOTSTRAP.md` + attestation | New onboarding path keyed off `SessionStore`'s "new session" signal. |

**Persona injection and skill injection are two parallel context sources, not a
replacement.** A DoneDone persona is a hybrid: persona files (SOUL/IDENTITY/USER)
**+** the existing router/skills machinery. The engine stays capability-neutral;
the files decide how much of each a persona leans on.

**First-turn-only injection.** Like OpenClaw, persona + memory files inject on the
**first turn of a session only**, not every turn — token-lean and matches the
existing one-shot system-prompt assembly. Blank-skip and trim-truncate rules port
directly.

---

## 5. The phase roadmap

Six phases. Dependency-ordered; each independently shippable. Because personas are
untyped and **memory is the evolution mechanism** (D3), memory is early.

```
A ──▶ B ──▶ C ──▶ ┬──▶ D
                  └──▶ E
F (deferred, later)
```

### Phase A — Persona / workspace contract (foundation)

Define what a persona *is* on disk, and inject it.

- A workspace dir holds `SOUL.md`, `IDENTITY.md`, `AGENTS.md`, `USER.md`,
  `TOOLS.md` (+ optional `persona.toml` per D4).
- On the **first turn of a session only**: read them, skip blanks, trim/truncate
  large ones, inject as a **second context block alongside the existing skill
  block**. No new injection seam (§4).
- Ship a single built-in **"default" persona** so current users see no change.
- *No memory, no routing, no crons yet* — just "the files are the agent, and the
  engine reads them."

**Out of scope:** multiple personas, persona switching, memory writes.

### Phase B — Persistent memory (the evolution substrate)

`MEMORY.md` (durable) + `memory/YYYY-MM-DD.md` (daily).

- On session start: read today + yesterday + `MEMORY.md`; inject alongside persona
  files.
- Give the agent a **memory-write tool/skill** that appends concrete updates,
  read-before-write, **no empty placeholders** (OpenClaw's exact protocol).
- This is what lets a persona *drift* toward coding or assistant work over time
  (D3).

**Depends on A** (writes into the workspace A defined). **Out of scope:** automatic
summarization/compaction of memory (future).

### Phase C — Persona selection & isolation (the "multi")

Multiple workspaces + the ability to pick one.

- Workspaces under `~/.config/harness/agents/<id>/`.
- CLI `--persona <id>`; TUI `/persona` picker; default persona when unset.
- `SessionStore` gains a persona/workspace dimension so **sessions and memory are
  isolated per persona**.
- Bindings collapse to explicit selection (D5).

**Depends on A + B** (needs something to isolate). **Out of scope:** channels,
auto-routing by content.

### Phase D — First-run onboarding (`BOOTSTRAP.md` + attestation)

Make creating a *new* persona a real flow, not manual file-copying.

- Scaffold default templates for a new persona.
- Run a one-time `BOOTSTRAP.md` interview ritual; **self-delete** it after.
- Write a **state-dir attestation marker**; refuse to silently re-seed a
  wiped-but-attested workspace (OpenClaw's safety rule).

**Depends on C.**

### Phase E — Crons / HEARTBEAT (proactive runs)

`HEARTBEAT.md` (cron-in-English) + a scheduler that fires prompts at the engine on
a schedule.

- Built as a **separate ACP consumer of the engine**, *not* engine-internal (D5,
  `AGENTS.md` #6).
- Per-persona schedules. This is where a persona acts *without* the user present.

**Depends on C** (per-persona schedules). **Out of scope:** distributed/remote
scheduling.

### Phase F — Sub-agents (deferred)

OpenClaw's "spawning background agent runs." Lowest priority for a persona fleet,
and overlaps the GitHub-PR-worker work already on the broader roadmap. **Flagged as
future; not specced here.**

---

## 6. Open questions for later phases (not blocking this doc)

- **Memory growth control (Phase B+):** when does `MEMORY.md` get summarized /
  compacted so it doesn't blow the first-turn token budget? OpenClaw relies on
  trim-truncate at inject time; a smarter compaction step may be wanted.
- **Cross-persona memory (post-C):** should personas ever share memory, or is full
  isolation always right? Default position: isolated, per D2.
- **Persona switching mid-session (Phase C):** is switching only between sessions,
  or live within one? Default position: between sessions (simpler; matches
  first-turn-only injection).
- **`persona.toml` schema (Phase A/D4):** exact fields beyond `model` and
  `skill_dirs`; how it merges with `--model` precedence.
- **Sub-agent isolation model (Phase F):** subprocess vs in-process; overlap with
  the GitHub worker.

---

## 7. Relationship to the existing phase roadmap

The current engine is at **Phase 5 complete** (ACP agent + TUI + packaging), with
**Phase 6 (full distributability)** and **Phase 7 (GitHub PR worker)** queued. The
persona fleet is a **parallel track** that builds on the same seams:

- Phase A's injection rides the **skills layer (Phase 3)** seam.
- Persona/memory isolation extends the **`SessionStore` (Phase 4)**.
- Crons (Phase E) are **another ACP consumer**, exactly like the TUI (Phase 5) and
  the future GitHub worker (Phase 7) — reinforcing "engine is the product."

Phase F (sub-agents) and Phase 7 (GitHub worker) should be designed together when
the time comes; both are background-agent orchestration.

---

## 8. Summary

- **Persona = a mutable workspace of plain-text files, not a class.** Untyped;
  evolves via memory.
- **One engine, persona = selected workspace.** No gateway, no subprocess-per-agent
  yet.
- **Persona context is a second injected block beside skills**, first-turn-only —
  no new seam.
- **Six phases, memory early** (A persona-contract → B memory → C selection/isolation
  → D onboarding → E crons → F sub-agents deferred).
- **Two divergences from OpenClaw:** no channels (explicit selection), crons in a
  client (engine stays scheduler-free).
- Each phase is its own brainstorm→spec→build cycle.
