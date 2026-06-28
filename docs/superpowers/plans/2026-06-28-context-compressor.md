# Context Compressor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bound Done's cross-turn transcript by summarizing the middle of the conversation (protecting head + tail) before it is sent to the model, closing audit issue #105.

**Architecture:** A pure, provider-agnostic module `harness/compaction.py` exposes `compress(prior, *, summarize, count_tokens, ...)` taking injected callables — it imports nothing model-related. A thin `Compaction` adapter built inside `TracingAgent.__init__` wires the agent's existing `self.model` and the `compaction:` config into those callables. The agent calls `compress` at the one transcript chokepoint in `run()` (`tracing_agent.py:99`), default-OFF.

**Tech Stack:** Python 3.11+, dataclasses, pytest. No new runtime dependencies (token estimate is `len//4`; no litellm/tiktoken required for v1).

## Global Constraints

- **Provider-agnostic:** `harness/compaction.py` MUST NOT import `vibeproxy`, `litellm`, or any model module. All model access is via injected callables. (Future OpenRouter swap = zero compressor edits.)
- **Caching is out of scope** — dead on this path (`vibeproxy.py:37` hard-prefixes `openai/`; `cache_control` is `/v1/messages`-only).
- **Default OFF in v1** — `compaction.enabled` defaults `false`; the no-op path MUST be byte-identical to today's behavior. #105 bound stays open until a follow-up flips the default.
- **Never crash a turn** — if `summarize` raises, fall back to truncation (`head+tail`).
- **No real-LLM tests** — pure module tested with fake callables; integration tested with the mock model (`harness/models_mock.py`). Avoids `auth_unavailable`.
- **Tool-pair matching key = `tool_call_id` ↔ `tool_calls[].id`** (stable string id, NOT index). Verified: `upstream/.../models/utils/actions_toolcall.py:74,104-106`, `harness/models_mock.py:41-46`.
- **Cost honesty:** the summarize call's cost (`msg["extra"]["cost"]`) IS added to `self.cost`; it is NOT counted against `step_limit` (it does not go through `TracingAgent.query()`).
- **Test command (from worktree root):** `.venv/bin/python -m pytest tests/ -q`

### Codex-review corrections (verified against live code — applied below)

These four bugs were found reviewing the first draft and are already corrected in the tasks. Listed here so the implementer understands *why* the code looks the way it does:

- **C1 (config delivery):** upstream `AgentConfig` is a **Pydantic `BaseModel` with `extra="ignore"` (the effective default)** — a `compaction` key passed via `**cfg` is **silently dropped**, so `self.config.compaction` never exists and the feature is unreachable. FIX (Task 4): `kwargs.pop("compaction", None)` at the **top** of `TracingAgent.__init__`, before `super().__init__(...)`, stored as `self._compaction_cfg`.
- **C2 (overhead at __init__ crashes):** `_render_template(instance_template)` cannot run in `__init__` — `mini.yaml:5` is `Please solve this issue: {{task}}` and `task` is only in `extra_template_vars` after `run()` sets it (upstream `default.py:90`); `StrictUndefined` → `UndefinedError`. FIX (Task 4/5): compute `fixed_overhead_tokens` **lazily inside `run()`** (after `task` is set), and build the `Compaction` adapter there too. `__init__` only stores `self._compaction_cfg`.
- **C3 (test budget floor):** `MIN_BUDGET_FLOOR=1000` dominates the tiny `ctx_window` test values, so `before_tokens <= budget` is always true → compaction never fires → "above budget" tests FALSELY PASS as no-ops. FIX (Tasks 1-2 tests): use a `count_tokens` multiplier large enough that the transcript clears the 1000-token floor (e.g. `TOK = lambda s: len(s) * 50`), and assert `compressed is True` so a silent no-op can't masquerade as success.
- **C4 (integration loop never terminates):** a hand-rolled model returning a plain assistant message with no actions never appends an `exit` message, so `run()`'s loop (`tracing_agent.py:131-132`) spins forever. FIX (Task 5): use the **real mock model** `harness/models_mock.py::build_mock_model()` (ends in a submit sentinel), not a fake.

## File Structure

- Create: `harness/compaction.py` — pure compaction logic + `CompactResult` + `Compaction` adapter dataclass.
- Create: `tests/test_compaction.py` — pure unit tests (fakes, no LLM).
- Create: `tests/test_compaction_integration.py` — one mock-model integration test.
- Modify: `harness/tracing_agent.py` — build the adapter in `__init__`; call `compress` at the seed line in `run()`; emit `context.compacted`.

---

### Task 1: Pure compaction core — split, summarize, fallback

