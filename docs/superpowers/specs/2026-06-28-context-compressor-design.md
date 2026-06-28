# Context Compressor — Design Spec

**Date:** 2026-06-28
**Branch:** `worktree-context-compressor`
**Status:** Design approved; implementation pending
**Closes / relates:** #105 (O(n²) transcript re-sent every turn)

## Background

Done re-sends the full cross-turn transcript to the model on every turn. In
`tracing_agent.run()` the message list is rebuilt each turn and `prior` is
injected at one chokepoint (`harness/tracing_agent.py:99`,
`self.add_messages(*(prior or []))`), between a fresh system message (`:97-98`)
and a fresh instance message (`:100-101`). The transcript only grows, so cost
and latency grow O(n²) in turn count by design. This is audit issue #105.

Nous Research's **Hermes Agent** ships a clean, copy-able solution: a
`ContextCompressor` that summarizes the middle of the conversation while
protecting a head and a recent tail, plus a `_sanitize_tool_pairs()` step that
keeps the message list API-valid after the cut. We borrow the algorithm and its
hardest-won lessons.

### Lessons borrowed from Hermes (and why)

1. **Summarize the middle, protect head + tail.** Recent turns and the opening
   context are kept verbatim; only the middle is lossy. (Hermes
   `protect_first_n` / `protect_last_n`.)
2. **Sanitize orphaned tool pairs after the cut** — dropping the middle can
   orphan a `tool_call` whose result was kept, or a result whose call was
   dropped. Both must be repaired or the API rejects the request.
