# Output-Filter Seam Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut LLM token use on verbose command output by post-processing bash output inside Done's `env.execute` path with small, Done-owned, fail-open filters.

**Architecture:** A pure-function filter package (`harness/output_filters/`) + a dispatcher `filter_output(command, output, returncode) -> str`. `AcpEnvironment` gains an optional injected `output_filter` member, applied at the single point where all three `execute` branches converge (before `return out`). Default `None` = byte-identical no-op. A measurement trace lands FIRST so filter choice is driven by Done's real (Python) workload, not assumptions.

**Tech Stack:** Python 3.11, pytest. No new dependencies. Reuses existing `config.py` `[harness]` settings API and the existing `on_command` trace hook.

## Global Constraints

- **Fail-open, always:** any filter that raises, returns `None`, returns empty, or returns output LONGER than the input → the dispatcher returns the ORIGINAL output unchanged. A filter must never drop signal (a real test failure, a stack trace). Correctness > savings.
- **No-op parity:** with no filter injected (`output_filter=None`), `AcpEnvironment.execute` returns a byte-identical `out`. This is the regression guard.
- **Zero upstream edits:** do not edit `minisweagent/`. `AcpEnvironment` is a subclass; keep it that way.
- **Pure functions:** each filter is `(command: str, output: str, returncode: int) -> str | None`. No I/O, no global state, no env access. This is the testable unit.
- **Test command (from this worktree root):** `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/ -q`. The venv lives in the PRIMARY checkout; conftest (PR #94) prepends this worktree's src root so editable-install shadowing is handled. Never use a bare `.venv/bin/python` — this worktree has none.
- **Scope = interactive (`AcpEnvironment`) path only.** The headless/cron path builds a plain `LocalEnvironment` (`run_traced.py:168`) with no seam; covering it is a deferred decision (see Task 5 note), not part of v1.

---

### Task 1: Measurement trace (evidence before filters)

Land the savings-measurement hook with an IDENTITY filter, so a few real sessions reveal which command formats actually dominate Done's (Python) workload before any real filter is written. This is the spec's Open Question 1 made concrete.

**Files:**
- Create: `harness/output_filters/__init__.py`
- Create: `harness/output_filters/dispatch.py`
- Modify: `harness/acp_env.py` (constructor `:25-36`; `execute` before `return out` at `:83`)
- Modify: `harness/acp_agent.py` (env construction `:654`; `on_command` at `:571`)
- Test: `tests/test_output_filter_seam.py`

**Interfaces:**
- Produces: `harness.output_filters.dispatch.filter_output(command: str, output: str, returncode: int) -> str` — applies the first matching filter, fail-open; with the default registry (empty in Task 1) it is the identity function.
- Produces: `AcpEnvironment(..., output_filter: Callable[[str, str, int], str] | None = None)` — new keyword-only param; when `None`, `execute` is a byte-identical no-op.
- Consumes (Task 2+): later tasks register filters into `dispatch`'s registry.

- [ ] **Step 1: Write the failing test for the dispatcher identity + the seam no-op**

```python
# tests/test_output_filter_seam.py
from harness.output_filters.dispatch import filter_output


def test_dispatch_identity_when_no_filter_matches():
    # No filters registered (Task 1) → output returned unchanged.
    out = "anything at all\nline 2\n"
    assert filter_output("git status", out, 0) == out


def test_dispatch_is_failopen_on_unknown_command():
    assert filter_output("totally-unknown-cmd --x", "raw", 1) == "raw"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_output_filter_seam.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'harness.output_filters'`

- [ ] **Step 3: Create the package and dispatcher**

```python
# harness/output_filters/__init__.py
"""Done-owned output filters: pure (command, output, returncode) -> str|None
functions that compact verbose command output before it reaches the model.
Fail-open by contract — see dispatch.filter_output."""
```

```python
# harness/output_filters/dispatch.py
"""filter_output: pick the first registered filter whose matcher recognizes the
command, apply it, and FAIL OPEN — any error / None / empty / longer-than-input
result yields the ORIGINAL output unchanged. A filter must never lose signal.

Filters register via FILTERS (a list of (matcher, filter) pairs). Empty in Task 1
(identity); Task 2+ append real filters."""
from __future__ import annotations

from typing import Callable

# matcher: (command) -> bool ;  filt: (command, output, returncode) -> str | None
FILTERS: list[tuple[Callable[[str], bool], Callable[[str, str, int], str | None]]] = []


def filter_output(command: str, output: str, returncode: int) -> str:
    for matcher, filt in FILTERS:
        try:
            if not matcher(command):
                continue
            result = filt(command, output, returncode)
        except Exception:
            return output                      # fail-open: filter bug never loses output
        if not result or len(result) >= len(output):
            return output                      # declined / no shrink → original
        return result
    return output                              # no matcher → identity
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_output_filter_seam.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Write the failing test for the `AcpEnvironment` seam no-op + applied path**

```python
# append to tests/test_output_filter_seam.py
from harness.acp_env import AcpEnvironment


def _env(**kw):
    # on_command is required; a no-op callback suffices for execute() tests.
    return AcpEnvironment(cwd=".", on_command=lambda *a: None, **kw)


def test_seam_noop_when_no_filter():
    env = _env()                                   # output_filter defaults to None
    out = env.execute({"command": "printf 'hello\\nworld\\n'"})
    assert out["output"] == "hello\nworld\n"       # byte-identical, unfiltered


def test_seam_applies_injected_filter():
    # Filter uppercases — proves the seam routes output through it.
    env = _env(output_filter=lambda cmd, o, rc: o.upper())
    out = env.execute({"command": "printf 'hello\\n'"})
    assert out["output"] == "HELLO\n"
```

- [ ] **Step 6: Run to verify it fails**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_output_filter_seam.py -q`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'output_filter'`

- [ ] **Step 7: Add the constructor param and the seam in `acp_env.py`**

In `AcpEnvironment.__init__` (after `on_plan` param at `:29`, and after `self._on_plan = on_plan` at `:36`):

```python
                 on_plan: Callable[[list[tuple[str, str]]], None] | None = None,
                 output_filter: Callable[[str, str, int], str] | None = None,
                 **kwargs: Any):
        super().__init__(**kwargs)
        self._on_command = on_command
        self._check_permission = check_permission
        self._cancel_flag = cancel_flag
        self._client_terminal = client_terminal
        self._on_plan = on_plan
        self._output_filter = output_filter