**Files:**
- Create: `harness/compaction.py`
- Test: `tests/test_compaction.py`

**Interfaces:**
- Consumes: nothing (leaf module).
- Produces:
  - `@dataclass CompactResult` with fields: `messages: list[dict]`, `compressed: bool`, `method: str` (`"none"|"summary"|"truncated"`), `before_tokens: int`, `after_tokens: int`, `before_msgs: int`, `after_msgs: int`.
  - `def render(messages: list[dict]) -> str` — concatenates `role` + `content` of each message (content coerced to `str`), newline-joined.
  - `def compress(prior: list[dict], *, summarize: Callable[[list[dict]], str], count_tokens: Callable[[str], int], fixed_overhead_tokens: int, ctx_window: int, threshold: float = 0.5, target_ratio: float = 0.2, protect_head_n: int = 0, protect_last_n: int = 20) -> CompactResult`
  - Module constant `MIN_BUDGET_FLOOR: int = 1000`.

- [ ] **Step 1: Write the failing tests (core behavior, no tool pairs yet)**

```python
# tests/test_compaction.py
from harness.compaction import compress, render, CompactResult

def _msgs(n, role="user", text="x"):
    return [{"role": role, "content": f"{text}{i}"} for i in range(n)]

# count_tokens: chars * 50 so small test transcripts clear MIN_BUDGET_FLOOR=1000
# (C3 fix — with a plain len() the 1000-token floor dominates tiny ctx_window
# values and compaction would never fire, making "above budget" tests false no-ops).
TOK = lambda s: len(s) * 50

def test_below_budget_is_noop_and_never_summarizes():
    spy = {"called": False}
    def summ(_): spy["called"] = True; return "S"
    prior = _msgs(3)
    r = compress(prior, summarize=summ, count_tokens=TOK,
                 fixed_overhead_tokens=0, ctx_window=10_000_000)  # huge window -> below budget
    assert r.compressed is False
    assert r.method == "none"
    assert r.messages == prior          # same content
    assert spy["called"] is False       # hot path: no LLM

def test_above_budget_summarizes_middle_keeps_head_tail():
    prior = _msgs(40)                    # 40 small msgs; TOK*50 -> well over 1000
    r = compress(prior, summarize=lambda m: "SUMMARY", count_tokens=TOK,
                 fixed_overhead_tokens=0, ctx_window=200,  # int(0.5*200)=100 < floor -> budget=1000
                 protect_head_n=2, protect_last_n=5, target_ratio=0.2)
    assert r.compressed is True          # C3: assert it actually fired, not a no-op
    assert r.method == "summary"
    assert r.messages[:2] == prior[:2]                 # head verbatim
    assert r.messages[-5:] == prior[-5:]               # tail verbatim
    mids = [m for m in r.messages if "SUMMARY" in str(m.get("content"))]
    assert len(mids) == 1                               # exactly one summary msg
    assert mids[0]["role"] == "user"
    assert r.after_msgs < r.before_msgs

def test_empty_middle_is_noop():
    # 8 msgs, protect_last_n=10 -> tail consumes all -> middle empty. Must FIRE the
    # trigger first (clear the 1000 floor) so we exercise the empty-middle path,
    # not just the below-budget early return.
    prior = _msgs(8)
    r = compress(prior, summarize=lambda m: "S", count_tokens=TOK,
                 fixed_overhead_tokens=0, ctx_window=2,    # budget floored to 1000; 8*~150 TOK > 1000 -> fires
                 protect_head_n=0, protect_last_n=10)      # tail floor >= len -> no middle
    assert r.method == "none"
    assert r.messages == prior

def test_summarizer_failure_falls_back_to_truncation():
    prior = _msgs(40)
    def boom(_): raise RuntimeError("provider down")
    r = compress(prior, summarize=boom, count_tokens=TOK,
                 fixed_overhead_tokens=0, ctx_window=200,
                 protect_head_n=2, protect_last_n=5)
    assert r.method == "truncated"
    assert r.messages == prior[:2] + prior[-5:]        # head + tail, no crash

def test_degenerate_budget_is_noop():
    prior = _msgs(40)
    # overhead (subtracted) exceeds int(threshold*ctx_window) -> budget<=0 -> noop
    r = compress(prior, summarize=lambda m: "S", count_tokens=TOK,
                 fixed_overhead_tokens=10_000, ctx_window=200)  # int(0.5*200)=100 - 10000 < 0
    assert r.method == "none"
    assert r.messages == prior
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_compaction.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'harness.compaction'`

- [ ] **Step 3: Write minimal implementation**

