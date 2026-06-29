# Compaction Default-ON + Derived ctx_window + Observability — Design Spec

**Date:** 2026-06-29
**Branch:** `worktree-compaction-default-on`
**Status:** Design approved (delta on merged PR #143)
**Builds on:** `docs/superpowers/specs/2026-06-28-context-compressor-design.md` (merged)
**Relates:** #105

## Background

PR #143 shipped the context compressor **default-OFF** with a hardcoded
`ctx_window=32000` and a `len//4` token estimate. The mechanism is proven but
was never exercised on a real session, and OFF means the O(n²) bound (#105)
stays open for everyone until they opt in. This PR turns it **ON by default**,
which requires two supporting changes: an accurate context window (a guessed
32000 is wrong on every model Done ships — they're 200K–1M), and observability
so an always-on feature is debuggable via `--debug`.

## Decisions (all settled — no open questions)

1. **Default ON.** `compaction.enabled` defaults to `true` (config + `Compaction` dataclass).
2. **`ctx_window` resolution:** `config override → curated CONTEXT_WINDOWS table → litellm.get_max_tokens fallback → conservative floor`.
3. **Observability:** two new trace events — `context.compaction.eval` (every turn) and `context.compaction.summarize` (when summary fires) — threaded through `compress()` via an **optional** `on_event` callback (default `None` = no-op, so all merged pure tests stay green).

### Why a curated table, not `get_max_tokens` alone

Verified against live litellm: `get_max_tokens` does **not** know Done's models.
It returns a wrong `128000` for both `gpt-5.4` and `claude-opus-4-8` (both
post-date litellm's registry), and `16384` (max-output, not context) for
`gpt-4o`. The real windows (from the claude-api skill's authoritative table and
Done's shipped defaults):

| model (normalized name) | real context window |
|---|---|
| `gpt-5.4` | 400000 |
| `gpt-5.4-mini` | 400000 |
| `claude-opus-4-8` | 1000000 |
| `claude-opus-4-7` | 1000000 |
| `claude-opus-4-6` | 1000000 |
| `claude-sonnet-4-6` | 1000000 |
| `claude-haiku-4-5` | 200000 |
| `claude-fable-5` | 1000000 |

(Values are the authoritative numbers as of 2026-06-29; the table is a Done-local
constant to maintain as models change — documented as such.)

## Architecture

All three changes are additive and live in the same two files PR #143 touched
(`harness/compaction.py`, `harness/tracing_agent.py`) plus the trace relay in
`harness/acp_agent.py` already wired for `context.compacted`.

### Change 1 — Default ON

- `harness/compaction.py`: `Compaction.enabled` default `True` (already is).
  `build_compaction` already returns a live adapter when `cfg.get("enabled")` is
  truthy. The flip is in **how the config defaults when absent**: today the
  config block is absent → `self._compaction_cfg is None` → no adapter. To make
  ON the default, `tracing_agent.run()` builds the adapter when the cfg is
  present-and-enabled **OR absent** (absent → use all defaults with
  `enabled=True`). Explicit `compaction.enabled = false` still disables.
- Net: a fresh install with no `compaction:` block now compacts; a config with
  `enabled: false` does not. The no-op path still exists (explicit opt-out).

### Change 2 — Derived ctx_window

New in `harness/compaction.py`:

```python
CONTEXT_WINDOWS = {            # Done-local; maintain as models change (2026-06-29)
    "gpt-5.4": 400_000, "gpt-5.4-mini": 400_000,
    "claude-opus-4-8": 1_000_000, "claude-opus-4-7": 1_000_000,
    "claude-opus-4-6": 1_000_000, "claude-sonnet-4-6": 1_000_000,
    "claude-haiku-4-5": 200_000, "claude-fable-5": 1_000_000,
}
DEFAULT_CONTEXT_WINDOW = 32_000   # conservative floor for unknown models

def resolve_ctx_window(model_name, cfg_override=None):
    """config override > curated table > litellm.get_max_tokens > floor.
    model_name is normalized (strip a leading 'openai/' provider prefix)."""
    if cfg_override:                       # explicit config wins, always
        return int(cfg_override)
    name = (model_name or "").split("/", 1)[-1]   # 'openai/gpt-5.4' -> 'gpt-5.4'
    if name in CONTEXT_WINDOWS:
        return CONTEXT_WINDOWS[name]
    try:
        from litellm import get_max_tokens
        n = get_max_tokens(name)
        if n:
            return int(n)
    except Exception:
        pass
    return DEFAULT_CONTEXT_WINDOW
```

- `build_compaction` calls `resolve_ctx_window(model_name, cfg.get("ctx_window"))`
  instead of `int(cfg.get("ctx_window", DEFAULT_CONTEXT_WINDOW))`. It gains a
  `model_name` parameter (passed by the agent from `self.model.config.model_name`).
- **Config key stays `ctx_window`** (the override). The table is the *default*
  source; the key name is unchanged so existing/explicit configs keep working.
- `litellm` is imported **lazily inside the function** (mirrors the vibeproxy
  note that litellm import costs ~1s on the startup path) — never at module top.
  This keeps `compaction.py` import-light; the function is only called when
  compaction actually builds an adapter.

### Change 3 — Observability (optional `on_event` callback)

`compress()` and the `summarize` closure gain an **optional** keyword
`on_event: Callable[[str, dict], None] | None = None`. Default `None` → no-op →
every merged pure test (which omits it) is byte-for-byte unaffected.

`compress()` emits, when `on_event` is set:

```python
# at the trigger, EVERY call (fired or not):
on_event("context.compaction.eval", {
    "prior_tokens": before_tokens, "budget": budget,
    "ctx_window": ctx_window, "fixed_overhead": fixed_overhead_tokens,
    "decision": method,          # "none" | "summary" | "truncated"
})
```

The `summarize` closure (in `build_compaction`) wraps its model call and emits:

```python
on_event("context.compaction.summarize", {
    "in_tokens": estimate_tokens(user_content),
    "out_tokens": estimate_tokens(text),
    "cost": cost, "elapsed_s": round(elapsed, 3),
})
```

`elapsed` is computed inside the closure from a monotonic clock the agent
injects (the agent already has `self._t`/`time` — pass a `now: Callable[[], float]`
or compute in the closure with `time.monotonic()`, which is allowed in the
agent, not the pure module). To keep `compaction.py` pure (no `time` import
needed for correctness), the agent passes a `now` callable into
`build_compaction`; default `now=None` skips elapsed (tests).

**Wiring:** `tracing_agent.run()` passes `on_event=self._emitter.emit` into
`compress(...)` (via the adapter's `params()` — add `on_event` to the dict) and
into `build_compaction(..., on_event=self._emitter.emit)` for the summarize
event. The existing `context.compacted` event is unchanged.

**Delivery to `--debug`:** the relay in `acp_agent.py:run_engine` forwards ALL
engine events when `self._debug` (the `_relay` debug branch sends every event
over `with_meta`). So `context.compaction.eval` and `.summarize` reach the
`--debug` JSONL trace automatically — **no acp_agent change needed**. In
non-debug mode the relay only captures `context.compacted`, so the two new
events are silently dropped (correct — they're debug-only diagnostics).

## Testing

### Unit (`tests/test_compaction.py`, extend)
- `resolve_ctx_window`: config override wins; known model → table value; `openai/`-prefixed name normalized; unknown model → `get_max_tokens` (monkeypatched) → floor when that returns falsy/raises.
- `build_compaction` default `enabled` true; passing `model_name` resolves the window from the table.
- `compress(on_event=spy)`: emits `context.compaction.eval` with the right `decision` on (a) below-budget no-op, (b) summary, (c) truncated; spy NOT called when `on_event=None` (default path unchanged — assert merged tests still pass).
- `summarize` with `on_event=spy` emits `context.compaction.summarize` with in/out token estimates + cost.

### Integration (`tests/test_compaction_integration.py`, extend)
- Default-ON: construct `TracingAgent` with **no** compaction kwarg + long prior + mock model → assert compaction fires (`context.compacted` emitted) — proves ON-by-default.
- Explicit `enabled: false` → no `context.compacted` (opt-out still works).
- ctx_window: agent with a `claude-opus-4-8` mock model name resolves 1_000_000 (assert via a spied/injected resolver or the eval event's `ctx_window`).

### No real LLM anywhere — fakes + mock model (the merged constraint holds).

## Migration / compatibility notes

- **Behavior change for existing users:** anyone on the merged version with no
  `compaction:` block goes from OFF to ON. This is the intended flip. Document
  in the PR body; the safety properties (fail-to-truncation, degenerate-budget
  no-op, per-turn rebuild) all carry over from #143.
- **Opt-out is `compaction.enabled: false`** — call this out in the PR so anyone
  who wants the old behavior has a one-line escape.
- README/done.conf config-surface docs (the M2 follow-up) are folded into THIS
  PR now that the feature is on by default and must be discoverable.

## Out of scope (unchanged from #143)
Prompt caching (dead on this path); a second gateway-hygiene threshold; a formal
pluggable ContextEngine ABC.
