# Gap analysis — the Claude Code system prompt vs. DoneDone today

**Status:** research / roadmap document (no implementation). Hand-off for a
refinement team.
**Date:** 2026-06-27
**Author:** Alberto Vasquez (with Claude Opus 4.8)
**Scope:** treat the **Claude Code agentic-CLI system prompt** as the *target
architecture* for `dn`, and map every section onto what DoneDone actually has.
Produce two things: (1) the slice we can **mine now** into a `dn`-native base
prompt, and (2) a **roadmap** of the harness capabilities the prompt assumes but
`dn` lacks — so the gaps become a feature backlog, not a surprise.
**Primary reference:** the Claude Code system prompt (agentic CLI variant), as
captured 2026-05-28. Distinct from the Claude.ai *consumer-chat* prompt — that
one describes a product `dn` isn't and is out of scope here.
**Decisions baked in (from brainstorming):**
- Adopt the base behavioral prompt on **both** the coding path and the chat path
  (the chat path has **no** base prompt today).
- Treat the prompt **as the target architecture**, not as text to paste: mine
  the portable policy, road-map the rest.

---

## 0. TL;DR

1. The Claude Code prompt is roughly **25% portable behavioral policy** and
   **75% harness-specific machinery documentation** (tools, memory index,
   workflow orchestration, scheduling, browser automation). The 25% is real and
   `dn` is **missing it**; the 75% is a *capability roadmap*, because the prompt's
   text is only *true* once the matching machinery exists.
2. **The linchpin fact, verified in code:** `dn`'s agent is **function-calling
   already**, but with **exactly one tool — `bash`**
   (`upstream/src/minisweagent/models/utils/actions_toolcall.py:11`,
   passed as `tools=[BASH_TOOL]` in
   `upstream/src/minisweagent/models/litellm_model.py:69`; `dn` uses
   `StreamingLitellmModel(LitellmModel)`, `harness/streaming_model.py:30`). So
   the Tools section of the prompt is not blocked by "no tool surface" — it is
   blocked by "every capability is expressed as a bash command." Adding
   `Read`/`Edit`/`Write`/`Agent` is *adding tools to an existing loop*, which is a
   far cheaper proposition than a rewrite. **But** every new tool added to the
   loop is an edit to *which tools we register*, not to `upstream/` itself —
   AGENTS.md #4 (zero upstream edits) is respected by configuring/extending in
   `harness/`, never by patching the vendored engine.
3. **The single injection chokepoint already exists.** Persona + memory + skills
   are appended to the rendered system template at
   `harness/tracing_agent.py:48-56`. A `dn`-native base prompt block slots in
   there with one line. The chat path (`harness/chat_handler.py:81`) needs the
   same block prepended as its (currently absent) system message.
4. **Net:** ship the base prompt now (Part 1); use Part 2 as the backlog that
   tells us, section by section, *where `dn` is lacking* relative to a
   first-class agentic CLI.

---

## 1. What we mine NOW — the `dn`-native base prompt

These pieces are product-agnostic agent **policy**. They describe how a coding
agent should *behave*, independent of which specific tools the harness exposes.
`dn`'s base prompt today is the upstream one-liner — *"You are a helpful
assistant that can interact with a computer"* (`default.yaml:3`) — plus the
single-bash-block format contract. There is no discipline prose, no security
stance, no faithful-reporting rule. That is the gap Part 1 closes.

### 1.1 Security posture — lift nearly verbatim

> *Assist with authorized security testing, defensive security, CTF challenges,
> and educational contexts. Refuse … destructive techniques, DoS, mass
> targeting, supply-chain compromise, or detection evasion for malicious
> purposes. Dual-use tools … require clear authorization context …*

This **matches** `dn`'s stated posture (`CLAUDE.md` security note; `AGENTS.md`
intent) and **resolves** the conflict the consumer-chat prompt created (that one
refuses all malware "even for education", which would block the CTF/pentest work
`dn` is meant to support). Adopt this block essentially as-is.

### 1.2 Harness behavioral discipline — adopt the prose, drop tool-specifics

From the `# Harness` section and the paragraph after it, the portable rules:

- **Report outcomes faithfully** — "if tests fail, say so with the output; if a
  step was skipped, say that; when something is done and verified, state it
  plainly without hedging." Composes directly with the bundled
  `verification-before-completion` skill (`README.md:97`).
- **Confirm hard-to-reverse / outward-facing actions** before doing them; an
  approval in one context doesn't carry to the next. `dn` already has a
  permission model (`--yolo`, Allow/Reject, `README.md:33`); this is the *prose*
  that should govern when the agent *asks*.
- **Before deleting/overwriting, look at the target**; if it contradicts how it
  was described, surface that instead of proceeding.
- **Reference code as `file_path:line_number`**; **match the surrounding code's
  style/idiom/comment density.** (The latter already lives in `AGENTS.md` #5 — so
  the base prompt and the operating standard reinforce each other.)

Drop the tool-coupled lines ("prefer dedicated file/search tools over shell" —
`dn` has only `bash` today; "independent tool calls in parallel" — single tool,
so N/A until Part 2 lands more tools).

### 1.3 Environment block — already generated, just align the shape

The prompt's `# Environment` (cwd, git, platform, OS, model id, knowledge
cutoff) is **computed at runtime**, not authored. `dn` already injects
`<system_information>` via the upstream `instance_template` (`default.yaml:42`)
and knows its cwd (`--cwd`) and model. Action item is small: align format and add
the missing fields (model id string, cutoff) to the generated block — no new
machinery.

### 1.4 Where it plugs in (both paths)

```
coding path  (harness/tracing_agent.py:48)
    upstream system_template
      + [NEW dn base block]      ← §1.1–1.3
      + persona_block            (harness/persona.py)
      + memory_block             (harness/memory.py)
      + skill_block              (harness/skills.py)

chat path    (harness/chat_handler.py:81)
    [NEW dn base block]          ← §1.1–1.3  (today: nothing)
      + persona_block
      + history + user turn
```

One block, two insertion points. The coding-path insertion is one line at the
existing chokepoint (`_render_template`, identity-matched to the system template
so it never leaks into the instance message — same guard already used for
persona/skills). The chat-path insertion prepends it to the `messages` list
ahead of `persona_block`.

**Open question for the refinement team:** author the base block as a new bundled
file (e.g. `harness/prompts/base.md`) read once at startup, *or* inline it in a
`harness/` constant? A file mirrors the persona/skills "content layer" pattern
and lets users override it; a constant is simpler and can't be accidentally
emptied. Lean **file**, for consistency with `persona.py`/`memory.py`/`skills.py`
and user-overridability — but flag the no-op risk (an empty override file must be
content-gated like personas are, `persona.py:24`).

---

## 2. The roadmap — what the prompt assumes that `dn` LACKS

Each subsection is one capability the Claude Code prompt documents. For each:
**what the prompt promises**, **what `dn` has today** (code-grounded), **the
gap**, and a **rough cost/priority**. This is the backlog that answers "where are
we lacking?"

### 2.1 A multi-tool surface (Read / Edit / Write / Bash-as-one-of-many)

- **Prompt promises:** distinct `Read`, `Edit`, `Write`, `Bash` tools, "prefer
  dedicated file/search tools over shell," and **parallel tool calls in one
  response.**
- **`dn` today:** function-calling loop with **one** tool, `bash`
  (`actions_toolcall.py:11`). All file reading/editing happens *through* bash
  (`cat`, `sed -i ''` — the upstream prompt even hard-codes the macOS `sed`
  caveat, `default.yaml:74`).
- **Gap:** no structured file tools; no parallelism (one tool ⇒ one call shape).
  Without `Read`/`Edit`, the model can't do precise line-anchored edits — it
  shells out, which is lossier and harder to trace.
- **Cost / priority:** **Medium, high-value.** This is *adding tools to an
  existing loop*, registered in `harness/` (NOT patching `upstream/`). The
  upstream `step()`/tool-dispatch (`default.py:124`) assumes the single bash
  tool, so the seam is "how `dn` registers tools and dispatches their results" —
  design that seam first. **This is the highest-leverage roadmap item:** most of
  the rest of the prompt's Tools section presupposes it.

### 2.2 Subagents (`Agent`) and multi-agent orchestration (`Workflow`)