3. **Always have a non-LLM fallback.** Hermes had compression-failure problems
   (issue #9666); if the summarization call fails, degrade to truncation rather
   than crashing the user's turn.
4. **Per-surface thresholds, not one global number.** Hermes runs two thresholds
   (agent 50% / gateway hygiene 85%) because a single 50% fired prematurely.
   Done has one surface, so one configurable threshold — but the lesson informs
   the *configurable* design.

### Lessons borrowed from Done's own history

- **Prompt caching is dead on this path** and is therefore **out of scope**.
  `vibeproxy.model_id()` hard-prefixes `"openai/"` (`vibeproxy.py:37`);
  Anthropic `cache_control` is a `/v1/messages`-only feature, silently ignored
  on `/v1/chat/completions`. So unlike Hermes, the compressor here has **no
  caching payoff** — its sole value is bounding the message-list size (#105) and
  staying under the context limit. We do not sell or design around caching.
  (See memory: `router-110-fastpath-parked`.)
- **Do not couple to vibeproxy.** All model/provider knowledge resolves through
  the agent's *existing* model abstraction and config, so a future provider
  swap (e.g. OpenRouter) needs no compressor changes.
- **LLM-call tests hit `auth_unavailable`** without Claude auth. The pure module
  takes injected callables so 100% of its tests run with fakes — no network.
- **Verify message shape against live code, not memory.** Done's default path
  (`mini.yaml` + `StreamingLitellmModel` →
  `upstream/.../models/utils/actions_toolcall.py`) pairs an assistant message
  carrying `tool_calls:[{id}]` with a tool message carrying `tool_call_id`. The
  matching key is the **stable string id**, not list index. The mock model
  (`harness/models_mock.py`) reproduces this exact shape, so tests are faithful.

## Goals / Non-goals

**Goals**
- Bound the cross-turn transcript so the prompt stays under a configurable
  context budget.
- Provider-agnostic: survives a swap from vibeproxy to OpenRouter (or
  Anthropic-native) with zero compressor edits.
- Never crash a turn because compaction failed.
- Fully unit-testable without a real model.
- Default OFF in v1 (opt-in), flipped on in a follow-up once exercised.

**Non-goals (v1)**
- Prompt caching (dead on this path).
- A second "gateway hygiene" threshold (Done has one surface).
- A formal pluggable `ContextEngine` ABC/registry (the injected-callable seam is
  the extension point until a second engine actually exists).
- Idle / pre-flight compaction.

## Architecture (Approach A: pure function + injected adapter)

A new module **`harness/compaction.py`** that imports nothing model-related.
All provider knowledge lives in a thin agent-side adapter that injects callables.

```
tracing_agent.run()                      compaction.py (pure)         agent-side adapter
─────────────────                        ────────────────             ─────────────────
  system msg  (:97-98)
  prior  ────────────────► compress(prior, *, summarize,            summarize  -> self.model
                                    count_tokens,                   count_tokens -> len//4 (swappable)
                                    fixed_overhead_tokens,          ctx_window  -> config number
                                    ctx_window, threshold, ...)
              ◄──────────── CompactResult.messages
  instance msg (:100-101)
```

### Entry point

```python
def compress(prior, *, summarize, count_tokens, fixed_overhead_tokens,
             ctx_window, threshold=0.5, target_ratio=0.2,
             protect_head_n=0, protect_last_n=20) -> CompactResult
```

- `prior: list[dict]` — the cross-turn transcript. **The system message is NOT
  in `prior`** (added separately at `:97-98`), so head-protection operates on
  transcript only; default `protect_head_n=0`.
- `summarize: Callable[[list[dict]], str]` — injected; produces middle-turn
  prose. The **module** owns wrapping it into a message dict and placing it;
  the callable only returns text. Never sees dicts-out.
- `count_tokens: Callable[[str], int]` — injected, provider-neutral estimator.
- `fixed_overhead_tokens: int` — tokens of everything outside `prior`
  (system + base/persona/memory/skill blocks + instance), so the trigger budgets
  `prior` against the *real* remaining window.
- `ctx_window: int` — from config; no per-model table, nothing keyed on
  vibeproxy ids.

Returns:
```python
@dataclass
class CompactResult:
    messages: list[dict]
    compressed: bool
    method: str            # "none" | "summary" | "truncated"
    before_tokens: int
    after_tokens: int
    before_msgs: int
    after_msgs: int
```

## Algorithm

`compress` is a pure pipeline; it never mutates `prior` (returns a new list).

### Step 0 — Trigger
```
budget = max(threshold * ctx_window - fixed_overhead_tokens, MIN_BUDGET_FLOOR)
prior_tokens = count_tokens(render(prior))
if prior_tokens <= budget:
    return CompactResult(prior, compressed=False, method="none", ...)
```
- **Degenerate-budget guard (caveman-fix):** if
  `fixed_overhead_tokens > threshold * ctx_window`, the fixed blocks themselves
  exceed budget — compaction can't help. Log a warning, return uncompressed
  (`method="none"`). `MIN_BUDGET_FLOOR` keeps the tail target from going
  negative.
- Below budget → exact same list, **no LLM call** (assert in tests via a spy).
  This is the hot path on short sessions and must be cheap.

### Step 1 — Split head / middle / tail
```
head = prior[:protect_head_n]                       # default 0
tail = grow from the END until BOTH:
         len(tail) >= protect_last_n  (default 20)
         AND tokens(tail) >= target_ratio * budget
middle = prior[len(head) : len(prior)-len(tail)]
if not middle:                                       # nothing to compress
    return CompactResult(prior, compressed=False, method="none", ...)
```
Tail grows back-to-front so the most recent turns are kept whole. Two stop
conditions: a count floor (sane minimum when messages are tiny) and a token
target (so a few huge recent messages don't blow the tail past budget).

### Step 2 — Summarize the middle (with fallback — the Hermes #9666 lesson)
```python
try:
    text = summarize(middle)                         # via self.model, injected
    summary = {"role": "user",
               "content": "[Earlier conversation summarized to save context]\n" + text}
    new = head + [summary] + tail
    method = "summary"
except Exception:                                    # timeout / auth / provider error
    new = head + tail                                # drop middle, never crash the turn
    method = "truncated"
```
The summarize call is the **only** place a model is touched, and it is fully
guarded. The marker is human-visible and tells the model the gap is intentional.

### Step 3 — Sanitize tool pairs (ported from Hermes `_sanitize_tool_pairs`)
Run on `new` **after** assembly, because dropping the middle can orphan
references that straddle the cut. **Matching key = `tool_call_id` ↔
`tool_calls[].id`** (verified against `actions_toolcall.py:74,104-106` and
`models_mock.py:41-46`):
```
- tool message ({"role":"tool","tool_call_id":X}) whose assistant tool_calls[].id==X
    was removed                      → drop the tool message
- assistant tool_calls[].id==X whose matching tool message was removed
                                     → inject a stub tool message
    {"role":"tool","tool_call_id":X,
     "content":"[result omitted during context compaction]"}
```
Keeps the message list API-valid regardless of provider.

### Step 4 — Return
`CompactResult(new, compressed=True, method, before/after tokens+msgs)`.

### Boundedness, not idempotence (caveman-fix)
A second `compress` pass folds a prior summary into the new middle and
re-summarizes it (summary-of-summary). This is acceptable but is **not**
idempotent. The test asserts the output is **bounded and API-valid**, not equal
to a re-run. We do not promise idempotence.

## Agent-side adapter (provider-agnostic injection)

`compaction.py` stays pure. All provider knowledge lives in a small adapter the
agent builds and injects.

### Config block
A new `compaction:` section, default OFF:
```yaml
compaction:
  enabled: false          # v1 default-off (#105 bound stays open until flipped)
  threshold: 0.5
  target_ratio: 0.2
  protect_head_n: 0
  protect_last_n: 20
  context_window: 32000   # CONSERVATIVE default (smallest realistic target window,
                          #   not 200k) so a small-window provider can't overflow
                          #   silently. Raise per deployment.
  summary_model: ""       # "" => reuse the worker model; else an explicit model id
```
`context_window` is a plain number — no per-model lookup, nothing keyed on
vibeproxy ids. An OpenRouter switch just changes this number (or a provider may
later populate it).

### Injected callables, built in the agent
A `Compaction` adapter holding closures, constructed where persona/model is
already resolved:

- **`summarize(middle) -> str`** — wraps a model call through the *same model
  abstraction the agent already holds* (never imports vibeproxy):
  - default: `msg = self.model.query([{system: COMPRESS_PROMPT},
    {user: render(middle)}])`, then `text = msg.get("content") or ""`
    (caveman-fix: `query` returns a **dict**, mirror `tracing_agent.query:166`).
  - if `summary_model` set: construct that model via the *existing
    model-construction path* persona model-binding already uses.
  - **Cost accounting (caveman-fix):** add `msg["extra"]["cost"]` to
    `self.cost` (mirror `tracing_agent.query:163`). This call does **not** go
    through `TracingAgent.query()`, so it does **not** bump `n_calls` and is
    **excluded from `step_limit`** (infrastructure, not an agent step). Cost is
    real money and is always counted.
- **`count_tokens(text) -> int`** — provider-neutral, default `len(text)//4`,
  swappable to `litellm.token_counter` later without touching `compaction.py`.
  No assumption that the openai tokenizer is the real model's. Because the
  estimate is rough (~30% off on code/JSON), `context_window` defaults
  conservatively.
- **`fixed_overhead_tokens`** — computed once per turn as
  `count_tokens(system + base/persona/memory/skill + instance)`.

### The seam call (the one loop edit)
In `tracing_agent.run()` between `:97` and `:99`:
```python
prior = prior or []
if self._compaction and self._compaction.enabled:
    result = compaction.compress(prior, **self._compaction.params())
    prior = result.messages
    if result.compressed:
        self._emitter.emit("context.compacted", method=result.method,
                           before_tokens=result.before_tokens,
                           after_tokens=result.after_tokens,
                           before_msgs=result.before_msgs,
                           after_msgs=result.after_msgs)
self.add_messages(*prior)
```
No-op path (default) is byte-identical to today.

### Known limitation (caveman-noted)
The `summarize` call re-enters `self.model` and sits **outside** the loop's ESC
checkpoint (`tracing_agent.py:108`). A long compaction summary is therefore a
brief unkillable window. Acceptable for v1 (one bounded call); we do **not**
claim ESC is responsive during compaction.

## Testing

### Unit — `tests/test_compaction.py` (pure, no LLM, no network)
All cases use a **fake summarizer** (`lambda m: "SUMMARY"`, plus a spy variant)
and a deterministic `count_tokens`. RED-first.

1. Below budget → no-op; same list; `compressed=False`; summarizer **never
   called** (spy assert).
2. Above budget → summary; head+tail verbatim; one `[…summarized…]` message
   between them; `method="summary"`.
3. Tail sizing: `protect_last_n` floor honored AND tail grows to
   `target_ratio*budget`; most-recent messages kept whole.
4. Empty middle → no-op.
5. **Summarizer raises → truncation fallback:** `method="truncated"`, returns
   `head+tail`, no crash. (Hermes #9666 as an executable test.)
6. **Tool-pair sanitization:** middle drop orphans a `tool` message → dropped;
   orphans an assistant `tool_calls[].id` → stub injected; output well-formed.
   Fixtures use the **mock-model shape** (`call_i_j` ids), faithful to the real
   `LitellmModel` per `models_mock.py:5`.
7. **Boundedness (not idempotence):** `compress(compress(x))` is bounded and
   API-valid (no stacked markers crash); not asserted equal.
8. Degenerate budget: `fixed_overhead > threshold*ctx_window` → `method="none"`
   + warning, no crash.

### Integration — one test, mock model (no real model)
`TracingAgent` with the mock model and `compaction.enabled=True`, fed a long
`prior`: assert the model receives a **bounded** message list (seam fired) and
`context.compacted` was emitted. Uses the existing mock fixture → no
`auth_unavailable`.

## Observability

- New trace event **`context.compacted`** (method, before/after tokens + msg
  counts) via the existing `--debug` JSONL path; TUI is sole writer (existing
  contract).
- TUI surfacing minimal in v1: a dim turn-footer note (e.g.
  `↯ context compacted 18→6 msgs`) reusing `.turn-meta` styling (PR #97/#100).
  No new widget. Verify visually via `save_screenshot`→PNG (low risk — a string).

## Implementation order (TDD)

1. `harness/compaction.py` pure module + `tests/test_compaction.py` (RED→GREEN),
   cases 1–8. No agent wiring yet.
2. `Compaction` adapter + config block (`compaction:`), built where persona/model
   resolves.
3. Seam edit in `tracing_agent.run()` + `context.compacted` event + integration
   test.
4. TUI footer note + visual verification.

Each step keeps the full suite green (`.venv/bin/python -m pytest tests/ -q`).

## Open items to confirm during implementation
- Exact place the adapter is constructed so `fixed_overhead_tokens` sees the
  *final* base/persona/memory/skill blocks for the active session.
- `render(messages) -> str` helper used by both `count_tokens` and `summarize`
  input (concatenate role+content; tool messages included).
