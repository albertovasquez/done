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

**Extraction is the risk-concentrated task (verified against live code).** The construction is
NOT a clean copy-paste: in `jobs/executor.py` it lives inside the `_default_deps()` factory
with ~15 lazy imports and nested `compose`/`run_turn`/`_load_agent_cfg` closures, and
`resolve_model` reads `os.environ` (executor.py:153). The refactor must untangle these
closures into explicit `build_persona_agent` parameters. Treat it as the **highest-risk task
in the plan**, with the existing executor tests as the parity gate (the cron path must produce
a byte-identical agent before and after).

---

## 2a. Concurrency safety (the load-bearing assumption)

The feature rests on running **N `TracingAgent` instances concurrently on threads**. Verified
against live code (harness AND upstream):

**Safe — per-instance, no harness-level shared mutable state:**

- `StreamingLitellmModel` holds `self.registry` / `self.on_delta` per construction
  (streaming_model.py:43-45, *"Fresh registry per construction — never a shared
  module-global"*). `litellm` is used only as call-site functions
  (`litellm.completion`, `litellm.stream_chunk_builder`) — there is **no `litellm.<global> = …`
  assignment** that would race.
- `TracingAgent` has no module-globals / ClassVars — only per-instance `self.messages`,
  `self.cost`, `self.n_calls`, `self._cancel_flag`.
- `LocalEnvironment.execute` spawns a fresh `subprocess.Popen` per call — stateless.

**The one shared singleton — `GLOBAL_MODEL_STATS` (upstream, process-global):**

Upstream defines a **process-global** model-stats accumulator
(`upstream/src/minisweagent/models/__init__.py:42`), hit on **every** litellm query
(`litellm_model.py:86`). It is **mutex-locked** (no corruption / no crash under concurrency),
but it is **process-global, not per-agent**, so **cost and call-count accounting cross-talks
across all concurrent workers and the parent.**

Consequences the plan must honor:

- **Do NOT rely on `cost_limit` as a worker guardrail.** With N workers + parent accumulating
  into one counter, a global cost limit trips early and unpredictably. The worker guardrails
  are **`step_limit` (turn cap) and `wall_time_limit`** (§7) — both genuinely per-instance —
  NOT cost.
- Per-worker cost *attribution* is not available in v1 (it would require threading a
  per-agent stats object through upstream — out of scope, and a reason to keep workers cheap
  by model choice, not by cost metering).

**Required by the plan:** fresh `StreamingLitellmModel` + fresh registry + fresh
`LocalEnvironment` + fresh `MiniSweAgentRunner` **per worker** (never shared across threads),
and a **concurrency stress test**: N concurrent mock workers (correctness/isolation) to prove
no cross-talk in results, messages, or tool dispatch.

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

1. `agent_id = getattr(env, "_active_persona", None) or "default"` (inherited; same read
   `CreateJobTool` uses at create_job.py:125). Never read `agent_id` from the model.

   **Correction to an earlier assumption:** `_active_persona` is NOT "stamped at env
   construction." It is set *after* construction, and **only on the ACP/interactive path**
   (acp_agent.py:667/675). The **cron path does NOT stamp it** — `jobs/executor.py:140` builds
   a bare `LocalEnvironment(cwd=workspace)`. Therefore the plan must, in TWO places:
   (a) have the cron executor stamp `env._active_persona = job.agent_id`, and
   (b) have `build_persona_agent` stamp `env._active_persona = agent_id` unconditionally,
   so a worker is always bound to the correct persona regardless of the launch surface.
2. Read optional propagation from `env`: `cancel_flag` (parent interrupt) and
   `_remaining_secs` (cron budget; `None` on the interactive path). These are worker-builder
   additions to the env, not existing attributes.
3. Run `tasks` on a `ThreadPoolExecutor(max_workers=cap)` **created and torn down inside this
   single tool call** (§7) — never a shared/module-level pool. Cap the task count (§7). The
   parent's tool call **blocks** until all workers finish — exactly like a slow `bash` call.
   No scheduler.
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

> **AS-SHIPPED DEVIATION (v1) — tracked in [issue #172](https://github.com/albertovasquez/done/issues/172).**
> The shipped v1 worker is trimmed **further** than the table below: `_run_one_worker`
> calls `runner.run(task_str)` with **no** `base_block` and **no** `agents_block` either —
> the worker's only prompt content is `goal` + `context` + the structured-summary contract
> (the bare upstream `mini.yaml` system line). This was a deliberate "maximally cheap
> context" choice surfaced by the whole-branch review. Whether to add `base_block` +
> AGENTS.md back (so write-capable / shell-running workers obey operating standards) is an
> **open product decision** in #172. The table below is the *original* design intent; the
> code currently implements the "Drop everything but the task" end of that spectrum.

Original design intent — `context_mode="worker"` assembles the prompt from these pieces:

| Piece                                   | Worker gets (design intent)                            | v1 as-shipped |
|-----------------------------------------|--------------------------------------------------------|---------------|
| `base_block` (policy + cwd/model/OS)    | **Include** — small and necessary                      | **Dropped** (#172) |
| `agents_block` (3-tier AGENTS.md)       | **Include for ALL workers** — operating standards apply even to read-only workers running `git`/`gh` | **Dropped** (#172) |
| persona soul (`persona_block`)          | **Drop** — the persona voice is applied by the *parent* when it synthesizes returned summaries, not inside the worker | Dropped ✓ |
| memory block (auto-injected)            | **Drop** — but keep the `load_memory` tool if the workspace has memory, so a worker can pull a fact on demand without prepaying the whole block | Dropped ✓ |
| skills menu (auto-injected)             | **Drop** — keep `load_skill` only if the task's `tools` grants it | Dropped ✓ |
| `goal` + `context` (from parent)        | **Include** — this is the worker's instance prompt     | Included ✓ |

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
- Implemented as a filter at the end of `build_registry(..., toolset)`:
  `tools = [t for t in tools if t.name in toolset]` when `toolset is not None`.
  **Both the model and the agent must receive the SAME filtered registry** — the model uses it
  for `_tool_schemas()` (what the LLM sees) and the agent uses it for dispatch
  (`by_name = {t.name: t for t in self.registry}`, streaming_model.py:104). A mismatch would
  let the model call a tool the agent can't dispatch (or vice-versa). The builder must pass one
  registry object to both.
- **Depth-1 enforcement is an explicit deny rule, not a side effect of `toolset`.** A worker is
  built with an explicit `is_worker=True` (or equivalent mode) that *always* excludes
  `subagent` from its registry, regardless of what `tools` requests. Do not rely on `subagent`
  merely being absent from the default toolset — a task could name it in `tools`. The deny
  must be unconditional for workers.

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
- **But cap the absolute task count** (e.g. a hard `MAX_TASKS_PER_CALL`, ~16): queueing
  unbounded tasks behind a blocked parent thread is a thread-starvation / runaway-cost risk.
  Over the hard cap → reject with a clear tool error (a model emitting 500 tasks is a bug, not
  a workload). The pool is **created and torn down inside the single tool call**, never shared.

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

### Cancellation — best-effort, NOT mid-step (corrected)

`cancel_flag` is checked at **exactly one point**: between steps (tracing_agent.py:154, the
"ESC checkpoint"). So cancellation is **cooperative and coarse**:

- A worker mid-LLM-call or mid-`bash` does **not** stop until the current step completes.
- The parent's `cancel_flag` is passed to each worker so an interrupt *reaches* them, but the
  spec must NOT claim crisp "interrupt propagation." A worker finishes its in-flight step
  first. For a cheap worker on a short turn this is usually sub-second; for a long `bash` it is
  not. This is a known engine limitation (the loop has no mid-step cancel), not something this
  feature fixes.

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
- **The cron `timeout_secs` does NOT reap threads (corrected — Codex Risk B).** The cron
  timeout (`jobs/ops.py:55/68`) makes the supervisor **stop waiting** for the turn; it does
  **not** kill the executor thread or any worker threads it spawned. Because workers run on
  **daemon** threads, they die when the process exits — but a "timed-out" cron job can leave
  worker threads running (and burning tokens) until the daemon process recycles. So the
  per-worker `step_limit` + `wall_time_limit` are the **real** bounds; the job timeout is a
  *supervisor-level* give-up, not a thread reaper. Lean on the per-worker caps, not the job
  timeout, to bound worker work.
- **Interactive (non-cron) path:** `env._remaining_secs = None` → workers use their own
  defaults. No special-casing — the budget cap only activates when a parent sets it.
- **Why the cron path is the *easy* surface, not the hard one:** cron already runs through
  `MiniSweAgentRunner` synchronously on threads, with no ACP loop and no async boundary. A
  cron-launched worker just spawns more threads inside the same headless process. The
  async-boundary risk (the `#81/#91/#99/#138` family) lives only on the interactive TUI path,
  which is deferred to v2.

**Deferred (documented future tightening):** aggregate cost/time accounting across a batch.
v1 relies on **per-worker turn cap + per-worker wall-time cap** as the genuine bounds (the job
timeout is a supervisor give-up, not a reaper — see above; and `cost_limit` is unreliable under
concurrency because of `GLOBAL_MODEL_STATS` cross-talk, §2a). The honest limitation: per-worker
caps bound each worker individually, not the *sum*. Aggregate accounting that actually reaps an
over-budget batch is future work.

---

## 9. Guardrail summary

| Guardrail | Mechanism                              | Default                          | Per-task override |
|-----------|----------------------------------------|----------------------------------|-------------------|
| Turn cap  | `step_limit` (existing `AgentConfig`)  | 15                               | `max_iterations`  |
| Wall-time | `wall_time_limit` (existing)           | `min(default, parent_remaining)` | —                 |
| Tools     | registry `toolset` filter              | `{read, bash}`                   | `tools`           |
| Model     | resolution chain (§6)                  | parent's (until opted cheaper)   | `model`           |
| Depth     | explicit `is_worker` deny of `subagent` | flat (1) — no nested spawn      | none              |
| Concurrency | per-call `ThreadPoolExecutor(max_workers=cap)` | 4 (queue overflow, hard task cap ~16) | —          |
| Interrupt | parent `cancel_flag` passed to workers | best-effort, between-steps only (NOT mid-step) | —     |
| Cost limit | — | **NOT a guardrail** (`GLOBAL_MODEL_STATS` cross-talks across workers, §2a) | — |

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