```

In `execute`, replace the final `return out` (`:83`) with:

```python
        finally:
            self._on_command("done", command, out)
        if self._output_filter is not None and out.get("returncode") is not None:
            # All three branches converge here, AFTER _check_finished/Submitted.
            # The cancelled branch already returned early (:75), so it is never filtered.
            out = {**out, "output": self._output_filter(
                command, out.get("output", ""), out.get("returncode", 0))}
        return out
```

- [ ] **Step 8: Run to verify the seam tests pass**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_output_filter_seam.py -q`
Expected: PASS (4 passed)

- [ ] **Step 9: Wire the dispatcher + savings trace at the construction site**

In `harness/acp_agent.py`, the existing `on_command` callback (`:571`, signature `on_command(phase, command, out)`) already fires `("done", command, out)`. Emit savings there, and inject the dispatcher into the env.

Add the savings emit inside `on_command`, in the `phase == "done"` branch (locate the existing done-handling; add alongside it):

```python
            if phase == "done" and out is not None:
                raw = out.get("output", "") or ""
                # _filtered_len is stashed by the env after filtering; absent → no filter ran
                filtered_len = out.get("_filtered_bytes")
                if filtered_len is not None and len(raw) and tracer is not None:
                    tracer.emit("filter", "savings", command=command,
                                bytes_in=out.get("_raw_bytes", len(raw)),
                                bytes_out=filtered_len)
```

NOTE: to provide `_raw_bytes`/`_filtered_bytes` without changing `out`'s public shape for the model, stamp them in the seam (Step 7) BEFORE replacing output. Revise the Step-7 seam block to:

```python
        if self._output_filter is not None and out.get("returncode") is not None:
            raw = out.get("output", "")
            filtered = self._output_filter(command, raw, out.get("returncode", 0))
            out = {**out, "output": filtered,
                   "_raw_bytes": len(raw), "_filtered_bytes": len(filtered)}
        return out
```

At the env construction (`acp_agent.py:654`), pass the dispatcher:

```python
        from harness.output_filters.dispatch import filter_output
        env = AcpEnvironment(cwd=state.cwd, on_command=on_command,
                             check_permission=check_permission,
                             output_filter=filter_output,
                             ...)   # keep existing kwargs
```

- [ ] **Step 10: Test that the savings keys are stamped and don't leak into a no-filter run**

