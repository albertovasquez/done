# Compaction Default-ON + Derived ctx_window + Observability — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the merged context compressor ON by default, resolve `ctx_window` from a curated per-model table (not a guessed 32000), and add `--debug` observability for the compaction decision and summary call.

**Architecture:** Three additive changes to the two files PR #143 already touched (`harness/compaction.py`, `harness/tracing_agent.py`). No `acp_agent.py` change — its `--debug` relay already forwards all engine events. Default-ON is achieved by building the adapter when the `compaction` config is absent OR present-and-enabled; explicit `enabled: false` opts out. Observability rides an optional `on_event` callback (default `None`) so every merged pure test is unaffected.

**Tech Stack:** Python 3.11+, dataclasses, pytest. `litellm.get_max_tokens` imported lazily inside one function as a fallback only (no new top-level dep, no startup cost).

## Global Constraints

- `harness/compaction.py` MUST NOT import `vibeproxy`, `litellm`, or any model module **at module top**. `litellm.get_max_tokens` may be imported **lazily inside `resolve_ctx_window`** as a fallback only.
- The optional `on_event` callback defaults to `None` (no-op). All merged tests that call `compress()` without it MUST stay green — do not change their behavior.
- **Default-ON semantics:** no `compaction:` config block → compaction is ON (all defaults, `enabled=True`). Explicit `compaction.enabled = false` → OFF (opt-out preserved).
- **ctx_window precedence:** config `ctx_window` override → curated `CONTEXT_WINDOWS` table → `litellm.get_max_tokens` → `DEFAULT_CONTEXT_WINDOW` floor (32000).
- Model name is normalized by stripping a leading `openai/` provider prefix before table lookup.
- No real-LLM tests — fakes + the mock model (`harness/models_mock.py`). Avoids `auth_unavailable`.
- Never crash a turn (the merged fail-to-truncation + degenerate-budget no-op are unchanged).
- **Test command (from worktree root):** `.venv/bin/python -m pytest tests/ -q` (baseline is 873 green at branch start).

## File Structure

- Modify: `harness/compaction.py` — add `CONTEXT_WINDOWS`, `resolve_ctx_window`; `build_compaction` gains `model_name` + `on_event` params and uses the resolver; `compress` gains `on_event` and emits `context.compaction.eval`; `Compaction.params()` includes `on_event`.
- Modify: `harness/tracing_agent.py:101-137` — build adapter when cfg absent-or-enabled; pass `model_name` + `on_event=self._emitter.emit`.
- Test: `tests/test_compaction.py` (unit), `tests/test_compaction_integration.py` (integration).

---

### Task 1: Curated ctx_window resolver

**Files:**
- Modify: `harness/compaction.py`
- Test: `tests/test_compaction.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `CONTEXT_WINDOWS: dict[str, int]` — Done's shipped models → real context windows.
  - `def resolve_ctx_window(model_name: str | None, cfg_override=None) -> int` — precedence: `cfg_override` (truthy) → `CONTEXT_WINDOWS[normalized name]` → `litellm.get_max_tokens(normalized name)` → `DEFAULT_CONTEXT_WINDOW`. Normalizes `model_name` by stripping a leading `openai/`.
  - `DEFAULT_CONTEXT_WINDOW` already exists (32000) — keep it.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_compaction.py (append)
from harness.compaction import resolve_ctx_window, CONTEXT_WINDOWS, DEFAULT_CONTEXT_WINDOW

def test_resolve_ctx_window_config_override_wins():
    # override beats everything, even a known model
    assert resolve_ctx_window("claude-opus-4-8", cfg_override=12345) == 12345

def test_resolve_ctx_window_known_model_from_table():
    assert resolve_ctx_window("gpt-5.4") == CONTEXT_WINDOWS["gpt-5.4"]
    assert resolve_ctx_window("claude-opus-4-8") == 1_000_000

def test_resolve_ctx_window_strips_openai_prefix():
    # vibeproxy presents models as 'openai/<name>'
    assert resolve_ctx_window("openai/gpt-5.4") == CONTEXT_WINDOWS["gpt-5.4"]

def test_resolve_ctx_window_unknown_model_falls_back_to_floor(monkeypatch):
    # force the litellm fallback to return nothing -> floor
    import harness.compaction as c
    def fake_get_max_tokens(name): return None
    monkeypatch.setattr(c, "_get_max_tokens", fake_get_max_tokens, raising=False)
    assert resolve_ctx_window("totally-unknown-model-xyz") == DEFAULT_CONTEXT_WINDOW

def test_resolve_ctx_window_uses_litellm_when_available(monkeypatch):
    import harness.compaction as c
    monkeypatch.setattr(c, "_get_max_tokens", lambda name: 55555, raising=False)
    assert resolve_ctx_window("some-litellm-known-model") == 55555
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_compaction.py -k resolve_ctx_window -v`
Expected: FAIL — `ImportError: cannot import name 'resolve_ctx_window'`