```python
# harness/compaction.py
"""Pure, provider-agnostic context compaction.

Bounds the cross-turn transcript before it is sent to the model: protects a head
and a recent tail, summarizes the middle via an INJECTED callable, and repairs
tool-call/tool-result pairs orphaned by the cut. Imports nothing model-related —
all model/provider access arrives as callables (see harness.tracing_agent for
the adapter that wires them). See docs/superpowers/specs/2026-06-28-context-
compressor-design.md.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger(__name__)

MIN_BUDGET_FLOOR = 1000  # tokens; keeps the tail target from going negative


@dataclass
class CompactResult:
    messages: list[dict]
    compressed: bool
    method: str            # "none" | "summary" | "truncated"
    before_tokens: int
    after_tokens: int
    before_msgs: int
    after_msgs: int


def render(messages: list[dict]) -> str:
    return "\n".join(f"{m.get('role','')}: {m.get('content','')}" for m in messages)


def _split(prior, *, count_tokens, budget, protect_head_n, protect_last_n, target_ratio):
    head = prior[:protect_head_n]
    rest = prior[protect_head_n:]
    tail: list[dict] = []
    tail_target = max(int(target_ratio * budget), 0)
    for m in reversed(rest):
        if len(tail) >= protect_last_n and count_tokens(render(tail)) >= tail_target:
            break
        tail.insert(0, m)
    middle = rest[: len(rest) - len(tail)]
    return head, middle, tail


def compress(prior, *, summarize: Callable[[list[dict]], str],
             count_tokens: Callable[[str], int], fixed_overhead_tokens: int,
             ctx_window: int, threshold: float = 0.5, target_ratio: float = 0.2,
             protect_head_n: int = 0, protect_last_n: int = 20) -> CompactResult:
    prior = prior or []
    before_msgs = len(prior)
    before_tokens = count_tokens(render(prior))

    def noop(method="none"):
        return CompactResult(prior, False, method, before_tokens, before_tokens,
                             before_msgs, before_msgs)

    budget = int(threshold * ctx_window) - fixed_overhead_tokens
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
    return CompactResult(new, True, method, before_tokens,
                         count_tokens(render(new)), before_msgs, len(new))


def _sanitize_tool_pairs(messages: list[dict]) -> list[dict]:
    return messages  # implemented in Task 2
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_compaction.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add harness/compaction.py tests/test_compaction.py
git commit -m "feat(compaction): pure split/summarize/fallback core (#105)"
```

---

### Task 2: Tool-pair sanitization

**Files:**
- Modify: `harness/compaction.py` (`_sanitize_tool_pairs`)
- Test: `tests/test_compaction.py`

**Interfaces:**
- Consumes: `CompactResult`, `compress` from Task 1.
- Produces: `def _sanitize_tool_pairs(messages: list[dict]) -> list[dict]` — drops `role:"tool"` messages whose `tool_call_id` has no surviving assistant `tool_calls[].id`, and injects a stub tool message for any assistant `tool_calls[].id` whose result was dropped (stub placed immediately after that assistant message).

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_compaction.py
from harness.compaction import _sanitize_tool_pairs

def _assistant_with_call(cid, text="ran"):
    return {"role": "assistant", "content": text,
            "tool_calls": [{"id": cid, "type": "function",
                            "function": {"name": "bash", "arguments": "{}"}}]}

def _tool_result(cid, text="out"):
    return {"role": "tool", "tool_call_id": cid, "content": text}

def test_orphan_tool_result_is_dropped():
    # tool result whose assistant call was cut away
    msgs = [{"role": "user", "content": "hi"}, _tool_result("call_X")]
    out = _sanitize_tool_pairs(msgs)
    assert out == [{"role": "user", "content": "hi"}]   # orphan result removed

def test_orphan_tool_call_gets_stub_result():
    msgs = [_assistant_with_call("call_Y")]              # call with no result
    out = _sanitize_tool_pairs(msgs)
    assert len(out) == 2
    assert out[0] == msgs[0]
    assert out[1]["role"] == "tool"
    assert out[1]["tool_call_id"] == "call_Y"
    assert "omitted during context compaction" in out[1]["content"]

def test_well_formed_pair_is_unchanged():
    msgs = [_assistant_with_call("call_Z"), _tool_result("call_Z")]
    assert _sanitize_tool_pairs(msgs) == msgs

