# Sub-agents (Hermes-model parallel workers) — design

**Status:** design approved 2026-06-29; pending implementation plan.
**Author:** brainstormed with Alberto.
**Scope:** one implementation plan. CLI/cron headless surface only; TUI/ACP deferred to v2.

---

## 1. Concept and boundary

A **worker** is an ephemeral, low-context agent that a parent agent spawns to do **one
focused task** and return a **structured summary**. It runs on its own thread, with a
fresh minimal conversation and a cheaper model.

A worker is **NOT** a persona:

- never gets a fleet seat (`persona_sessions`),
- never appears in the persona rail,
- never shows in any session list,
- never persists a session.

It is a thread with a fresh conversation, not an identity.

This is deliberately the **Hermes `delegate_task` model**, which is distinct from
OpenClaw's "subagent" (a named, persistent, user-facing identity — what *this* codebase
already calls a **persona**). We are not building roster delegation; we already have that.

**The win:** fan out cheap, focused, parallel single-item tasks (investigation/research by
default) on a cheaper model with tiny context. Only summaries return, so the parent's
context stays clean.

**Explicit non-goals (YAGNI — keep complexity down):**

- No roles / orchestrator / `max_spawn_depth`. Workers are **flat (depth 1)** and cannot
  spawn workers. (This is the biggest complexity sink in Hermes's design and is not needed
  for single-item fan-out.)
- No aggregate cost/time accounting across a batch (documented future tightening; see §8).
- No TUI nested-trace rendering (v2; that is where the async-boundary risk lives).
- No hard tool sandbox. The toolset shapes intent, not a security boundary (see §5).

---

## 2. Architecture — one agent-construction chokepoint, reused

The core move is a **refactor first**: the agent-construction body that today lives inline
in `harness/jobs/executor.py` (the block that resolves workspace → `compose_context` →
`render_base_prompt` → build `StreamingLitellmModel` with a registry → build
`LocalEnvironment` → `MiniSweAgentRunner`) is extracted into a single reusable builder so
**cron, CLI, and workers all construct agents one way**.

```
harness/agent_build.py   (NEW)

  build_persona_agent(
      agent_id: str,
      *,
      model_override: str | None = None,   # cheaper worker model (see §6)
      toolset: set[str] | None = None,     # restricted registry (see §5); None = full
      context_mode: str = "full",          # "full" | "worker"  (see §4)
      wall_time_limit: int | None = None,  # cron budget cap (see §8)
      step_limit: int | None = None,       # turn cap (see §7)
  ) -> tuple[MiniSweAgentRunner, TurnContext]
```

- `jobs/executor.py` is refactored to call `build_persona_agent(agent_id, context_mode="full")`;
  its inline construction block collapses to a few lines. This refactor is the **bulk of the
  work** and is guarded by the existing cron/executor tests (parity is the acceptance bar).
- `context_mode="worker"` produces the trimmed prompt described in §4.
- The worker tool (§3) sits thin on top of this builder.

**Why a shared builder rather than a worker-only path:** the cron executor already proves a
persona agent runs cleanly headless on threads. A worker is that same recipe with three knobs
turned (trimmed context, restricted tools, cheaper model). Sharing the chokepoint means the
worker can never silently drift from how a real persona turn is constructed.

---

## 3. The tool surface — `harness/tools/subagent.py`

Mirrors `CreateJobTool` exactly in shape: a friendly model-facing schema, a normalizer, and
`agent_id` resolved from the **environment**, never from the model.

```
subagent(tasks=[
  { goal: str,              # required — the focused task
    context: str,           # required — explicit facts the worker needs
    tools?: [str],          # optional — opt up from the default toolset (§5)
    model?: str,            # optional — per-task model override (§6)
    max_iterations?: int }, # optional — per-task turn cap override (§7)
  ...
])
```

Tool execution (`execute(args, env) -> dict`):

1. `agent_id = env._active_persona` (inherited; same stamp `CreateJobTool` relies on).
   Never read `agent_id` from the model.
2. Read optional propagation from `env`: `cancel_flag` (parent interrupt) and
   `_remaining_secs` (cron budget; `None` on the interactive path).
3. Run `tasks` on a `ThreadPoolExecutor(max_workers=cap)` (§7). The parent's tool call
   **blocks** until all workers finish — exactly like a slow `bash` call. No scheduler.
4. Return a **digest** observation (§7): `{"output": digest, "returncode": 0,
   "exception_info": None}`.

Registry placement:

- `SubagentTool` is added to `build_registry` as a context-gated tool (present on a normal
  persona agent).
- It is **excluded from a worker's own registry** — that is the entire depth-1 enforcement.
  A worker physically cannot call `subagent`.

---

## 4. Worker context (the "trimmed" definition)