```python
# append to tests/test_output_filter_seam.py
def test_seam_stamps_savings_bytes_when_filter_shrinks():
    env = _env(output_filter=lambda cmd, o, rc: "x")   # shrinks
    out = env.execute({"command": "printf 'hello\\n'"})
    assert out["_raw_bytes"] == len("hello\n")
    assert out["_filtered_bytes"] == 1

def test_seam_no_savings_keys_without_filter():
    env = _env()
    out = env.execute({"command": "printf 'hi\\n'"})
    assert "_raw_bytes" not in out and "_filtered_bytes" not in out
```

- [ ] **Step 11: Run the new tests + the regression neighbors**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_output_filter_seam.py tests/test_acp_env.py tests/test_tracing_agent_perm.py -q`
Expected: PASS (all; the acp_env suite is the no-op parity regression guard)

- [ ] **Step 12: Commit**

```bash
git add harness/output_filters/ harness/acp_env.py harness/acp_agent.py tests/test_output_filter_seam.py
git commit -m "feat(filters): output-filter seam + savings trace (identity, no-op default)"
```

---

### Task 2: Test-runner filter (pytest)

The first REAL filter, targeting Done's own workload (pytest). Collapses the passing-test noise while preserving every failure/error verbatim.

**Files:**
- Create: `harness/output_filters/pytest_filter.py`
- Modify: `harness/output_filters/dispatch.py` (register the filter)
- Test: `tests/test_pytest_filter.py`

**Interfaces:**
- Consumes: `filter_output` registry (`FILTERS`) from Task 1.
- Produces: `harness.output_filters.pytest_filter.matches(command) -> bool` and `filter_pytest(command, output, returncode) -> str | None`.

- [ ] **Step 1: Write the failing test with REAL captured pytest output**

```python
# tests/test_pytest_filter.py
from harness.output_filters.pytest_filter import matches, filter_pytest

CLEAN = (
    "============================= test session starts =============================\n"
    "collected 42 items\n\n"
    "tests/test_a.py ......\n"
    "tests/test_b.py ....................\n"
    "tests/test_c.py ................\n\n"
    "============================== 42 passed in 1.23s ==============================\n"
)

FAILING = (
    "============================= test session starts =============================\n"
    "collected 3 items\n\n"
    "tests/test_x.py .F.\n\n"
    "=================================== FAILURES ===================================\n"
    "___________________________________ test_y ____________________________________\n"
    "    def test_y():\n"
    ">       assert 1 == 2\n"
    "E       assert 1 == 2\n\n"
    "tests/test_x.py:7: AssertionError\n"
    "=========================== 1 failed, 2 passed in 0.04s ===========================\n"
)


def test_matches_pytest_command():
    assert matches("pytest tests/ -q")
    assert matches("python -m pytest tests/test_a.py")
    assert not matches("git status")


def test_clean_run_is_compacted_but_keeps_summary():
    out = filter_pytest("pytest -q", CLEAN, 0)
    assert out is not None
    assert "42 passed in 1.23s" in out          # summary preserved
    assert len(out) < len(CLEAN)                 # noise collapsed
    assert "tests/test_b.py ...................." not in out  # per-file dots dropped


def test_failing_run_preserves_failure_verbatim():
    out = filter_pytest("pytest -q", FAILING, 1)
    # The FAILURES block and the assertion MUST survive untouched.
    assert "assert 1 == 2" in out
    assert "tests/test_x.py:7: AssertionError" in out
    assert "1 failed, 2 passed" in out
```

- [ ] **Step 2: Run to verify it fails**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_pytest_filter.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'harness.output_filters.pytest_filter'`

- [ ] **Step 3: Implement the filter (conservative — preserve failures whole)**

```python
# harness/output_filters/pytest_filter.py
"""pytest output filter: on a passing run, drop the per-file progress lines and
keep the session header + final summary. On ANY failure (returncode != 0 or a
FAILURES/ERRORS section present), return None → the FULL output passes through
unchanged. Never risk hiding a failure."""
from __future__ import annotations

import re

_SUMMARY = re.compile(r"^=+ .*(passed|failed|error|skipped).* in .*=+\s*$", re.M)
_HAS_FAILURE = re.compile(r"^(=+ (FAILURES|ERRORS) =+|=+ short test summary)", re.M)


def matches(command: str) -> bool:
    c = command.strip()
    return c.startswith("pytest") or " pytest" in c or "-m pytest" in c


def filter_pytest(command: str, output: str, returncode: int) -> str | None:
    if returncode != 0 or _HAS_FAILURE.search(output):
        return None                               # failures pass through whole
    m = _SUMMARY.search(output)
    if not m:
        return None                               # unrecognized shape → decline
    header_end = output.find("\n\n")              # keep the session-start header
    header = output[:header_end] if header_end != -1 else ""
    return f"{header}\n\n{m.group(0).strip()}\n"  # header + summary only
```