def test_compress_sanitizes_after_cut():
    # head keeps an assistant call; its result lives in the middle (cut) ->
    # surviving call must get a stub, and no orphan result remains.
    head = [_assistant_with_call("call_M")]
    middle = [_tool_result("call_M")] + [{"role": "user", "content": f"m{i}"} for i in range(40)]
    tail = [{"role": "user", "content": f"t{i}"} for i in range(5)]
    prior = head + middle + tail
    r = compress(prior, summarize=lambda m: "SUMMARY", count_tokens=lambda s: len(s),
                 fixed_overhead_tokens=0, ctx_window=200,
                 protect_head_n=1, protect_last_n=5)
    # the surviving assistant call gets a stub result right after it
    assert r.messages[0] == head[0]
    assert r.messages[1]["role"] == "tool"
    assert r.messages[1]["tool_call_id"] == "call_M"
    # no tool message references a call that isn't present
    call_ids = {c["id"] for m in r.messages if m.get("role") == "assistant"
                for c in m.get("tool_calls", [])}
    for m in r.messages:
        if m.get("role") == "tool":
            assert m["tool_call_id"] in call_ids
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_compaction.py -k sanitiz -v` and `-k after_cut`
Expected: FAIL — orphan result not dropped / stub not injected (current `_sanitize_tool_pairs` is identity).

- [ ] **Step 3: Write minimal implementation**

```python
# replace the stub _sanitize_tool_pairs in harness/compaction.py
STUB_RESULT = "[result omitted during context compaction]"


def _sanitize_tool_pairs(messages: list[dict]) -> list[dict]:
    present_ids = {c["id"]
                   for m in messages if m.get("role") == "assistant"
                   for c in (m.get("tool_calls") or []) if "id" in c}
    result_ids = {m["tool_call_id"] for m in messages
                  if m.get("role") == "tool" and "tool_call_id" in m}
    out: list[dict] = []
    for m in messages:
        if m.get("role") == "tool" and m.get("tool_call_id") not in present_ids:
            continue  # orphan result -> drop
        out.append(m)
        if m.get("role") == "assistant":
            for c in (m.get("tool_calls") or []):
                cid = c.get("id")
                if cid and cid not in result_ids:
                    out.append({"role": "tool", "tool_call_id": cid,
                                "content": STUB_RESULT})  # orphan call -> stub
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_compaction.py -v`
Expected: PASS (all Task 1 + Task 2 tests)

- [ ] **Step 5: Commit**

```bash
git add harness/compaction.py tests/test_compaction.py
git commit -m "feat(compaction): sanitize orphaned tool pairs by tool_call_id"
```

---

### Task 3: Boundedness on re-compress

**Files:**
- Test: `tests/test_compaction.py`

**Interfaces:**
- Consumes: `compress` (Tasks 1-2). Produces: nothing new (regression guard only).

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_compaction.py
def test_recompress_is_bounded_and_valid_not_equal():
    prior = _msgs(60)
    once = compress(prior, summarize=lambda m: "S", count_tokens=lambda s: len(s),
                    fixed_overhead_tokens=0, ctx_window=200,
                    protect_head_n=2, protect_last_n=5)
    twice = compress(once.messages, summarize=lambda m: "S",
                     count_tokens=lambda s: len(s),
                     fixed_overhead_tokens=0, ctx_window=200,
                     protect_head_n=2, protect_last_n=5)
    # bounded: never grows; valid: exactly one summary marker, no stacking
    assert twice.after_msgs <= once.after_msgs
    markers = [m for m in twice.messages
               if str(m.get("content", "")).startswith("[Earlier conversation summarized")]
    assert len(markers) <= 1
```

- [ ] **Step 2: Run test to verify it passes (or surfaces a bug)**

Run: `.venv/bin/python -m pytest tests/test_compaction.py::test_recompress_is_bounded_and_valid_not_equal -v`
Expected: PASS. If it FAILS (stacked markers), fix by folding a prior summary into the middle naturally — the current design already does, so this is a guard.

- [ ] **Step 3: Commit**

```bash
git add tests/test_compaction.py
git commit -m "test(compaction): guard boundedness on re-compress"
```

---

### Task 4: Compaction adapter + config (built in TracingAgent.__init__)

**Files:**
- Modify: `harness/compaction.py` (add `Compaction` adapter)
- Modify: `harness/tracing_agent.py:36-54` (`__init__`)
- Test: `tests/test_compaction.py`