A worker runs **as the persona** (`env._active_persona` = parent's agent_id, so memory
writes land in the correct workspace) but with a **fresh, minimal conversation**.

`context_mode="worker"` assembles the prompt from exactly these pieces:

| Piece                                   | Worker gets                                            |
|-----------------------------------------|--------------------------------------------------------|
| `base_block` (policy + cwd/model/OS)    | **Include** — small and necessary                      |
| `agents_block` (3-tier AGENTS.md)       | **Include for ALL workers** — operating standards apply even to read-only workers running `git`/`gh` |
| persona soul (`persona_block`)          | **Drop** — the persona voice is applied by the *parent* when it synthesizes returned summaries, not inside the worker |
| memory block (auto-injected)            | **Drop** — but keep the `load_memory` tool if the workspace has memory, so a worker can pull a fact on demand without prepaying the whole block |
| skills menu (auto-injected)             | **Drop** — keep `load_skill` only if the task's `tools` grants it |
| `goal` + `context` (from parent)        | **Include** — this is the worker's instance prompt     |

### Structured-summary return contract (instance template)

Borrowed from Hermes: the worker's instance template instructs it to finish by producing a
**structured summary** with four fields:

1. **what it did**,
2. **what it found**,
3. **any files modified**,
4. **any issues encountered**.

This is a *prompt-level contract*, not new code. It makes the digest predictable and the
parent's synthesis reliable. The worker's submission (its `Submitted` payload, which
`runner.run` already returns) is this structured summary.

---

## 5. Toolset (default and opt-up)

- **Default toolset: `{read, bash}`** — a read-only investigator. This is the highest-value,
  lowest-risk fan-out pattern (research / survey / analyze).
- The parent opts a task **up** to `{write, edit}` (and any other tool) per task via `tools`.
- Implemented as a one-line filter at the end of `build_registry(..., toolset)`:
  `tools = [t for t in tools if t.name in toolset]` when `toolset is not None`.
- `subagent` is always excluded from a worker's registry regardless of `tools` (§3, depth-1).

**Honest caveat (recorded, not hidden):** `bash` is in the default set, and `bash` can write
files (`echo > file`, `rm`). So **"read-only" is an *intent* default, not a hard sandbox** —
consistent with `create_job`'s grant model, which is "recorded-not-enforced in v1." Hard
enforcement (a no-write bash) is **out of scope** for this design and is not implied by the
default toolset.

---

## 6. Model resolution

Resolution order for a worker's model:

1. `tasks[].model` — per-task override (parent can escalate one hard task).
2. `[agents.<id>].subagent_model` in `done.conf` — per-persona.
3. global `subagent_model` in `done.conf`.
4. **parent's own model** — final fallback.

The global `subagent_model` is **unset by default**, so with no configuration the feature
ships as a **behavioral no-op**: workers run on the parent's model (functional, just not
cheaper) until a persona or the global opts into a cheaper model. This preserves the
byte-identical-no-op discipline used throughout the persona work.

Model id is single-homed in `done.conf` (consistent with existing per-persona model
resolution via `resolve_session_model`). `subagent_model` is a non-clobbering addition to
the `[agents.<id>]` table and a global default key.

---

## 7. Parallelism, turn cap, failure, and return

### Concurrency

- **Cap = 4** by default, configurable via a `done.conf` key (`subagent_max_concurrent`).
- **Overflow is queued, not rejected.** If the model submits more tasks than the cap, the
  executor runs them as slots free up. (Hermes rejects over-cap; we queue because workers are
  cheap and the parent is already blocked.)

### Turn cap (primary cheap-model guardrail)

- Each worker gets a low **`step_limit`** (turn cap) — default **15** — via `AgentConfig`
  (the field already exists upstream).
- A task may override with `max_iterations` (mapped onto `step_limit`).
- Rationale: a cheap model stuck in a format-error or tool loop burns *turns*, not just
  seconds. The turn cap is cheaper and more robust than relying on wall-time alone.

### Per-worker failure isolation

- `MiniSweAgentRunner.run` / `TracingAgent.run` already capture `BaseException` into `_Done`.
- A failed worker (exception, `TimeExceeded`, `LimitsExceeded`, `RepeatedFormatError`)
  returns a **structured error entry** instead of a summary. It never aborts sibling workers.
- The batch always completes.

### Return shape (digest)

The tool returns a digest, one block per task, e.g.:

```
[subagent 1/3 ✓] goal: "Survey approach X"
<structured summary>

[subagent 2/3 ✓] goal: "Survey approach Y"
<structured summary>

[subagent 3/3 ✗] goal: "Survey approach Z"
failed: TimeExceeded (ran 30s of 30s budget)
```

- `returncode` is **0 even on partial failure** — the *tool* succeeded; each worker's status
  lives in the text. This lets the parent reason about partial results rather than the agent
  loop treating the whole call as a tool error.
