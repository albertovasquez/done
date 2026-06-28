# Memory

Each persona has a **persistent memory** in its workspace. Memory is plain
Markdown files the agent reads and writes — there is no hidden state, no database,
no embeddings, and no external service. It is **per-persona** (hard-isolated by the
workspace directory) and a strict **no-op until used** — an empty workspace adds
nothing to the agent's context and registers no extra tools.

Memory has two halves that mirror the [skills system](router-flows.md): an
**index** the agent sees at the start of every turn, and a **load-on-demand tool**
(`load_memory`) for pulling a fact's full text when it needs it.

## Where memory lives

In the persona's workspace (default `~/.config/harness/agents/<id>/`):

```
MEMORY.md                 the durable index — injected at the start of every turn
memory/<YYYY-MM-DD>.md     daily notes — today's and yesterday's are auto-injected
memory/<slug>.md           typed facts — listed in the index, pulled on demand
```

- **`MEMORY.md`** is the curated, always-loaded layer. Keep it small and
  high-signal — durable preferences, decisions, constraints, open loops. It is
  trimmed at 8 000 characters when injected, so if it grows past that, move detail
  into per-fact files and leave a one-line pointer in the index.
- **`memory/<date>.md`** are the working layer (a day's notes). Today's and
  yesterday's are injected automatically; older days are still reachable with
  `load_memory`.
- **`memory/<slug>.md`** are typed facts (see below). They are **not** injected in
  full — Done auto-generates a one-line menu of them (name · type · description)
  and appends it to the startup block, so the agent knows they exist and pulls a
  body with `load_memory` when relevant.

## The two recall modes

| Mode | What | When |
|---|---|---|
| **Startup inject** | `MEMORY.md` + today's + yesterday's notes + an auto-generated menu of typed facts go into context at the start of every turn (content-gated, trimmed). | Always — the agent reads it without doing anything. |
| **Load on demand** | `load_memory(memory_name)` returns one fact's full text as a tool observation. | When the index references a fact the agent didn't get in full. |

This is the same progressive-disclosure shape as skills (a cheap menu + a
`load_skill` tool): the index stays small no matter how much the persona
remembers, and the agent fetches only what a given turn needs.

## Typed facts

A per-fact file carries YAML frontmatter so facts are categorized:

```markdown
---
name: user-terse
description: No trailing summaries after code changes
type: feedback
---

The user prefers terse responses. Skip the "here's what I did" recap after
edits; state the result in one line. Applies to all code-change turns.
```

- **`name`** — a slug; must match the filename stem (`user-terse` ↔ `user-terse.md`).
- **`description`** — one line; this is what shows in the index and decides relevance.
- **`type`** — one of:
  - `user` — who the user is (role, expertise, standing preferences)
  - `feedback` — how the agent should work (corrections, confirmed approaches)
  - `project` — ongoing work, goals, constraints not derivable from the code
  - `reference` — pointers to external resources (URLs, dashboards, tickets)

  `type` is optional and defaults to `reference`. An unknown value is kept as-is
  (forward-compatible) — a single odd file never breaks recall.

Plain-prose memory still works: a `MEMORY.md` with no frontmatter and no per-fact
files behaves exactly as it always has (injected at startup). The typed-manifest
convention is **additive** — adopt it only when the always-injected block starts
getting large.

## The auto-generated menu

You don't have to maintain a manifest by hand: Done builds the menu from the
typed facts under `memory/` automatically and appends it to the startup block:

```
## Available memory (load by name with `load_memory`)
- `user-terse` (feedback) — no trailing summaries
- `pr-workflow` (project) — ship via PR, never main
```

The agent pulls any entry with `load_memory("pr-workflow")`. Keep `MEMORY.md` for
the always-loaded durable facts; move anything bulky into a typed fact file and
let the menu surface it — that keeps the always-injected block cheap no matter how
much the persona remembers.

## Writing memory

The agent writes its own memory via plain shell — when a workspace has memory
content, its context carries a short write-protocol preamble teaching it to
`mkdir`/`cat`/`append`. You can also just tell it: "remember that I prefer X," and
it will write the right file. You can edit any memory file by hand at any time.

## Isolation & safety

- **Per-persona:** `load_memory` resolves names **strictly inside the active
  persona's workspace**. Names containing `/`, `\`, `..`, or absolute paths are
  rejected — one persona can never read another's memory, and a fact can never
  escape the workspace.
- **No external calls:** everything is local files. Nothing is uploaded, indexed
  remotely, or sent to a third party.

## What this deliberately does *not* do (yet)

Memory has **no search index**, no semantic/vector recall, and no auto-capture —
by design. Done evaluated adopting a hybrid-search backend
([QMD](https://github.com/tobi/qmd), as OpenClaw does) and decided against it for
now: it is a Node sidecar with a ~2 GB first-run model download, which violates
Done's Python-only, no-new-dependency, no-op-when-unused values to deliver semantic
recall there's no evidence Done needs yet. Because the Markdown files remain the
source of truth, a keyword index (pure-Python `sqlite3` FTS5) or QMD can be added
later as a purely **additive** layer — nothing here has to change.

See `docs/superpowers/specs/2026-06-28-memory-recall-design.md` for the full
design and the research spike behind this decision.