- [ ] **Step 3: Write minimal implementation**

Add to `harness/compaction.py` (near `DEFAULT_CONTEXT_WINDOW`, after it):

```python
# Done's shipped models -> real context windows (authoritative as of 2026-06-29).
# litellm.get_max_tokens is WRONG for these (it predates them: returns 128000 for
# gpt-5.4 and claude-opus-4-8, which are really 400k/1M), so this table wins over it.
# Maintain as models change.
CONTEXT_WINDOWS = {
    "gpt-5.4": 400_000,
    "gpt-5.4-mini": 400_000,
    "claude-opus-4-8": 1_000_000,
    "claude-opus-4-7": 1_000_000,
    "claude-opus-4-6": 1_000_000,
    "claude-sonnet-4-6": 1_000_000,
    "claude-haiku-4-5": 200_000,
    "claude-fable-5": 1_000_000,
}


def _get_max_tokens(name: str):
    """Lazy litellm fallback for unknown models. Imported here (not at module top)
    because litellm import costs ~1s on the startup path; this is only called when
    compaction builds an adapter for a model absent from CONTEXT_WINDOWS."""
    try:
        from litellm import get_max_tokens
        return get_max_tokens(name)
    except Exception:
        return None


def resolve_ctx_window(model_name, cfg_override=None) -> int:
    """Resolve the model's context window:
    config override > curated table > litellm.get_max_tokens > floor.
    `model_name` is normalized by stripping a leading 'openai/' provider prefix."""
    if cfg_override:
        return int(cfg_override)
    name = (model_name or "").split("/", 1)[-1]
    if name in CONTEXT_WINDOWS:
        return CONTEXT_WINDOWS[name]
    n = _get_max_tokens(name)
    if n:
        return int(n)
    return DEFAULT_CONTEXT_WINDOW
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_compaction.py -k resolve_ctx_window -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add harness/compaction.py tests/test_compaction.py
git commit -m "feat(compaction): curated ctx_window resolver (table > litellm > floor)"
```

---

### Task 2: build_compaction uses the resolver + default-ON dataclass

**Files:**
- Modify: `harness/compaction.py` (`build_compaction`)
- Test: `tests/test_compaction.py`

**Interfaces:**
- Consumes: `resolve_ctx_window` (Task 1).
- Produces:
  - `build_compaction(cfg, *, model, model_name="", fixed_overhead_tokens, add_cost, on_event=None, now=None) -> Compaction | None` — NEW kwargs `model_name`, `on_event`, `now` (all optional). Resolves `ctx_window` via `resolve_ctx_window(model_name, cfg.get("ctx_window"))`. The summarize closure forwards `on_event`/`now` (used in Task 3); for THIS task they are accepted and stored/passed but the eval/summarize emissions are added in Task 3.
  - `Compaction.enabled` default stays `True` (already is). `Compaction.params()` gains `"on_event": self.on_event` (Task 3 adds the field; for Task 2 just thread `on_event` into the dataclass).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_compaction.py (append)
from harness.compaction import build_compaction, Compaction

class _FakeModel:
    def query(self, msgs):
        return {"role": "assistant", "content": "S", "extra": {"cost": 0.0}}

def test_build_compaction_resolves_ctx_window_from_model_name():
    comp = build_compaction(
        {"enabled": True},                      # no ctx_window override
        model=_FakeModel(), model_name="openai/claude-opus-4-8",
        fixed_overhead_tokens=0, add_cost=lambda c: None)
    assert comp is not None
    assert comp.ctx_window == 1_000_000          # from table, prefix stripped