**Interfaces:**
- Consumes: `compress`, `render` (Tasks 1-2).
- Produces:
  - `@dataclass Compaction` with: `enabled: bool`, `threshold: float`, `target_ratio: float`, `protect_head_n: int`, `protect_last_n: int`, `ctx_window: int`, `summarize: Callable[[list[dict]], str]`, `count_tokens: Callable[[str], int]`, `fixed_overhead_tokens: int`.
  - `Compaction.params() -> dict` — returns the kwargs `compress` needs (everything except `prior`): `summarize`, `count_tokens`, `fixed_overhead_tokens`, `ctx_window`, `threshold`, `target_ratio`, `protect_head_n`, `protect_last_n`.
  - `def estimate_tokens(text: str) -> int` — `max(1, len(text) // 4)` (provider-neutral default).
  - `def build_compaction(cfg: dict, *, model, fixed_overhead_tokens: int, add_cost: Callable[[float], None]) -> Compaction | None` — reads `cfg.get("compaction", {})`; returns `None` if absent or `enabled` is falsy. Builds `summarize` to call `model.query([...])`, read `msg.get("content") or ""`, and call `add_cost(msg.get("extra", {}).get("cost", 0.0))`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_compaction.py
from harness.compaction import build_compaction, estimate_tokens, Compaction

class _FakeModel:
    def __init__(self): self.seen = None
    def query(self, msgs):
        self.seen = msgs
        return {"role": "assistant", "content": "SUMMARY TEXT", "extra": {"cost": 0.002}}

def test_estimate_tokens_is_chars_over_four():
    assert estimate_tokens("a" * 40) == 10
    assert estimate_tokens("") == 1

def test_build_compaction_disabled_returns_none():
    assert build_compaction({}, model=_FakeModel(), fixed_overhead_tokens=0,
                            add_cost=lambda c: None) is None
    assert build_compaction({"compaction": {"enabled": False}}, model=_FakeModel(),
                            fixed_overhead_tokens=0, add_cost=lambda c: None) is None

def test_build_compaction_enabled_summarize_reads_content_and_adds_cost():
    costs = []
    m = _FakeModel()
    comp = build_compaction(
        {"compaction": {"enabled": True, "context_window": 50000}},
        model=m, fixed_overhead_tokens=123, add_cost=costs.append)
    assert isinstance(comp, Compaction)
    assert comp.enabled is True
    assert comp.fixed_overhead_tokens == 123
    text = comp.summarize([{"role": "user", "content": "hello"}])
    assert text == "SUMMARY TEXT"        # reads .content (dict return), not the dict
    assert costs == [0.002]              # cost forwarded to add_cost
    assert m.seen is not None            # model was actually called
    assert "summarize" in comp.params() and "prior" not in comp.params()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_compaction.py -k 'estimate or build_compaction' -v`
Expected: FAIL — `ImportError: cannot import name 'build_compaction'`

- [ ] **Step 3: Write minimal implementation (adapter in compaction.py)**

```python
# add to harness/compaction.py
COMPRESS_SYSTEM = (
    "You compress conversation history. Summarize the following messages into a "
    "concise but information-dense recap that preserves decisions, facts, file "
    "paths, and open tasks. Omit pleasantries. Output only the summary."
)

DEFAULT_CONTEXT_WINDOW = 32000   # conservative; smallest realistic target window


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


@dataclass
class Compaction:
    enabled: bool
    threshold: float
    target_ratio: float
    protect_head_n: int
    protect_last_n: int
    ctx_window: int
    summarize: Callable[[list[dict]], str]
    count_tokens: Callable[[str], int]
    fixed_overhead_tokens: int

    def params(self) -> dict:
        return dict(summarize=self.summarize, count_tokens=self.count_tokens,
                    fixed_overhead_tokens=self.fixed_overhead_tokens,
                    ctx_window=self.ctx_window, threshold=self.threshold,
                    target_ratio=self.target_ratio,
                    protect_head_n=self.protect_head_n,
                    protect_last_n=self.protect_last_n)


def build_compaction(cfg: dict, *, model, fixed_overhead_tokens: int,
                     add_cost: Callable[[float], None]) -> "Compaction | None":
    c = (cfg or {}).get("compaction") or {}
    if not c.get("enabled"):
        return None

    def summarize(middle: list[dict]) -> str:
        msg = model.query([
            {"role": "system", "content": COMPRESS_SYSTEM},
            {"role": "user", "content": render(middle)},
        ])
        add_cost(msg.get("extra", {}).get("cost", 0.0))
        return msg.get("content") or ""

    return Compaction(
        enabled=True,
        threshold=float(c.get("threshold", 0.5)),
        target_ratio=float(c.get("target_ratio", 0.2)),
        protect_head_n=int(c.get("protect_head_n", 0)),
        protect_last_n=int(c.get("protect_last_n", 20)),
        ctx_window=int(c.get("context_window", DEFAULT_CONTEXT_WINDOW)),
        summarize=summarize,
        count_tokens=estimate_tokens,
        fixed_overhead_tokens=fixed_overhead_tokens,
    )