- **Prompt promises:** spawn typed subagents (`Explore`, `Plan`,
  `general-purpose`), run them in parallel/background, and a full `Workflow`
  DSL (pipeline/parallel/phase, adversarial-verify, judge panels).
- **`dn` today:** none. A single agent loop per turn. (Personas hint at a
  *future* fleet — see `docs/personas.md` and the persona roadmap — but there is
  no subagent-dispatch or orchestration primitive in the engine.)
- **Gap:** large. No agent-spawns-agent, no parallel fan-out, no structured-output
  contract between agents.
- **Cost / priority:** **High effort, defer.** Depends on 2.1 (tools) and on the
  persona-fleet track maturing. Genuinely the "target architecture" end-state,
  not a near-term fill. Worth a dedicated spec when the fleet work reaches it.

### 2.3 Deferred tools + `ToolSearch`

- **Prompt promises:** a large tool catalog where most tools are *deferred*
  (name-only) and loaded on demand via `ToolSearch` to keep context small.
- **`dn` today:** no tool catalog at all (one tool), so nothing to defer.
- **Gap:** only meaningful *after* 2.1/2.2 create enough tools that loading all
  schemas would bloat context.
- **Cost / priority:** **Low now, conditional.** A scaling optimization, not a
  capability. Park until the tool count justifies it.

### 2.4 Memory — recall index vs. write-protocol log

- **Prompt promises:** a `/memory/` store of **one-fact-per-file** entries with
  YAML frontmatter (`name`/`description`/`type`) and a loaded `MEMORY.md`
  **index** for relevance-based recall; explicit rules for `user`/`feedback`/
  `project`/`reference` fact types.
- **`dn` today:** a **different** memory model — content-gated injection of
  `MEMORY.md` + today's/yesterday's daily notes, with a shell **write-protocol**
  the agent follows to append entries (`harness/memory.py:31-46`,
  `resolve_memory`). It is per-workspace, append-log shaped, not a
  slug-per-fact recall index.
- **Gap:** not "missing memory" — *different memory*. The prompt assumes
  retrieval/relevance over many small fact files; `dn` assumes a rolling
  append-log read in full. Adopting the prompt's model means a new subsystem
  (frontmatter parsing, an index distinct from content, recall selection),
  **plus** reconciling with the content-gating no-op that keeps unused personas
  byte-identical (`memory.py:90`).
- **Cost / priority:** **Medium, evaluate need first.** `dn`'s log model may be
  *sufficient* for a coding agent; the recall-index model earns its complexity
  mainly at consumer-assistant scale. **Decision needed:** do we want recall, or
  is the rolling log enough? Don't build the index on spec.

### 2.5 Scheduling & background work (`ScheduleWakeup`, `/schedule`, `/loop`, Cron)

- **Prompt promises:** self-paced loops, cron-scheduled remote agents, wake-ups,
  background tasks, push notifications.
- **`dn` today:** none. Synchronous turn loop; `Esc` cancels at a command
  boundary (`README.md:148`). No scheduler, no background runtime.
- **Gap:** total. Requires a persistent runtime `dn` doesn't have.
- **Cost / priority:** **High effort, low near-term value** for a terminal coding
  agent. Defer hard.

### 2.6 Context management / compaction

- **Prompt promises:** automatic summarization of long conversations across
  context windows, continuity after compaction.
- **`dn` today:** prior-history injection between system and instance messages
  (`tracing_agent.py:77`), but **no** summarization/compaction loop.
- **Gap:** `dn` will hit context limits on long turns with no graceful
  degradation.
- **Cost / priority:** **Medium, real.** Becomes urgent as real-model sessions
  lengthen. A bounded "summarize-oldest-when-over-budget" pass is a tractable
  first cut; full Claude-Code-style compaction is bigger.

### 2.7 `AskUserQuestion` (structured clarification) & richer permission UX

- **Prompt promises:** a structured multiple-choice clarification tool with
  previews; a rich permission-mode model.
- **`dn` today:** the **router** detects ambiguity and emits a single
  `clarifying_question` string (`harness/router.py:129-133`) — good bones, but
  free-text, not structured options. Permissions are binary Allow/Reject.
- **Gap:** clarification is text-only (no option chips/previews); permissions
  lack the graduated modes (`acceptEdits`, `plan`, etc.).
