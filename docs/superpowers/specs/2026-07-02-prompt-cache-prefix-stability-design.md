# Prompt-cache prefix stability & cache-aware history

**Design for #139 PR 2 (prefix stability) and #105 PR 3 (cache-aware history).**
Date: 2026-07-02. Status: spec — next step is a writing-plans implementation plan.

## Context

PR 1 shipped (#300 + #301): the proxy now enforces a provisioned client api-key,
which makes CLIProxyAPI send the codex upstream a stable `prompt_cache_key`;
byte-identical prompts verify at 97% cached (8,448/8,740 tokens on call 2).
Caching *works* — what remains is making the harness's real prompts byte-stable
enough to benefit.

Measured economics (2026-07-02 diagnostics, real sessions in `harness/runs`):
the system spine is ~4–7k tokens = 18–31% of cumulative prompt cost in long
sessions; the re-sent transcript is 69–82% (worst session: 67 calls, 1.51M
cumulative prompt tokens, 92k max single prompt). So prefix-stabilizing the
spine (PR 2) is the smaller half; keeping the transcript cache-warm (PR 3) is
the prize. Full findings and probe data: #139 comment (2026-07-02).

Reference architecture: hermes-agent treats prompt caching as a hard invariant
(system prompt built once per session and replayed byte-for-byte; late content
appended as user/tool messages; append-only until compaction, which takes one
deliberate miss). This design adopts that invariant, adapted to done's shape.

## The invariant

> **Within a session, the message list sent to a model is append-only and its
> prefix is byte-stable, except at declared cache boundaries.**

Declared boundaries (each is a deliberate, observable, one-time miss):

1. Persona switch (`set_persona`)
2. Worker-model swap (`set_model`)
3. Skills / AGENTS.md content change on disk (picked up next turn)
4. Compaction event (worker `prior`, and chat history once PR 3 lands)

Anything else that changes prompt bytes turn-over-turn is a defect. The
invariant is enforced by tests (byte-stability) and made observable at runtime
(spine-hash boundary events + cache-hit-rate telemetry).

## Non-goals (ruled out by the diagnostics — do not revisit)

- Harness-side `cache_control` plumbing: the proxy strips it on the OpenAI
  surface and auto-injects near-optimal breakpoints on Anthropic-bound
  requests. The vendored engine's dormant `set_cache_control` hook stays off.
- #110 router fast-path: parked on its own data-driven resume criterion. The
  router's user message is per-turn volatile by construction (prompt + rolling
  preamble, already capped at 8 turns, `transcript.py:31,46`) — a cache-cold
  router call is the accepted design.
- claude-* upstream work: blocked on #299 (cloak drops the client system
  prompt entirely).
- Cross-worker / parent↔worker prefix sharing: subagent workers build their own
  minimal prompt (`subagent.py:94-105`) — separate cache domain, out of scope.

---

## PR 2 — prefix stability (#139)

### 2a. Move per-turn skill bodies out of the system prompt

**This is the single biggest remaining invalidator.** `TracingAgent.
_render_template` (`tracing_agent.py:102-116`) appends `skill_block` — the
router-picked skill bodies for *this turn* — to the **system** message. The
system message is message[0]; when consecutive turns pick different skills,
its bytes change and the entire prompt goes cache-cold, no matter how stable
everything else is.

Change: `skill_block` moves to the **instance (user) message** — the per-turn
task message that is appended at the tail anyway. Concretely:

- `_render_template` appends `base + persona + memory` to the system template
  (unchanged) and appends `skill_block` to the **instance** template render
  instead, ahead of the task text, with an explicit delimiter
  (`## Skills loaded for this task`).
- The skills *menu* (names + descriptions) stays in the spine — it is
  session-stable. Lazy bodies via the `load_skill` tool already arrive as
  appended tool results (cache-friendly, no change).

This matches hermes exactly: stable index in the prefix, bodies ride the tail.
Prompt-shape risk (the model now sees skill bodies in the user turn) is
mitigated by the delimiter and by the fact that `load_skill`-fetched bodies
already appear outside the system prompt today.

### 2b. Reorder the spine most-stable-first; split the Environment block out