```

- [ ] **Step 4: Store the compaction config in `TracingAgent.__init__` (NOT the adapter)**

C1 + C2 are why this is split: the config must be popped before `super().__init__` (Pydantic `extra="ignore"` drops unknown keys → `self.config.compaction` would never exist), and the adapter CANNOT be built in `__init__` because computing `fixed_overhead` renders the instance template, which references `{{task}}` — unset until `run()`, so `StrictUndefined` raises. So `__init__` only stashes the raw config; the adapter is built lazily in `run()` (Task 5).

In `harness/tracing_agent.py`, add the import at top, and edit `__init__`:

```python
# top of file, with the other harness imports
from harness import compaction as _compaction
```

At the **very top** of `__init__`, before `super().__init__(model, env, **kwargs)`:

```python
    def __init__(self, model, env, *, emitter: Emitter, skill_block: str = "",
                 persona_block: str = "", memory_block: str = "",
                 base_block: str = "", registry=None,
                 cancel_flag: threading.Event | None = None, **kwargs):
        # Pop the compaction block BEFORE super().__init__ — upstream AgentConfig
        # is Pydantic(extra="ignore") and would silently drop this unknown key,
        # making self.config.compaction unreachable (C1). Adapter is built lazily
        # in run() because fixed_overhead renders {{task}}, unset until then (C2).
        self._compaction_cfg = kwargs.pop("compaction", None)
        self._compaction = None  # built in run() once per turn
        super().__init__(model, env, **kwargs)
        # ... rest of __init__ unchanged ...
```

Default OFF: when no `compaction` block is passed (or `enabled` is falsy), `build_compaction` returns `None` in `run()` → seam skipped → behavior byte-identical to today. Config travels via the `cfg` dict in `acp_agent.py:680` and `runner.py` `_agent_cfg` (both `**`-splatted into the constructor); adding a `compaction` key there now reaches `self._compaction_cfg`.

- [ ] **Step 5: Run tests + full suite to verify no regression**

Run: `.venv/bin/python -m pytest tests/test_compaction.py -v`
Expected: PASS (build_compaction / estimate_tokens tests + all earlier).
Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 847 passed (baseline) — the `__init__` change only pops a kwarg and sets two attrs; inert when no `compaction` config is present.

- [ ] **Step 6: Commit**

```bash
git add harness/compaction.py harness/tracing_agent.py tests/test_compaction.py
git commit -m "feat(compaction): provider-agnostic adapter + stash config in __init__"
```

---

### Task 5: Lazy adapter build + seam call in run() + context.compacted event

**Files:**
- Modify: `harness/tracing_agent.py` `run()` — build the adapter (after `task` is set) and call `compress` at the seed line.
- Test: `tests/test_compaction_integration.py`

**Interfaces:**
- Consumes: `self._compaction_cfg` (Task 4), `compaction.build_compaction`, `compaction.compress`, `compaction.estimate_tokens` (Tasks 1-4).
- Produces: emits `context.compacted` event with `method`, `before_tokens`, `after_tokens`, `before_msgs`, `after_msgs` when compression occurs.

- [ ] **Step 1: Write the failing integration test (REAL mock model — C4)**

```python
# tests/test_compaction_integration.py
"""Integration: compaction fires inside TracingAgent.run() with the REAL mock model.

C4: a hand-rolled model returning a plain assistant message with no actions never
appends an "exit" message, so run()'s loop (tracing_agent.py:131-132) spins
forever. We use harness.models_mock.build_mock_model(), which ends in a submit
sentinel and reproduces the real LitellmModel tool-call shape (models_mock.py:5).
No real LLM -> no auth_unavailable.
"""
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
    # Minimal valid AgentConfig kwargs: system + instance templates are required.
    # instance_template references {{task}} (mini.yaml:5); keep it simple here.
    return {"system_template": "You are a helpful agent.",
            "instance_template": "Task: {{task}}",
            "step_limit": 10, "cost_limit": 5.0}


def test_compaction_fires_in_run_and_emits_event(tmp_path):
    emitter, seen = _events_collector()
    model = build_mock_model()
    env = LocalEnvironment(cwd=str(tmp_path))
    agent = TracingAgent(
        model, env, emitter=emitter, registry=None,
        compaction={"enabled": True, "context_window": 200,
                    "protect_head_n": 1, "protect_last_n": 3, "target_ratio": 0.2},
        **_agent_cfg(),
    )
    # Long prior -> with TOK estimate (len//4 *... ) and ctx_window=200, budget
    # floors to 1000; the prior below renders to >1000 estimated tokens so it fires.
    prior = [{"role": "user", "content": f"turn-{i} " * 60} for i in range(40)]
    agent.run("solve the bug", prior=prior)

    names = [n for n, _ in seen]
    assert "context.compacted" in names                # seam fired + event emitted
    data = dict(seen[names.index("context.compacted")][1])
    assert data["after_msgs"] < data["before_msgs"]
    assert data["method"] in ("summary", "truncated")


