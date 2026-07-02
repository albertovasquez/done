# Cache-Aware History (PR 3 of #139 / #105) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make session history episodic-never-sliding: compaction results persist across turns as a `CompactView` on the session, so between episodes the effective history is byte-stable (cache-warm) and chat history stops being unbounded.

**Architecture:** A pure `history_view` module composes `view.messages + transcript[view.upto:]` and reconciles it through the existing `compaction.compress()`; the ACP chokepoint (`acp_agent.prompt()`) drives reconciliation once per turn and persists episodes on `SessionState.compact_view`. Chat, the tool-probe, and the agent `prior` consume the view; the router keeps the raw (already 8-turn-capped) transcript. Engine-side per-turn compaction stays as a within-turn safety net.

**Corrected premise (supersedes spec §3a's "already episodic" claim):** `prior` is re-derived from the FULL stored transcript every turn (`acp_agent.py`: `transcript = state.transcript`) and compaction results are never persisted — so once over budget, the engine re-summarizes every turn, rewriting the history head every turn (permanently cache-cold). Persisting the episode is the core of this PR. Task 4 records this correction in the spec.

**Tech Stack:** Python 3.11, pytest, existing `harness/compaction.py` machinery (pure `compress()`, `COMPRESS_SYSTEM`, `render`, `estimate_tokens`, `resolve_ctx_window`). No new dependencies.

## Global Constraints

- Work in the dedicated git worktree; NEVER commit on `main` (AGENTS.md #1). Run `pwd` + `git branch --show-current` before every commit.
- Test command from the worktree root: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/ -q`.
- Known pre-existing baseline failures (NOT yours to fix; they fail on main): `tests/test_system_skills.py::test_catalog_is_exactly_the_maturity_spine`, `tests/test_tui_snapshots.py::test_completed_turn_ordering`; `test_pilot_streams_deltas_into_one_markdown_widget` is a known ~1-in-5 flake — retry before suspecting your diff.
- Never modify anything under `upstream/`. Never modify `harness/compaction.py` (it is reused as-is).
- The store's `transcript` stays append-only raw truth — never mutated by this PR.
- Compaction facts you must respect in tests: `compress()` clamps budget to `MIN_BUDGET_FLOOR = 1000` tokens; `estimate_tokens` = chars//4; defaults `threshold=0.5`, `target_ratio=0.2`, `protect_last_n=20`; on summarize exception it degrades to `method="truncated"`; under budget it returns the SAME list untouched (`method="none"`, `compressed=False`).

---

### Task 1: `harness/history_view.py` — the pure episodic view

**Files:**
- Create: `harness/history_view.py`
- Test: `tests/test_history_view.py` (create)

**Interfaces:**
- Produces: `CompactView` dataclass (`upto: int`, `messages: list[dict]`); `effective_history(transcript, view) -> list[dict]`; `reconcile(transcript, view, *, summarize, fixed_overhead_tokens, ctx_window, on_event=None) -> tuple[list[dict], CompactView | None, compaction.CompactResult]`. Task 2 consumes these exact names.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_history_view.py`:

```python
"""Episodic-never-sliding invariant for session history (#105, PR 3)."""
import pytest

from harness.history_view import CompactView, effective_history, reconcile

# Sizing: ctx_window=4000 -> budget = 0.5*4000 = 2000 tokens (>= MIN_BUDGET_FLOOR).
# Each message is 160 chars = 40 tokens. 60 msgs = 2400 tokens > 2000 -> fires.
# Tail protection keeps the last 20 msgs (~800 tokens >= tail target 400), so the
# post-episode view is ~810 tokens << 2000 -> genuinely episodic afterwards.
CTX = 4000
MSG = {"role": "user", "content": "x" * 160, "origin": "chat"}


def _transcript(n):
    return [dict(MSG) for _ in range(n)]


def _counting_summarize():
    calls = {"n": 0}

    def summarize(middle):
        calls["n"] += 1
        return "SUMMARY"
    return summarize, calls


def test_effective_history_is_transcript_when_no_view():
    t = _transcript(3)
    assert effective_history(t, None) == t
    assert effective_history(t, None) is not t          # copy, not alias


def test_effective_history_composes_view_plus_tail():
    t = _transcript(5)
    view = CompactView(upto=3, messages=[{"role": "user", "content": "S"}])
    out = effective_history(t, view)
    assert out == [{"role": "user", "content": "S"}] + t[3:]


def test_under_budget_no_compression_no_summarize_call():
    summarize, calls = _counting_summarize()
    t = _transcript(5)
    history, view, result = reconcile(t, None, summarize=summarize,
                                      fixed_overhead_tokens=0, ctx_window=CTX)
    assert history == t and view is None
    assert result.compressed is False and calls["n"] == 0


def test_episode_fires_once_then_head_is_byte_stable():
    summarize, calls = _counting_summarize()
    t = _transcript(60)                                  # over budget
    history1, view1, r1 = reconcile(t, None, summarize=summarize,
                                    fixed_overhead_tokens=0, ctx_window=CTX)
    assert r1.compressed and r1.method == "summary" and calls["n"] == 1
    assert view1 is not None and view1.upto == 60
    assert history1 == view1.messages

    t.extend(_transcript(5))                             # small tail growth
    history2, view2, r2 = reconcile(t, view1, summarize=summarize,
                                    fixed_overhead_tokens=0, ctx_window=CTX)
    assert r2.compressed is False and calls["n"] == 1    # NO re-summarize
    assert view2 is view1                                # view unchanged
    # THE invariant: the head of the history is byte-stable between episodes.
    assert history2[:len(view1.messages)] == view1.messages
    assert history2[len(view1.messages):] == t[60:]


def test_regrowth_triggers_second_episode_anchored_at_new_length():
    summarize, calls = _counting_summarize()
    t = _transcript(60)
    _, view1, _ = reconcile(t, None, summarize=summarize,
                            fixed_overhead_tokens=0, ctx_window=CTX)
    t.extend(_transcript(40))                            # regrow past budget
    history3, view3, r3 = reconcile(t, view1, summarize=summarize,
                                    fixed_overhead_tokens=0, ctx_window=CTX)
    assert r3.compressed and calls["n"] == 2
    assert view3.upto == 100
    assert history3 == view3.messages


def test_summarize_failure_degrades_to_truncated_and_still_persists():
    def boom(middle):
        raise RuntimeError("no summarizer")
    t = _transcript(60)
    history, view, result = reconcile(t, None, summarize=boom,
                                      fixed_overhead_tokens=0, ctx_window=CTX)
    assert result.compressed and result.method == "truncated"
    assert view is not None and view.upto == 60
    assert history == view.messages
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_history_view.py -q`
Expected: FAIL — `ModuleNotFoundError: harness.history_view`.

- [ ] **Step 3: Implement `harness/history_view.py`**

```python
"""Episodic compacted view of a session transcript (#105, PR 3 of #139).

The store's transcript is append-only raw truth. Re-summarizing FROM THE FULL
TRANSCRIPT every turn (what the engine-side per-turn compaction does once over
budget) rewrites the history head every turn — permanently cache-cold. This
module persists each compaction episode as a CompactView so between episodes
the effective history is byte-stable and append-only:
``view.messages + transcript[view.upto:]``. Pure — no I/O; the summarize LLM
closure is injected by the caller."""

from __future__ import annotations

from dataclasses import dataclass

from harness import compaction as _compaction


@dataclass
class CompactView:
    upto: int               # transcript prefix length this view replaces
    messages: list[dict]    # compacted stand-in for transcript[:upto]


def effective_history(transcript: list[dict], view: CompactView | None) -> list[dict]:
    """The history consumers should send: compacted episodes + live tail."""
    if view is None:
        return list(transcript)
    return list(view.messages) + list(transcript[view.upto:])


def reconcile(transcript: list[dict], view: CompactView | None, *,
              summarize, fixed_overhead_tokens: int, ctx_window: int,
              on_event=None):
    """Return ``(history, view', result)``.

    Episodic-never-sliding: compress() fires only when the effective history
    exceeds the budget; when it fires, the result is PERSISTED as a new view
    anchored at the current transcript length, so the next turn appends to the
    compacted head instead of re-summarizing (one deliberate cache miss per
    episode). Under budget, compress() returns the same list untouched."""
    history = effective_history(transcript, view)
    result = _compaction.compress(
        history,
        summarize=summarize,
        count_tokens=_compaction.estimate_tokens,
        fixed_overhead_tokens=fixed_overhead_tokens,
        ctx_window=ctx_window,
        on_event=on_event,
    )
    if not result.compressed:
        return history, view, result
    new_view = CompactView(upto=len(transcript), messages=list(result.messages))
    return list(result.messages), new_view, result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_history_view.py -q`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add harness/history_view.py tests/test_history_view.py
git commit -m "feat(cache): episodic CompactView — history head byte-stable between compactions (#105)"
```

---

### Task 2: Wire the view into the ACP chokepoint

**Files:**
- Modify: `harness/acp_session.py` (SessionState — one field)
- Modify: `harness/acp_agent.py` (`prompt()` — reconcile once per turn; three consumers switch to the view)
- Test: covered by Task 1 units + Task 3 integration; this task's gate is the full suite.

**Interfaces:**
- Consumes: `history_view.reconcile(...)` from Task 1 (exact signature above).
- Produces: `SessionState.compact_view` (CompactView | None); local `history` list used by the tool-probe, the chat branch, and the agent `prior`; trace event `cache.boundary` with `changed="history"` and `method` on each episode.

- [ ] **Step 1: Add the SessionState field**

In `harness/acp_session.py`, after `prompt_hashes: dict | None = None` (line 33), add in the neighbors' style:

```python
    compact_view: "object | None" = None  # episodic compacted history (history_view.CompactView, #105)
```

- [ ] **Step 2: Reconcile once per turn in `prompt()`**

In `harness/acp_agent.py`, find the cache.boundary block that ends with `state.prompt_hashes = _hashes` (added in PR 2; sits after the `env_block` assembly and before the tool-escalation gate). Directly AFTER that line, insert:

```python
        # #105: episodic history view — compaction episodes persist on the
        # session so between episodes the effective history is byte-stable
        # (cache-warm). The raw transcript stays append-only truth; the router
        # keeps consuming it directly (already tail-capped to 8 turns).
        from harness import compaction as _compaction
        from harness import history_view as _history_view

        def _summarize_history(middle: list[dict]) -> str:
            if model_id is None:
                raise RuntimeError("mock mode: no summarizer model")  # -> truncated
            import litellm  # lazy: keep the ~1s import off startup
            from harness import vibeproxy
            resp = litellm.completion(
                model=vibeproxy.model_id(model_id),
                **vibeproxy.completion_kwargs(),
                messages=[{"role": "system", "content": _compaction.COMPRESS_SYSTEM},
                          {"role": "user", "content": _compaction.render(middle)}],
                max_tokens=2000,
            )
            return resp.choices[0].message.content or ""

        _fixed_overhead = _compaction.estimate_tokens(
            base_block + (state.persona_block or "") + (state.memory_block or "")
            + env_block + text)
        history, _new_view, _hist_result = await loop.run_in_executor(
            None, lambda: _history_view.reconcile(
                transcript, state.compact_view,
                summarize=_summarize_history,
                fixed_overhead_tokens=_fixed_overhead,
                ctx_window=_compaction.resolve_ctx_window(model_id or ""),
            ))
        if _hist_result.compressed:
            await self._trace(session_id, "cache.boundary", sid=session_id,
                              changed="history", method=_hist_result.method)
            state.compact_view = _new_view
```

Notes for the implementer: `loop`, `transcript`, `text`, `model_id`, `base_block`, `env_block`, and `state` are all already in scope at this point (verify each; if any is not, STOP and report). `run_in_executor` matters — `summarize` makes a blocking litellm call and must not run on the event loop.

- [ ] **Step 3: Switch the three consumers to the view**

Still in `harness/acp_agent.py`, AFTER the insertion point (never before it):
1. The wants_tool probe call: `_probe.wants_tool(text, history=transcript, ...)` → `history=history`.
2. The chat branch: `handler.answer_stream(text, history=transcript, ...)` → `history=history`.
3. The agent path: the `self._run_agent_turn(loop, session_id, state, text, ctx.skill_block, transcript, ...)` call passes the prior — change that `transcript` argument to `history`.
Leave `self._router.classify(text, history=transcript)` (earlier in the function) UNTOUCHED.

- [ ] **Step 4: Run the focused tests, then the full suite**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_history_view.py tests/test_acp_session_context.py tests/test_chat_handler.py -q`
Expected: all pass.
Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/ -q`
Expected: only the known baseline failures. If anything else fails and it is not obviously an assertion about WHICH list object reaches a consumer, STOP and report.

- [ ] **Step 5: Commit**

```bash
git add harness/acp_session.py harness/acp_agent.py
git commit -m "feat(cache): sessions consume the episodic history view; cache.boundary(history) per episode (#105)"
```

---

### Task 3: Integration tests — boundary events observed end-to-end

**Files:**
- Test: `tests/test_acp_history_boundary.py` (create)

**Interfaces:**
- Consumes: the `_FakeConn` / `_ScriptedRouter` / `_prompt` driver pattern from `tests/test_acp_session_context.py` (read that file first and reuse its construction helpers — do NOT invent a new driver).

- [ ] **Step 1: Write the tests (they must fail only if the wiring is broken — write them, run them, they should PASS against Tasks 1-2; if one fails, that is a real wiring bug to report, not adapt)**

Create `tests/test_acp_history_boundary.py` with three tests:

```python
"""cache.boundary integration: the alarm is observable end-to-end (#105/#139).

Drives HarnessAgent.prompt() directly (no subprocess) using the same fake-conn
driver as test_acp_session_context.py. Mock mode: the history summarizer
degrades to method="truncated" deterministically (no LLM available)."""
```

1. `test_history_episode_emits_boundary_once`:
   - Build the agent + session exactly like `test_acp_session_context.py` does (scripted router returning `chat_question`).
   - Seed the store past budget: `agent._store.extend(sid, [{"role": "user", "content": "y" * 2000, "origin": "chat"}] * 40)` (40 × 500 tokens = ~20k tokens > the mock budget of 16k = 0.5 × 32000 default ctx).
   - `_prompt(agent, sid, "hello")` → collect the trace payloads captured by the fake conn (`field_meta["harness"]["trace"]` entries) and assert EXACTLY ONE with `type == "cache.boundary"` and `data.changed == "history"` and `data.method == "truncated"`.
   - Assert `agent._store.get(sid).compact_view is not None`.
   - `_prompt(agent, sid, "again")` → assert NO additional `cache.boundary` with `changed == "history"` (episodic, not per-turn).
2. `test_small_session_never_emits_history_boundary`: fresh session, two small prompts, assert zero `cache.boundary` events with `changed == "history"` and `compact_view is None`.
3. `test_env_or_block_change_emits_named_boundary` (the PR-2 follow-up): fresh session, one prompt; then change a hashed block between prompts — the cleanest deterministic lever in this driver is the memory block: set `agent._store.get(sid).memory_block = "CHANGED MEMORY"` after the first prompt; second prompt → assert exactly one `cache.boundary` whose `data.changed` contains `"memory"`. (First prompt must NOT emit one — `changed_blocks` returns `[]` when there is no previous hash.)

Implementation notes: how trace events surface through the fake conn — look at how `_trace` delivers (session_update with `field_meta["harness"]["trace"]`) and how existing tests read captured updates; follow that exactly. If `_trace` output is not captured by the existing `_FakeConn`, extend the local copy of the fake in THIS test file only.

- [ ] **Step 2: Run the tests**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_acp_history_boundary.py -q`
Expected: 3 passed. A failure here is a wiring defect — investigate and report; do not weaken the test.

- [ ] **Step 3: Run the full suite**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/ -q`
Expected: only the known baseline failures.

- [ ] **Step 4: Commit**

```bash
git add tests/test_acp_history_boundary.py
git commit -m "test(cache): boundary events observed end-to-end — history episodes + block changes (#105 #139)"
```

---

### Task 4: Spec truth-up + plan bookkeeping

**Files:**
- Modify: `docs/superpowers/specs/2026-07-02-prompt-cache-prefix-stability-design.md` (§ "PR 3 — cache-aware history", subsection 3a)

- [ ] **Step 1: Amend the spec**

In section "### 3a. The rule: episodic, never sliding", after the paragraph beginning "Worker `prior` already behaves this way", add:

```markdown
**Corrected premise (2026-07-02, during PR 3):** the engine-side compaction is
episodic per *call* but not per *session* — `prior` is re-derived from the full
stored transcript every turn and results were never persisted, so once over
budget it re-summarized every turn (per-turn head churn, permanently
cache-cold). PR 3 therefore drives episodic compaction at the ACP chokepoint
and persists each episode as `SessionState.compact_view`
(`harness/history_view.py`); consumers (chat, tool-probe, agent prior) send
`view.messages + transcript[view.upto:]`. The engine-side per-turn compaction
is retained unchanged as a within-turn safety net — with the view in place it
no-ops in steady state. The raw store transcript remains append-only truth.
```

- [ ] **Step 2: Run the full suite once (docs-only change — sanity)**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_history_view.py tests/test_acp_history_boundary.py -q`
Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-07-02-prompt-cache-prefix-stability-design.md
git commit -m "docs(spec): record the corrected episodic-compaction premise (#105)"
```