def test_build_compaction_config_ctx_window_overrides_table():
    comp = build_compaction(
        {"enabled": True, "ctx_window": 7777},
        model=_FakeModel(), model_name="claude-opus-4-8",
        fixed_overhead_tokens=0, add_cost=lambda c: None)
    assert comp.ctx_window == 7777

def test_build_compaction_disabled_returns_none():
    assert build_compaction({"enabled": False}, model=_FakeModel(),
                            model_name="gpt-5.4", fixed_overhead_tokens=0,
                            add_cost=lambda c: None) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_compaction.py -k 'build_compaction_resolves or config_ctx_window or disabled_returns_none' -v`
Expected: FAIL — `build_compaction()` got an unexpected keyword `model_name` (or ctx_window assertion fails).

- [ ] **Step 3: Write minimal implementation**

In `harness/compaction.py`, update `build_compaction`'s signature and ctx_window line. Change the signature to:

```python
def build_compaction(cfg, *, model, model_name: str = "", fixed_overhead_tokens: int,
                     add_cost, on_event=None, now=None):
```

Replace the existing ctx_window resolution line:

```python
    # OLD: ctx_window: int = int(cfg.get("ctx_window", DEFAULT_CONTEXT_WINDOW))
    ctx_window: int = resolve_ctx_window(model_name, cfg.get("ctx_window"))
```

Pass `on_event` through to the returned `Compaction(...)` (add `on_event=on_event` to the constructor call). The `Compaction` dataclass gains a field `on_event=None` and `now` is captured by the summarize closure (Task 3 uses both); for Task 2, add the dataclass field and the closure capture so the kwargs are accepted:

```python
@dataclass
class Compaction:
    summarize: Callable[[list[dict]], str]
    count_tokens: Callable[[str], int]
    fixed_overhead_tokens: int
    ctx_window: int
    threshold: float = 0.5
    target_ratio: float = 0.2
    protect_head_n: int = 0
    protect_last_n: int = 20
    enabled: bool = True
    on_event: "Callable[[str, dict], None] | None" = None

    def params(self) -> dict:
        return {
            "summarize": self.summarize,
            "count_tokens": self.count_tokens,
            "fixed_overhead_tokens": self.fixed_overhead_tokens,
            "ctx_window": self.ctx_window,
            "threshold": self.threshold,
            "target_ratio": self.target_ratio,
            "protect_head_n": self.protect_head_n,
            "protect_last_n": self.protect_last_n,
            "on_event": self.on_event,
        }
```

And in `build_compaction`, return with `on_event=on_event`:

```python
    return Compaction(
        summarize=summarize,
        count_tokens=estimate_tokens,
        fixed_overhead_tokens=fixed_overhead_tokens,
        ctx_window=ctx_window,
        threshold=threshold,
        target_ratio=target_ratio,
        protect_head_n=protect_head_n,
        protect_last_n=protect_last_n,
        enabled=True,
        on_event=on_event,
    )
```

(The `summarize` closure already exists; Task 3 adds the `on_event`/`now` emission inside it. For Task 2 it is enough that the closure closes over `on_event` and `now` even if it doesn't emit yet — leave the closure body as merged.)

NOTE: `compress()` must accept `on_event` for `params()` to be valid — Task 3 adds it. To keep Task 2 self-contained and green, add `on_event=None` to `compress()`'s signature now (a no-op param) so `params()` round-trips. (Task 3 wires the emission.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_compaction.py -v`
Expected: PASS (Task 1 + Task 2 tests + all merged tests). If a merged test calls `compress(**comp.params())`, the new `on_event=None` kwarg must be accepted by `compress` — confirm the no-op param was added.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 873 + new, 0 failures.

- [ ] **Step 6: Commit**

```bash
git add harness/compaction.py tests/test_compaction.py
git commit -m "feat(compaction): build_compaction resolves ctx_window by model name"
```

---

### Task 3: Emit context.compaction.eval and .summarize

**Files:**
- Modify: `harness/compaction.py` (`compress`, summarize closure)
- Test: `tests/test_compaction.py`