def test_compaction_default_off_is_noop(tmp_path):
    # No compaction kwarg -> adapter None -> no event, behavior unchanged.
    emitter, seen = _events_collector()
    agent = TracingAgent(build_mock_model(), LocalEnvironment(cwd=str(tmp_path)),
                         emitter=emitter, registry=None, **_agent_cfg())
    agent.run("solve the bug", prior=[{"role": "user", "content": "x" * 9000}])
    assert "context.compacted" not in [n for n, _ in seen]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_compaction_integration.py -v`
Expected: FAIL — `context.compacted` not in emitted events (adapter not built / seam not wired yet).

- [ ] **Step 3: Build the adapter lazily + wire the seam in `run()`**

In `harness/tracing_agent.py` `run()`, the relevant lines today are:
```python
            self.extra_template_vars |= {"task": task, **kwargs}   # ~:87
            ...
            self.messages = []
            self.add_messages(self.model.format_message(
                role="system", content=self._render_template(self.config.system_template)))
            self.add_messages(*(prior or []))                       # :99 — the seed line
            self.add_messages(self.model.format_message(
                role="user", content=self._render_template(self.config.instance_template)))
```

After `self.extra_template_vars |= {"task": task, ...}` (so `{{task}}` is now
defined), build the adapter once for this turn. Then replace the `:99` seed line
with the compress-and-emit block:

```python
            # Build the compaction adapter lazily (C2: fixed_overhead renders the
            # instance template, which needs {{task}} — only defined just above).
            self._compaction = None
            if self._compaction_cfg:
                overhead_text = (self._render_template(self.config.system_template)
                                 + self._render_template(self.config.instance_template))
                self._compaction = _compaction.build_compaction(
                    {"compaction": self._compaction_cfg},
                    model=self.model,
                    fixed_overhead_tokens=_compaction.estimate_tokens(overhead_text),
                    add_cost=lambda c: setattr(self, "cost", self.cost + c),
                )

            self.messages = []
            self.add_messages(self.model.format_message(
                role="system", content=self._render_template(self.config.system_template)))
            # --- seed line (:99), now with compaction ---
            prior = prior or []
            if self._compaction is not None and self._compaction.enabled:
                result = _compaction.compress(prior, **self._compaction.params())
                prior = result.messages
                if result.compressed:
                    self._emitter.emit(
                        "context.compacted", method=result.method,
                        before_tokens=result.before_tokens,
                        after_tokens=result.after_tokens,
                        before_msgs=result.before_msgs,
                        after_msgs=result.after_msgs)
            self.add_messages(*prior)
            # --- end seed line ---
            self.add_messages(self.model.format_message(
                role="user", content=self._render_template(self.config.instance_template)))
```

This preserves the reimplemented-loop contract (C: the docstring says "change ONLY
the seed line"): the system/instance renders are byte-identical; only `prior`'s
handling changed, plus an adapter build above it that is a pure no-op when
`self._compaction_cfg` is falsy.

- [ ] **Step 4: Run test + full suite to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_compaction_integration.py -v`
Expected: PASS (both: fires-when-enabled, noop-when-default-off).
Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 847+ passed (baseline preserved; new tests added).

- [ ] **Step 5: Commit**

```bash
git add harness/tracing_agent.py tests/test_compaction_integration.py
git commit -m "feat(compaction): lazy adapter + fire compress at run() seed line + event (#105)"
```

---

### Task 6: TUI footer note (minimal) + visual verification