- **Cost / priority:** **Low–medium, nice-to-have.** The router's
  `needs_clarification` path is the natural home; this is an enrichment, not new
  infrastructure. Good early TUI win.

### 2.8 Browser automation (`claude-in-chrome`)

- **Prompt promises:** a full Chrome MCP tool suite (navigate, screenshot, DOM
  read, console, network, GIF capture).
- **`dn` today:** none, and **out of charter** — `dn` is a code/terminal agent.
- **Gap:** total, intentionally.
- **Cost / priority:** **Out of scope.** Record as "explicitly not pursued" so a
  future reader doesn't mistake the gap for an oversight.

### 2.9 `SendUserFile`, `Skill` mechanics, session/`!`-prefix guidance

- **Prompt promises:** push files to the user as deliverables; a `Skill`
  invocation tool; `!`-prefix shell hand-off; `/code-review ultra` etc.
- **`dn` today:** skills exist but are **router-injected into context**
  (`harness/skills.py`, `router.py`), not invoked as a *tool* by the model.
  No file-push, no `!`-prefix, no slash-command suite.
- **Gap:** mostly Claude-Code-CLI-specific affordances; the skills *mechanism*
  differs by design (`dn` selects skills *for* the agent; Claude Code lets the
  model *call* them).
- **Cost / priority:** **Low / mostly out of scope.** `SendUserFile` could be a
  small TUI win (surface generated artifacts); the rest is CLI-host-specific.

---

## 3. Roadmap at a glance

| # | Capability | `dn` today | Gap size | Priority |
|---|---|---|---|---|
| 1.1 | Security posture | none in prompt | small | **mine now** |
| 1.2 | Harness discipline prose | upstream one-liner | small | **mine now** |
| 1.3 | Environment block | partial (generated) | small | **mine now** |
| 2.1 | Multi-tool surface (Read/Edit/Write) | one tool: `bash` | medium | **high — do first** |
| 2.2 | Subagents / Workflow | none | large | high effort, defer |
| 2.3 | Deferred tools / ToolSearch | none (1 tool) | n/a yet | conditional |
| 2.4 | Memory recall index | append-log model | medium | evaluate need |
| 2.5 | Scheduling / background | none | large | defer |
| 2.6 | Context compaction | history-inject only | medium | real, growing |
| 2.7 | Structured clarify / perms | text clarify, binary perms | small | nice early win |
| 2.8 | Browser automation | none | total | out of scope |
| 2.9 | File-push / Skill-as-tool | router-injects skills | small | mostly out of scope |

---

## 4. Recommended sequence

1. **Now:** land the `dn`-native base prompt (Part 1) on both paths via the
   existing chokepoint. Smallest change, immediate behavioral lift, no new
   machinery. Spec → PR.
2. **Next:** design the **multi-tool seam** (2.1) — how `dn` registers tools
   beyond `bash` and dispatches results, *in `harness/`, without touching
   `upstream/`*. Everything else in the Tools half of the prompt presupposes it.
3. **Then, demand-driven:** context compaction (2.6) when sessions lengthen;
   structured clarification (2.7) as a TUI win; revisit the memory model (2.4)
   only after deciding recall-vs-log.
4. **Defer / out of scope:** subagents+workflow (2.2), scheduling (2.5),
   browser (2.8) — these are the genuine "target architecture" horizon, tracked
   but not near-term.

---

## 5. Provenance of every code claim

All file:line references verified against the worktree at authoring time
(2026-06-27): `actions_toolcall.py:11` (sole `bash` tool),
`litellm_model.py:69` (`tools=[BASH_TOOL]`), `streaming_model.py:30`
(`dn` uses `LitellmModel`), `tracing_agent.py:48-56` (injection chokepoint) and
`:77` (prior-history inject), `chat_handler.py:81` (chat path, persona-only, no
base prompt), `persona.py:24` (content-gating no-op), `memory.py:31-46,90`
(write-protocol + content-gate), `router.py:129-133` (text clarification),
`default.yaml:3,42,74` (upstream base prompt + system_information + sed caveat).
A refinement team should re-verify before acting — docs can lag a phase behind
(`AGENTS.md` #6).
```