**Interfaces:**
- Consumes: `compress`, `build_compaction` (Tasks 1-2).
- Produces:
  - `compress(..., on_event=None)` emits `on_event("context.compaction.eval", {...})` exactly once per call (when `on_event` is set), carrying `prior_tokens`, `budget`, `ctx_window`, `fixed_overhead`, `decision` (the final method: "none"|"summary"|"truncated").
  - The summarize closure emits `on_event("context.compaction.summarize", {...})` with `in_tokens`, `out_tokens`, `cost`, `elapsed_s` (elapsed only when `now` is provided; else 0.0).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_compaction.py (append)
from harness.compaction import compress

def _msgs(n, role="user", text="x"):
    return [{"role": role, "content": f"{text}{i}"} for i in range(n)]

TOK = lambda s: len(s) * 50

def test_compress_emits_eval_none_when_below_budget():
    events = []
    compress(_msgs(3), summarize=lambda m: "S", count_tokens=TOK,
             fixed_overhead_tokens=0, ctx_window=10_000_000,
             on_event=lambda name, data: events.append((name, data)))
    evals = [d for n, d in events if n == "context.compaction.eval"]
    assert len(evals) == 1
    assert evals[0]["decision"] == "none"
    assert "prior_tokens" in evals[0] and "budget" in evals[0]

def test_compress_emits_eval_summary_when_fired():
    events = []
    compress(_msgs(40), summarize=lambda m: "SUMMARY", count_tokens=TOK,
             fixed_overhead_tokens=0, ctx_window=200,
             protect_head_n=2, protect_last_n=5,
             on_event=lambda name, data: events.append((name, data)))
    evals = [d for n, d in events if n == "context.compaction.eval"]
    assert evals and evals[0]["decision"] == "summary"

def test_compress_no_event_callback_is_silent_and_unchanged():
    # default on_event=None -> no crash, normal result (merged behavior intact)
    r = compress(_msgs(3), summarize=lambda m: "S", count_tokens=TOK,
                 fixed_overhead_tokens=0, ctx_window=10_000_000)
    assert r.method == "none"