- [ ] **Step 4: Register the filter in the dispatcher**

In `harness/output_filters/dispatch.py`, after the `FILTERS = [...]` definition add:

```python
from harness.output_filters import pytest_filter  # noqa: E402

FILTERS.append((pytest_filter.matches, pytest_filter.filter_pytest))
```

- [ ] **Step 5: Run to verify it passes**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_pytest_filter.py -q`
Expected: PASS (3 passed)

- [ ] **Step 6: Run the dispatcher + seam tests for regressions**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_output_filter_seam.py tests/test_pytest_filter.py -q`
Expected: PASS (the Task-1 identity test still holds for non-pytest commands)

- [ ] **Step 7: Commit**

```bash
git add harness/output_filters/pytest_filter.py harness/output_filters/dispatch.py tests/test_pytest_filter.py
git commit -m "feat(filters): pytest filter — compact passing runs, failures pass through whole"
```

---

### Task 3: Optional `[harness]` toggle

Let an operator disable filtering via `done.conf`. Reuses the EXISTING `[harness]` settings API (`config.py:67/79`) — no new config-writer machinery (Codex MAJOR #4 avoided).

**Files:**
- Modify: `harness/acp_agent.py` (gate the `output_filter=` injection at `:654`)
- Test: `tests/test_output_filter_toggle.py`

**Interfaces:**
- Consumes: `harness.config.harness_setting(key) -> str | None` and `set_harness_setting(key, value)` (both exist).
- Produces: behavior — `[harness] output_filter = "false"` → env built with `output_filter=None`; absent or `"true"` → dispatcher injected.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_output_filter_toggle.py
import harness.config as config


def test_toggle_off_disables_filter(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "conf_path", lambda: tmp_path / "done.conf")
    config.set_harness_setting("output_filter", "false")
    assert config.harness_setting("output_filter") == "false"


def test_toggle_default_on_when_unset(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "conf_path", lambda: tmp_path / "done.conf")
    assert config.harness_setting("output_filter") is None   # unset → caller defaults to ON
```

- [ ] **Step 2: Run to verify it fails (or passes the read API)**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_output_filter_toggle.py -q`
Expected: PASS for both IF the existing API is sound (this task is mostly wiring); if `conf_path` isn't monkeypatchable as written, adjust the patch target to the symbol `set_harness_setting`/`harness_setting` actually read (confirm in `config.py`).

- [ ] **Step 3: Gate the injection in `acp_agent.py`**

At `:654`, replace the unconditional injection from Task 1 Step 9:

```python
        from harness.output_filters.dispatch import filter_output
        from harness.config import harness_setting
        _flt = None if harness_setting("output_filter") == "false" else filter_output
        env = AcpEnvironment(cwd=state.cwd, on_command=on_command,
                             check_permission=check_permission,
                             output_filter=_flt,
                             ...)   # keep existing kwargs
```

- [ ] **Step 4: Run to verify it passes**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_output_filter_toggle.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add harness/acp_agent.py tests/test_output_filter_toggle.py
git commit -m "feat(filters): [harness] output_filter toggle (default on; reuses existing config API)"
```

---

### Task 4: Lint filter (ruff) — add only if Task-1 trace data justifies it

Done is a Python project; `ruff` is the likely lint analogue to rtk's eslint win. **Gate this task on the Task-1 savings trace** showing ruff (or another tool) is actually high-volume/high-noise in Done's runs. If the data says otherwise, write the filter the data points to instead, following the identical shape.

**Files:**
- Create: `harness/output_filters/ruff_filter.py`
- Modify: `harness/output_filters/dispatch.py`
- Test: `tests/test_ruff_filter.py`

**Interfaces:**
- Produces: `matches(command) -> bool`, `filter_ruff(command, output, returncode) -> str | None` (same contract as pytest_filter).

- [ ] **Step 1: Write the failing test with REAL captured ruff output**

```python
# tests/test_ruff_filter.py
from harness.output_filters.ruff_filter import matches, filter_ruff

CLEAN = "All checks passed!\n"
WITH_ERRORS = (
    "harness/foo.py:12:1: F401 `os` imported but unused\n"
    "harness/bar.py:3:80: E501 Line too long (92 > 88)\n"
    "Found 2 errors.\n"
)


def test_matches_ruff():
    assert matches("ruff check harness/")
    assert not matches("pytest -q")


def test_clean_collapses_to_one_line():
    out = filter_ruff("ruff check .", CLEAN, 0)
    # already tiny — declines (no shrink) → None, so dispatcher passes through
    assert out is None or out == CLEAN


def test_errors_preserved_verbatim():
    out = filter_ruff("ruff check .", WITH_ERRORS, 1)
    assert out is None                            # errors pass through whole
```

- [ ] **Step 2: Run to verify it fails**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_ruff_filter.py -q`
Expected: FAIL — module not found

- [ ] **Step 3: Implement (errors always pass through; clean runs are already tiny)**

```python
# harness/output_filters/ruff_filter.py
"""ruff output filter. A clean run is already a single line ("All checks
passed!") so there is nothing to gain — decline. On errors, pass through whole.
This filter exists for symmetry/measurement; real shrink for ruff is marginal,
which is itself a finding worth recording rather than forcing savings."""
from __future__ import annotations


def matches(command: str) -> bool:
    return command.strip().startswith("ruff ") or " ruff " in command


def filter_ruff(command: str, output: str, returncode: int) -> str | None:
    return None      # nothing safe to compact; pass through (records the no-win)
```

- [ ] **Step 4: Register + run**

In `dispatch.py`:

```python
from harness.output_filters import ruff_filter  # noqa: E402

FILTERS.append((ruff_filter.matches, ruff_filter.filter_ruff))
```

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_ruff_filter.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add harness/output_filters/ruff_filter.py harness/output_filters/dispatch.py tests/test_ruff_filter.py
git commit -m "feat(filters): ruff filter (pass-through; records that ruff has no safe win)"
```

---

### Task 5: Full-suite verification + docs

**Files:**
- Modify: `docs/superpowers/specs/2026-06-30-output-filter-seam-design.md` (mark which OQs are resolved)
- Test: full suite

**Interfaces:** none.

- [ ] **Step 1: Run the entire suite**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/ -q`
Expected: PASS (no regressions; the no-op parity test in `test_acp_env.py` is the key guard)

- [ ] **Step 2: Record the headless-path decision in the spec**

Add a note to the spec's "Out of scope" or a new "Deferred" section: the headless/cron path (`run_traced.py:168` builds a plain `LocalEnvironment`) does NOT get the seam in v1. State whether it should later (depends on whether lint/test commands run under cron meaningfully).

- [ ] **Step 3: Record the trace finding**

After running real sessions with Task 1's trace, summarize in the spec which command kinds actually dominate Done's `bytes_in` — this closes Open Question 1 and justifies (or retires) Tasks 2/4.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-06-30-output-filter-seam-design.md
git commit -m "docs(filters): resolve OQ1 from trace data; record headless-path deferral"
```

---

## Self-Review

**Spec coverage:**
- Component 1 (filters) → Tasks 2, 4. Component 2 (seam in env.execute) → Task 1. Component 3 (config toggle) → Task 3. Component 4 (observability) → Task 1 Steps 9-10. OQ1 (which filters first) → Task 1 trace + Task 5 Step 3. OQ2 (aggressiveness on failing runs) → Task 2's "failures pass through whole" rule. Fail-open → Global Constraints + Task 1 dispatcher.
- Codex MAJOR #4 (config writer) → avoided by reusing existing `[harness]` API (Task 3).
- Codex BLOCKERs (perm gate, env lifecycle) → avoided by construction: filtering is a pure transform on already-produced `out`, no subprocess, no new tool (Global Constraints + Task 1 seam placement after `_check_finished`).

**Placeholder scan:** No TBD/TODO; every code step shows code; test code is concrete with real captured fixtures.

**Type consistency:** `filter_output(command, output, returncode) -> str` and per-filter `matches(command) -> bool` / `filter_*(command, output, returncode) -> str | None` are consistent across Tasks 1, 2, 4. The env param `output_filter: Callable[[str,str,int], str] | None` matches the dispatcher signature. Savings keys `_raw_bytes`/`_filtered_bytes` are defined in Task 1 Step 9 and consumed in the same step.

**Known soft spots for the implementer to confirm against live code (do NOT skip):**
1. Task 1 Step 9 — the EXACT structure of the existing `on_command` done-branch and whether a `tracer` symbol is in scope at `acp_agent.py:571`. Adapt the emit to the real local names.
2. Task 3 Step 2 — the correct monkeypatch target for `harness_setting`/`set_harness_setting` (confirm whether they read `conf_path()` or a cached path).