`render_base_prompt` (`base_prompt.py:81-117`) currently renders:
`BASE_POLICY → # Environment → # Persona files → skills menu → AGENTS.md`.
The Environment block (`cwd`, `model_id`, `platform.platform()`) sits second —
ahead of everything that is *more* stable than it. Within one session it is
constant, but a mid-session model swap invalidates every byte below it, and
across seats/sessions it kills prefix sharing (fleet economics, #61/#14).

New assembled system order, most-stable → least-stable:

| # | block | varies with |
|---|-------|-------------|
| 1 | engine system template + BASE_POLICY | release |
| 2 | AGENTS.md block | project file edits |
| 3 | skills menu | project skills / flow scope |
| 4 | persona-files pointer block | persona |
| 5 | `persona_block` (SOUL/IDENTITY/USER) | persona file edits |
| 6 | `memory_block` | session (cached once at `acp_agent.py:405`) |
| 7 | **Environment block** | session; model swap mid-session |

Mechanics: the Environment block moves out of `render_base_prompt` into its
own `render_env_block(...)` in `base_prompt.py`; the two assembly sites
(`tracing_agent._render_template` for the agent path, `acp_agent.py:540` /
`chat_handler` system_content for the chat path) append it last. A model swap
now invalidates only the final block instead of ~everything.

Non-issues, documented so nobody re-fixes them: `platform.platform()` and
`cwd` are session-stable (per boot / per session); daily-notes memory rotation
via `date.today()` is computed once per session (`acp_agent.py:405-412`) so it
cannot rotate mid-session — cross-session daily rotation is fine because
caches don't outlive the day anyway (5m–1h TTLs).

### 2c. Cache observability (ship first, verify everything else with it)

`_usage_from_extra` (`tracing_agent.py:40-67`) reads total/prompt/completion
and **drops** cache fields that are already in the raw response. Extend it to
extract `prompt_tokens_details.cached_tokens` (OpenAI shape) and
`cache_read_input_tokens`/`cache_creation_input_tokens` (Anthropic shape) into
the usage dict. It then flows for free through the `llm.return` event
(`tracing_agent.py:314-319`) into `events.jsonl`/`trace.jsonl` and the TUI
usage footer (`acp_agent.py:777-786`), which gains a hit-rate figure
(`cache 84%`).

### 2d. Spine-hash boundary events (the silent-invalidator alarm)

Per session, keep a hash of the last-sent system-message bytes. When it
changes, emit a `cache.boundary` event naming which block changed (compare
per-block hashes: base / persona / memory / env). Declared boundaries (§the
invariant) produce an expected event; anything else is a regression made
visible in the trace instead of silently costing money. This is how we catch
the *next* `skill_block`-class bug.

### PR 2 tests

- **Byte-stability regression test (the load-bearing one):** run a fake-model
  `TracingAgent` for two turns where the router-picked skills *differ*;
  assert the system message bytes are identical across turns and the skill
  bodies appear in the instance message. This test fails on today's code.
- Block-order test: assembled system prompt has the seven blocks in the table
  order (assert by marker strings, not full bytes).
- Env-swap test: changing `model_id` between turns changes *only* the env
  block (prefix up to block 6 byte-identical).
- `_usage_from_extra` extraction for both usage shapes; `cache.boundary`
  emitted on spine change with the changed-block label, absent otherwise.
- Live acceptance (manual, post-merge): a real session's footer shows non-zero
  cache %, and `trace.jsonl` `llm.return` events carry `cached` counts rising
  turn-over-turn.

---

## PR 3 — cache-aware history (#105)

### 3a. The rule: episodic, never sliding

A sliding window (drop oldest turn each turn once full) mutates the head of
the message list *every turn* — permanently cache-cold. The invariant demands
**episodic** trimming: grow append-only → breach budget → one chop down to a
target → grow again. Amortized, that is one declared miss per episode instead
of a miss per turn.

Worker `prior` already behaves this way: `compress()` (`compaction.py:198-251`)
fires only when `before_tokens > budget` (threshold 0.5 × ctx_window, target
0.2), replaces the middle with a summary, and is untouched otherwise. **Keep
it.** PR 3 adds: emit the compaction as a `cache.boundary` event (reason:
`compaction`), and a regression test that `prior`'s prefix is byte-stable
across turns *between* compactions (protect ordering + `_sanitize_tool_pairs`
must not reorder the head).

### 3b. Chat history gets the same episodic budget

Chat is the unbounded path today: `answer_stream` prepends the full transcript
every turn (`chat_handler.py:221`, fed from `acp_agent.py:436`). Reuse the
existing machinery rather than inventing a second policy: apply
`compaction.compress()` to the chat history with the same config surface
(threshold/target/ctx_window from `resolve_ctx_window` on the chat model),
summarizer wired to the same chat model, and the built-in degrade-to-truncate
on summarizer failure. Episodic by construction because `compress()` already
is. Emit the same `cache.boundary` event.

### 3c. Explicitly unchanged

- Router preamble: already tail-capped at 8 turns; its user message is
  volatile by design. Touching it buys nothing.
- No token-exact budgeting: `compaction.py`'s char/4 estimate stays. Caching
  changed the economics — the budget's job is bounding absolute context size
  (latency, quota), not shaving tokens.
- O(n²) note for the issue: with caching live, re-sent prefix tokens are
  cache reads (~0.25–0.5× on codex), so #105's cost multiplier drops even
  before PR 3; PR 3 bounds the absolute size and keeps the reads warm.

### PR 3 tests

- Episodic test: drive chat history past budget; assert exactly one chop
  (boundary event), then byte-stable prefix growth resumes; assert **no**
  per-turn head mutation under budget.
- Worker-prior prefix stability between compactions (two turns, no budget
  breach → `prior` head bytes identical).
- Degrade path: summarizer failure → truncate method, still episodic.

## Sequencing & risk

Ship PR 2 before PR 3 (observability + stable spine make PR 3's effect
measurable). Each PR is independently mergeable and reversible.

Risks: (1) skills-in-instance changes prompt shape — watch task-outcome
regressions in the eval runs; delimiter keeps it legible. (2) Chat compaction
can summarize away early context the user still references — same trade
already accepted for the worker path, and the summary message preserves the
gist. (3) Spine reorder changes block adjacency the model sees — content is
unchanged; no evidence adjacency matters, and the fleet-sharing upside is
measured.