def test_summarize_closure_emits_summarize_event():
    events = []
    class M:
        def query(self, msgs):
            return {"role": "assistant", "content": "the summary text",
                    "extra": {"cost": 0.002}}
    comp = build_compaction(
        {"enabled": True, "ctx_window": 200},
        model=M(), model_name="gpt-5.4", fixed_overhead_tokens=0,
        add_cost=lambda c: None,
        on_event=lambda name, data: events.append((name, data)))
    comp.summarize([{"role": "user", "content": "hello world"}])
    summ = [d for n, d in events if n == "context.compaction.summarize"]
    assert len(summ) == 1
    assert summ[0]["out_tokens"] >= 1 and summ[0]["in_tokens"] >= 1
    assert summ[0]["cost"] == 0.002
    assert "elapsed_s" in summ[0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_compaction.py -k 'emits_eval or summarize_closure_emits' -v`
Expected: FAIL — no eval/summarize events recorded.

- [ ] **Step 3: Write minimal implementation**

In `harness/compaction.py` `compress()`: add `on_event=None` to the signature (if Task 2 already added it as a no-op, replace the no-op with real emission). Compute the final `method` before emitting so the eval carries the true decision. Restructure so there is exactly ONE eval emission at the end covering all return paths:

```python
def compress(prior, *, summarize, count_tokens, fixed_overhead_tokens,
             ctx_window, threshold=0.5, target_ratio=0.2,
             protect_head_n=0, protect_last_n=20, on_event=None):
    prior = prior or []
    before_msgs = len(prior)
    before_tokens = count_tokens(render(prior))
    budget = int(threshold * ctx_window) - fixed_overhead_tokens

    def _emit_eval(decision):
        if on_event:
            on_event("context.compaction.eval", {
                "prior_tokens": before_tokens, "budget": budget,
                "ctx_window": ctx_window, "fixed_overhead": fixed_overhead_tokens,
                "decision": decision,
            })

    def noop(method="none"):
        _emit_eval(method)
        return CompactResult(prior, False, method, before_tokens, before_tokens,
                             before_msgs, before_msgs)

    if budget <= 0:
        log.warning("compaction: fixed overhead (%d) >= budget; cannot compact",
                    fixed_overhead_tokens)
        return noop()
    budget = max(budget, MIN_BUDGET_FLOOR)

    if before_tokens <= budget:
        return noop()

    head, middle, tail = _split(prior, count_tokens=count_tokens, budget=budget,
                                protect_head_n=protect_head_n,
                                protect_last_n=protect_last_n, target_ratio=target_ratio)
    if not middle:
        return noop()

    try:
        text = summarize(middle)
        summary = {"role": "user",
                   "content": "[Earlier conversation summarized to save context]\n" + text}
        new = head + [summary] + tail
        method = "summary"
    except Exception:                       # noqa: BLE001 — never crash the turn
        log.warning("compaction: summarize failed; falling back to truncation",
                    exc_info=True)
        new = head + tail
        method = "truncated"

    new = _sanitize_tool_pairs(new)
    _emit_eval(method)
    return CompactResult(new, True, method, before_tokens,
                         count_tokens(render(new)), before_msgs, len(new))
```

NOTE: `budget` is recomputed/clamped above; the eval reports the **pre-clamp** budget (the raw `int(threshold*ctx_window) - fixed_overhead`) so the number matches the trigger math the operator reasons about. Keep `budget` captured for `_emit_eval` BEFORE the `max(budget, MIN_BUDGET_FLOOR)` clamp — i.e. compute a local `raw_budget = int(threshold*ctx_window) - fixed_overhead_tokens` for the event, and use the clamped `budget` for `_split`. (Simplest: have `_emit_eval` close over the raw value computed at the top, as shown.)

In the `summarize` closure inside `build_compaction`, add timing + emission:

```python
    def summarize(middle: list[dict]) -> str:
        user_content = render(middle)
        start = now() if now else None
        msg = model.query([
            {"role": "system", "content": COMPRESS_SYSTEM},
            {"role": "user", "content": user_content},
        ])
        cost = msg.get("extra", {}).get("cost", 0.0)
        add_cost(cost)
        text = msg.get("content") or ""
        if on_event:
            on_event("context.compaction.summarize", {
                "in_tokens": estimate_tokens(user_content),
                "out_tokens": estimate_tokens(text),
                "cost": cost,
                "elapsed_s": round((now() - start), 3) if (now and start is not None) else 0.0,
            })
        return text
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_compaction.py -v`
Expected: PASS (all). Confirm `test_compress_no_event_callback_is_silent_and_unchanged` and every merged test still pass (default `on_event=None` is a pure no-op).

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 873 + new, 0 failures.

- [ ] **Step 6: Commit**

```bash
git add harness/compaction.py tests/test_compaction.py
git commit -m "feat(compaction): emit context.compaction.eval + .summarize via on_event"
```

---

### Task 4: Default-ON + wire model_name/on_event in tracing_agent

**Files:**
- Modify: `harness/tracing_agent.py:101-137`
- Test: `tests/test_compaction_integration.py`

**Interfaces:**
- Consumes: `build_compaction(model_name=, on_event=, now=)` (Tasks 2-3), `compress(on_event=)` (Task 3).
- Produces: default-ON behavior — a `TracingAgent` built with NO `compaction` kwarg compacts a long prior; explicit `enabled: false` does not.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_compaction_integration.py (append)
from minisweagent.environments.local import LocalEnvironment
from harness.models_mock import build_mock_model
from harness.tracing_agent import TracingAgent

def _events_collector():
    seen = []
    class E:
        def set_clock(self, *_): pass
        def emit(self, name, **data): seen.append((name, data))
    return E(), seen

def _agent_cfg():
    return {"system_template": "You are a helpful agent.",
            "instance_template": "Task: {{task}}",
            "step_limit": 10, "cost_limit": 5.0}

def test_compaction_default_on_fires_without_config(tmp_path):
    # NO compaction kwarg -> ON by default
    emitter, seen = _events_collector()
    model = build_mock_model()
    agent = TracingAgent(model, LocalEnvironment(cwd=str(tmp_path)),
                         emitter=emitter, registry=None, **_agent_cfg())
    prior = [{"role": "user", "content": f"turn-{i} " * 200} for i in range(40)]
    agent.run("solve the bug", prior=prior)
    names = [n for n, _ in seen]
    assert "context.compacted" in names          # fired with no config
    assert "context.compaction.eval" in names    # eval event present

def test_compaction_explicit_disable_is_off(tmp_path):
    emitter, seen = _events_collector()
    agent = TracingAgent(build_mock_model(), LocalEnvironment(cwd=str(tmp_path)),
                         emitter=emitter, registry=None,
                         compaction={"enabled": False}, **_agent_cfg())
    agent.run("solve the bug",
              prior=[{"role": "user", "content": "x " * 5000} for _ in range(40)])
    names = [n for n, _ in seen]
    assert "context.compacted" not in names      # opt-out works
    assert "context.compaction.eval" not in names
```

NOTE on the default-ON test firing: with `build_mock_model` the model name is the mock's; `resolve_ctx_window` will fall to the floor (32000) for an unknown mock name, so a 40×~200-word prior must exceed `0.5*32000=16000` est tokens. `estimate_tokens=len//4`; 40 messages × ~200 words × ~6 chars ≈ 48000 chars ≈ 12000 est tokens — may NOT exceed 16000. **Make the prior large enough:** use `"turn-{i} " * 200` × 40 is ~ borderline; bump to `* 400` or 60 messages so est tokens clearly exceed the floor budget. Verify by running; if eval shows `decision: none`, enlarge the prior. (The integration test asserts firing, so size the fixture to actually fire against the 32000 floor.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_compaction_integration.py -k 'default_on or explicit_disable' -v`
Expected: FAIL — `context.compacted` absent (default is still OFF; adapter not built when cfg is None).

- [ ] **Step 3: Write minimal implementation**

In `harness/tracing_agent.py`, replace the adapter-build block (currently lines ~101-113) so it builds when the cfg is absent OR present-and-not-explicitly-disabled, and passes `model_name` + `on_event`:

```python
            # Default-ON: build the adapter unless compaction is explicitly disabled.
            # Absent config -> {} -> all defaults with enabled=True (ON).
            # Explicit {"enabled": False} -> opt out.
            self._compaction = None
            cfg = self._compaction_cfg if self._compaction_cfg is not None else {}
            if cfg.get("enabled", True):
                rendered_system = self._render_template(self.config.system_template)
                rendered_instance = self._render_template(self.config.instance_template)
                fixed_overhead_tokens = _compaction.estimate_tokens(
                    rendered_system + rendered_instance
                )
                self._compaction = _compaction.build_compaction(
                    {**cfg, "enabled": True},   # normalize: build only when enabled
                    model=self.model,
                    model_name=getattr(self.model.config, "model_name", ""),
                    fixed_overhead_tokens=fixed_overhead_tokens,
                    add_cost=lambda c: setattr(self, "cost", self.cost + c),
                    on_event=self._emitter.emit,
                    now=time.monotonic,
                )
```

And thread `on_event` into the compress call (the `params()` dict already carries it from Task 3, so the existing `compress(prior, **self._compaction.params())` at line ~127 needs no change — confirm `params()` includes `on_event`). The `context.compacted` emit at ~130 is unchanged.

NOTE: `time` is already imported in `tracing_agent.py` (used for `self._run_start`). Use `time.monotonic` as the `now` callable. If `time` is not imported, add `import time` at the top.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_compaction_integration.py -v`
Expected: PASS (default-on fires; explicit-disable is silent). If default-on shows `decision: none`, enlarge the test prior per the Step 1 note until it fires against the 32000 floor.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 873 + new, 0 failures. The merged `test_compaction_default_off_no_event` (`tests/test_compaction_integration.py:153`, docstring line ~6) asserts OFF-by-default with NO compaction kwarg — this PR intentionally inverts that. **Update that test:** change it to pass explicit `compaction={"enabled": False}` (and rename to e.g. `test_compaction_explicit_disable_no_event` to match the new meaning, OR delete it since Task 4's `test_compaction_explicit_disable_is_off` already covers the opt-out case — deleting the now-redundant merged test is cleaner). Also update its docstring (line ~6) which says "default off".

- [ ] **Step 6: Commit**

```bash
git add harness/tracing_agent.py tests/test_compaction_integration.py
git commit -m "feat(compaction): ON by default + wire model_name/on_event (#105)"
```

---

### Task 5: Config-surface docs (README / done.conf)

**Files:**
- Modify: `README.md` (or the docs file documenting config — locate via grep) and any sample `done.conf` if present.
- Test: none (docs). Verification is a grep + visual read.

**Interfaces:**
- Consumes: the final config shape. Produces: discoverable documentation of the `compaction:` block, now that it's ON by default.

- [ ] **Step 1: Locate the config-docs surface**

Run: `grep -rn 'compaction\|done.conf\|VIBEPROXY_MODEL\|\[agents' README.md docs/ *.conf 2>/dev/null | head`
Identify where config keys are documented (the README config/flags section or a sample conf). If no config section documents per-feature blocks, add a short subsection to README.

- [ ] **Step 2: Write the docs**

Add a `compaction` subsection documenting: it is **ON by default**; the keys and defaults (`enabled: true`, `threshold: 0.5`, `target_ratio: 0.2`, `protect_head_n: 0`, `protect_last_n: 20`, `ctx_window: <auto from model, override here>`); how to **opt out** (`enabled: false`); that `ctx_window` auto-resolves from the model and only needs setting for unknown models; and that `--debug` surfaces `context.compaction.eval` / `.summarize` / `context.compacted` in the JSONL trace. Keep it to the style/length of the surrounding config docs.

- [ ] **Step 3: Verify**

Run: `grep -n 'compaction' README.md`
Expected: the new subsection present. Read it for accuracy against the implemented defaults (cross-check key names + defaults against `harness/compaction.py`).

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs(compaction): document the compaction config block (ON by default)"
```

---

## Self-Review

**Spec coverage**
- Default ON (config absent → ON; explicit false → off) → Task 4. ✅
- ctx_window: override → table → litellm → floor; openai/ prefix strip → Task 1; used by build_compaction → Task 2. ✅
- CONTEXT_WINDOWS authoritative values (gpt-5.4 400k, opus 1M, haiku 200k, …) → Task 1. ✅
- litellm imported lazily, not at module top → Task 1 (`_get_max_tokens`). ✅
- `context.compaction.eval` (every turn, with decision) → Task 3. ✅
- `context.compaction.summarize` (in/out/cost/elapsed) → Task 3. ✅
- optional `on_event` default None; merged tests unaffected → Task 2 (no-op param) + Task 3 (`test_compress_no_event_callback_is_silent_and_unchanged`). ✅
- `--debug` relay forwards new events with no acp_agent change → verified in spec (acp_agent.py:722 forwards all); no task needed. ✅
- Config-surface docs (folded M2 follow-up) → Task 5. ✅
- model_name from `self.model.config.model_name` → Task 4. ✅
- never-crash / degenerate-budget / per-turn rebuild preserved → unchanged merged code, carried in Task 3's compress restructure (same control flow) + Task 4 (rebuild block keeps per-turn semantics). ✅

**Placeholder scan:** Task 5 Step 1 locates the docs surface at execution time (the only file whose exact path is unknown — README config section vs a conf sample); every code task (1-4) has complete code. The integration-test fixture-sizing note (Task 4) gives a concrete "enlarge until it fires against the 32000 floor" instruction, not a vague placeholder. No "TBD"/"add error handling".

**Type consistency:** `resolve_ctx_window(model_name, cfg_override)`, `build_compaction(..., model_name, on_event, now)`, `compress(..., on_event)`, `Compaction.on_event` field + `params()` key, and the two event names (`context.compaction.eval`, `context.compaction.summarize`) are spelled identically across Tasks 1-4. `_get_max_tokens` is the monkeypatch seam used by Task 1 tests and called by `resolve_ctx_window`.

**Carry-forward risk flagged for the implementer:** Task 4 Step 5 — the merged off-by-default test must be inverted (grep `default_off`). And the default-ON integration fixture must be sized to exceed the 32000-floor budget (mock model name is unknown → floor applies).