- The parent then synthesizes the digest in-persona (this is where the persona voice is
  applied — see §4).

---

## 8. Cron budget interaction

The spicy case: a cron job runs a persona that spawns workers; the workers must stay inside
the job's `timeout_secs`.

- **Per-worker wall-time cap:** `wall_time_limit = min(worker_default, env._remaining_secs)`.
  A single worker cannot individually outlive the job budget.
- **Job-level `timeout_secs` is the real backstop.** `jobs/executor.py` already kills the
  whole headless turn on timeout, so we do **not** need aggregate accounting to be *safe*.
- **Interactive (non-cron) path:** `env._remaining_secs = None` → workers use their own
  defaults. No special-casing — the budget cap only activates when a parent sets it.
- **Why the cron path is the *easy* surface, not the hard one:** cron already runs through
  `MiniSweAgentRunner` synchronously on threads, with no ACP loop and no async boundary. A
  cron-launched worker just spawns more threads inside the same headless process. The
  async-boundary risk (the `#81/#91/#99/#138` family) lives only on the interactive TUI path,
  which is deferred to v2.

**Deferred (documented future tightening):** aggregate cost/time accounting across a batch.
v1 relies on per-worker turn cap + per-worker wall-time cap + job-level timeout. The honest
limitation: per-worker caps bound each worker individually, not the *sum*; but because workers
run in parallel and the parent blocks, the job-level `timeout_secs` still catches a true
overrun.

---

## 9. Guardrail summary

| Guardrail | Mechanism                              | Default                          | Per-task override |
|-----------|----------------------------------------|----------------------------------|-------------------|
| Turn cap  | `step_limit` (existing `AgentConfig`)  | 15                               | `max_iterations`  |
| Wall-time | `wall_time_limit` (existing)           | `min(default, parent_remaining)` | —                 |
| Tools     | registry `toolset` filter              | `{read, bash}`                   | `tools`           |
| Model     | resolution chain (§6)                  | parent's (until opted cheaper)   | `model`           |
| Depth     | `subagent` excluded from worker registry | flat (1) — no nested spawn      | none              |
| Concurrency | `ThreadPoolExecutor(max_workers=cap)` | 4 (queue overflow)              | —                 |
| Interrupt | parent `cancel_flag` passed to workers | propagated                       | —                 |

---

## 10. Files touched

**New:**

- `harness/agent_build.py` — `build_persona_agent` chokepoint.
- `harness/tools/subagent.py` — `SubagentTool` + the parallel runner + digest formatter.

**Modified:**

- `harness/jobs/executor.py` — refactor inline construction to call `build_persona_agent`
  (parity guarded by existing tests).
- `harness/tools/registry.py` — add `toolset` param; register `SubagentTool`; exclude it from
  worker registries.
- `harness/config.py` / `done.conf` schema — add `subagent_model` (global + per-persona) and
  `subagent_max_concurrent`.
- worker instance template — the structured-summary contract (§4). (Located alongside the
  existing instance templates the agent config uses.)

**Deferred (NOT in this plan):** all TUI/ACP work — nested worker traces, live progress,
the async event boundary.

---

## 11. Testing strategy

All tests use the **mock model** (no live LLM), consistent with the existing suite.

- **`build_persona_agent` parity:** the refactored cron path constructs an agent identical to
  the pre-refactor inline block (the existing executor tests are the acceptance bar; add a
  direct unit test for the builder).
- **Registry `toolset` filter:** `{read, bash}` default excludes `write`/`edit`/`create_job`;
  `subagent` is always excluded from a worker registry; opt-up via `tools` adds the named
  tools.
- **Model resolution order:** per-task → per-persona conf → global conf → parent; global unset
  → parent (no-op).
- **Turn cap + wall-time cap:** worker gets `step_limit`/`max_iterations`; cron path applies
  `min(default, remaining)`; interactive path leaves them at defaults.
- **Failure isolation:** one worker raising does not abort siblings; its entry is a structured
  error; `returncode` stays 0.
- **Digest formatting:** N tasks → N blocks with correct ✓/✗ and summaries/errors.
- **No-op discipline:** with no `subagent_model` configured and the tool merely present,
  existing persona/cron behavior is unchanged.

---

## 12. Open implementation questions (for the plan, not blockers)

- Exact home of the worker instance template. The intent is settled (§4: the structured-
  summary contract) and the mechanism is known (reuse the `instance_template` override the
  agent config already supports, e.g. the `code_explain`-style template seam); the plan just
  needs to pin the file/key it lives under.
- Whether `load_memory`/`load_skill` gating for a worker reuses the existing
  `build_registry` gating verbatim (expected: yes) or needs a worker-specific tweak.
- Precise digest delimiter/format string (cosmetic; pick one and make it explicit in the
  plan).