**Files:**
- Modify: the turn-footer renderer in `harness/tui/` (locate the `.turn-meta-run` / Build-line footer added in PR #97/#100; grep `turn-meta-run`).
- Test: extend the existing footer/turn-meta test if one exists; otherwise a Pilot assertion that the note text appears when a `context.compacted` event is observed.

**Interfaces:**
- Consumes: the `context.compacted` event (Task 5).
- Produces: a dim one-line footer note `↯ context compacted {before_msgs}→{after_msgs} msgs` on the turn where compaction fired. No new widget; reuse `.turn-meta` styling.

- [ ] **Step 1: Locate the footer seam**

Run: `grep -rn 'turn-meta-run\|turn-meta\|Build ' harness/tui/`
Identify where the turn-end footer string is composed (the Build line). The compaction note appends to that footer only when a `context.compacted` event was seen for the turn.

- [ ] **Step 2: Write the failing test**

```python
# in the appropriate harness/tui test module (mirror an existing footer test)
def test_footer_shows_compaction_note_when_event_seen(...):
    # drive a turn where the agent emits context.compacted (use the mock path
    # or a synthetic event injected into the reducer that owns the footer),
    # then assert the rendered footer contains "context compacted" and "→".
    ...
    assert "context compacted" in footer_text
    assert "→" in footer_text
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest <that test> -v`
Expected: FAIL — note absent.

- [ ] **Step 4: Implement the footer note**

In the reducer/state that builds the turn footer: when a `context.compacted`
event is recorded for the active turn, append
`f" ↯ context compacted {before_msgs}→{after_msgs} msgs"` (dim, `.turn-meta`
class) to the existing Build footer line. Default-OFF compaction means this note
never appears today — it is inert until `compaction.enabled` is set.

- [ ] **Step 5: Run test + full suite**

Run: `.venv/bin/python -m pytest <that test> -v` → PASS
Run: `.venv/bin/python -m pytest tests/ -q` → 847+ passed.

- [ ] **Step 6: Visual verification**

Enable compaction in a scratch config, run the TUI against a long synthetic
session, `save_screenshot` → render PNG (`qlmanage -t` or open), confirm the
dim footer note renders correctly and does not disrupt the existing Build-line
layout. (Per the TUI-verification habit: green tests are not enough for layout.)

- [ ] **Step 7: Commit**

```bash
git add harness/tui/ tests/
git commit -m "feat(tui): dim footer note when context is compacted"
```

---

## Self-Review

**Spec coverage**
- Pure module / approach A → Task 1. ✅
- Trigger w/ fixed-overhead + degenerate guard → Task 1 (`test_degenerate_budget_is_noop`). ✅
- Head/middle/tail split + tail sizing → Task 1 (`_split`, `test_above_budget...`). ✅
- LLM summary + truncation fallback (#9666) → Task 1 (`test_summarizer_failure...`). ✅
- Tool-pair sanitization by `tool_call_id` → Task 2. ✅
- Boundedness not idempotence → Task 3. ✅
- Provider-agnostic adapter, summarize reads `.content` + adds cost, config block, conservative `context_window` → Task 4. ✅
- No vibeproxy import in compaction.py → enforced by Global Constraints + Task 1 module docstring; no import present. ✅
- Lazy adapter build (C2) + seam call (one loop edit) + `context.compacted` event → Task 5. ✅
- Config delivery via `kwargs.pop` before `super().__init__` (C1) → Task 4 Step 4. ✅
- Codex-review corrections C1–C4 → all applied (see "Codex-review corrections" near the top); verified against live code (`upstream/.../default.py:19,41` Pydantic extra=ignore; `mini.yaml:5` `{{task}}`; loop term `tracing_agent.py:131-132`). ✅
- Cost counted, excluded from `step_limit` → Task 4 (`add_cost` closure mutates `self.cost`; summarize bypasses `TracingAgent.query`). ✅
- Default OFF / no-op byte-identical → Tasks 4-5 (adapter `None` → seam skipped); full-suite-green checks. ✅
- Observability event + minimal TUI footer → Tasks 5-6. ✅
- No-real-LLM tests → fakes (Tasks 1-4) + mock/recording model (Task 5). ✅

**Placeholder scan:** Task 6 Steps 1-2 intentionally locate-then-test the TUI seam (exact file unknown until grep); every code task (1-5) has complete code. No "TBD"/"add error handling"/"similar to Task N". Acceptable: Task 6 is the one UI task whose exact widget must be found at execution time, and the steps say exactly how.

**Type consistency:** `CompactResult` fields, `compress` signature, `Compaction.params()` keys, and `build_compaction` parameters are identical across Tasks 1, 4, 5. `tool_call_id` used consistently. `estimate_tokens` name matches Task 4 import and usage.

**Resolved (was open):** config-delivery path — confirmed via live probe that upstream `AgentConfig` is Pydantic `extra="ignore"`, so the block is popped in `__init__` before `super().__init__` (Task 4 Step 4) rather than ridden in on `self.config`. Both call sites (`acp_agent.py:680`, `runner.py` `_agent_cfg`) `**`-splat the cfg dict, so adding a `compaction` key there reaches `self._compaction_cfg`.

**Remaining open item:** Task 6's exact TUI footer widget file (located by grep at execution time — the only non-pinned task, by design).
