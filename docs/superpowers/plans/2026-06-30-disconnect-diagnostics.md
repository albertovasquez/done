# Disconnect Diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the agent subprocess dies mid-turn, show the actual exit cause (exit code or terminating signal) plus its last stderr lines inline in the TUI, instead of the opaque `(Connection closed)`.

**Architecture:** Two additions to `harness/tui/app.py`'s `HarnessTui`: (1) an always-on, bounded in-memory stderr ring buffer that `_drain_stderr` appends to unconditionally (not gated on `--debug`), cleared per connect; (2) a disconnect handler in `_send_prompt`'s except block that first flushes the concurrent stderr drain, then reads the subprocess `returncode` and formats it (exit code vs. signal name) into a multi-line on-screen message with the buffered stderr tail.

**Tech Stack:** Python 3.11, asyncio, Textual TUI, `collections.deque`, the `signal` module. Tests use the existing `tests/test_tui_stderr_drain.py` fixture pattern (bare `HarnessTui`, fake `StreamReader`, no subprocess).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-06-30-disconnect-diagnostics-design.md`.
- Diagnostics only — do NOT add any guard/try-except intended to *prevent* the crash; this surfaces the cause, nothing more.
- Do NOT change the existing `--debug` trace relay in `_drain_stderr` (`if self._tracer is not None: self._tracer.emit(...)`) — the new buffer is an independent, additive consumer of the same line.
- Ring buffer is bounded: `collections.deque(maxlen=20)`.
- All bounded waits use `timeout=0.5` (the value the spec specifies); a timeout degrades gracefully (partial/unknown), never blocks the UI or raises.
- Scope is the mid-prompt disconnect path (`_send_prompt`'s except at `app.py:1159`) only — NOT the connect-time failure path (`app.py:294`).
- Test command: the worktree has NO local `.venv`. Run pytest with the primary checkout's interpreter from the worktree root — the project conftest prepends this worktree's absolute src root, so the worktree's code is what gets tested:
  `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/ -q` (target `tests/` only). All `pytest` commands in the steps below assume this interpreter path.
- Tests put `sys.path.insert(0, "upstream/src")` and `sys.path.insert(0, ".")` at the top, matching `tests/test_tui_stderr_drain.py`.

---

### Task 1: `_format_exit` helper — render returncode as a human-readable cause

A pure, static helper that converts an `asyncio.subprocess.Process.returncode` into a display string. POSIX convention: `None` = not yet reaped, `>= 0` = normal exit code, `< 0` = killed by signal `-returncode`. Isolated and pure so it can be unit-tested without any TUI/async machinery.

**Files:**
- Modify: `harness/tui/app.py` (add a `@staticmethod` to `HarnessTui`, placed next to the other static helpers — e.g. just after `_escape` at `app.py:1138-1140`)
- Test: `tests/test_tui_disconnect_diag.py` (new file)

**Interfaces:**
- Consumes: nothing.
- Produces: `HarnessTui._format_exit(returncode: int | None) -> str` — returns `"exit status unknown"` for `None`, `"exited with code N"` for `N >= 0`, `"killed by SIG<NAME>"` for negative (signal-encoded), falling back to `"killed by signal N"` if the signal number is unrecognized.

- [ ] **Step 1: Write the failing test**

Create `tests/test_tui_disconnect_diag.py`:

```python
"""Disconnect diagnostics: _format_exit rendering + the _send_prompt disconnect
handler surfacing exit cause and buffered stderr. Exercised directly on a bare
HarnessTui (no Textual app, no subprocess), mirroring test_tui_stderr_drain.py.
"""

import asyncio
import signal
import sys

sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

from harness.tui.app import HarnessTui


def _bare_app():
    return HarnessTui(agent_cmd=["x"], cwd=".", model="mock")


def test_format_exit_none_is_unknown():
    assert HarnessTui._format_exit(None) == "exit status unknown"


def test_format_exit_zero_is_clean_code():
    assert HarnessTui._format_exit(0) == "exited with code 0"


def test_format_exit_positive_code():
    assert HarnessTui._format_exit(3) == "exited with code 3"


def test_format_exit_signal_name():
    # -11 = killed by SIGSEGV
    assert HarnessTui._format_exit(-signal.SIGSEGV) == "killed by SIGSEGV"


def test_format_exit_unrecognized_signal_falls_back():
    # an absurd negative value with no Signals member must not raise
    out = HarnessTui._format_exit(-9999)
    assert out == "killed by signal 9999", out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_tui_disconnect_diag.py -q`
Expected: FAIL — `AttributeError: type object 'HarnessTui' has no attribute '_format_exit'`.

- [ ] **Step 3: Write minimal implementation**

In `harness/tui/app.py`, first add `import signal` to the stdlib import block (after `import os` at line 23 — `signal` is NOT currently imported, verified). Then add this `@staticmethod` to `HarnessTui` immediately after the existing `_escape` static method (which ends at line 1140):

```python
    @staticmethod
    def _format_exit(returncode: int | None) -> str:
        """Render a subprocess returncode as a human-readable exit cause.
        POSIX: None = not reaped, >=0 = exit code, <0 = killed by signal -rc."""
        if returncode is None:
            return "exit status unknown"
        if returncode >= 0:
            return f"exited with code {returncode}"
        signum = -returncode
        try:
            name = signal.Signals(signum).name
        except ValueError:
            return f"killed by signal {signum}"
        return f"killed by {name}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_tui_disconnect_diag.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add harness/tui/app.py tests/test_tui_disconnect_diag.py
git commit -m "feat(tui): add _format_exit helper for disconnect exit cause"
```

---

### Task 2: Always-on stderr ring buffer — buffer lines + clear on connect

Add the bounded `self._stderr_tail` deque, populate it from `_drain_stderr` unconditionally (independent of the tracer relay), and clear it at the start of each connect so a prior process's lines can never be misattributed to a later death.

**Files:**
- Modify: `harness/tui/app.py` — `__init__` (insert near `self._proc = None` at line 123), `_connect` (clear at `app.py:286`), `_drain_stderr` (append at `app.py:299-317`)
- Test: `tests/test_tui_disconnect_diag.py` (extend), and the existing `tests/test_tui_stderr_drain.py` must still pass unchanged.

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `self._stderr_tail: collections.deque[str]` (maxlen=20) on `HarnessTui`, holding the most recent decoded stderr lines (no trailing newline). `_drain_stderr` appends every line to it. `_connect` calls `self._stderr_tail.clear()` before starting the new drain task.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tui_disconnect_diag.py`. Reuse the `_Proc` / `_reader_with` helpers — add them to this file (copied from `test_tui_stderr_drain.py`, since tasks may run independently):

```python
class _Proc:
    """Minimal subprocess stand-in: a .stderr StreamReader and a returncode."""
    def __init__(self, reader, returncode=None):
        self.stderr = reader
        self.returncode = returncode


def _reader_with(lines):
    # MUST be built inside a running loop (StreamReader binds the loop at init).
    r = asyncio.StreamReader()
    for ln in lines:
        r.feed_data(ln)
    r.feed_eof()
    return r


def test_drain_appends_to_stderr_tail_without_tracer():
    """The ring buffer is populated even when no --debug tracer is set."""
    app = _bare_app()
    app._tracer = None  # no debug relay — buffer must still fill

    async def go():
        reader = _reader_with([b"alpha\n", b"beta\n"])
        await asyncio.wait_for(app._drain_stderr(_Proc(reader)), timeout=5.0)

    asyncio.run(go())
    assert list(app._stderr_tail) == ["alpha", "beta"], list(app._stderr_tail)


def test_stderr_tail_is_bounded_to_20():
    app = _bare_app()
    app._tracer = None

    async def go():
        reader = _reader_with([f"line{i}\n".encode() for i in range(30)])
        await asyncio.wait_for(app._drain_stderr(_Proc(reader)), timeout=5.0)

    asyncio.run(go())
    tail = list(app._stderr_tail)
    assert len(tail) == 20, len(tail)
    assert tail[0] == "line10" and tail[-1] == "line29", tail
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_tui_disconnect_diag.py -q -k stderr_tail`
Expected: FAIL — `AttributeError: 'HarnessTui' object has no attribute '_stderr_tail'`.

- [ ] **Step 3: Write minimal implementation**

Three edits in `harness/tui/app.py`:

**(a)** Add `from collections import deque` to the stdlib import block (after `import time` at line 24 — it is NOT currently imported, verified).

**(b)** In `__init__`, immediately after line 123 (`self._proc = None ...`), add:

```python
        self._stderr_tail = deque(maxlen=20)  # last agent stderr lines, for disconnect diagnostics
```

**(c)** In `_connect`, immediately after line 286 (`self._stderr_task = asyncio.create_task(self._drain_stderr(self._proc))`), add the clear BEFORE the drain can append — actually place it just BEFORE line 286 so the new process starts with an empty buffer:

Replace:

```python
        self._stderr_task = asyncio.create_task(self._drain_stderr(self._proc))
```

with:

```python
        self._stderr_tail.clear()  # fresh buffer per process: never misattribute a prior process's stderr
        self._stderr_task = asyncio.create_task(self._drain_stderr(self._proc))
```

**(d)** In `_drain_stderr`, change the body of the `while True` loop so the buffer append happens unconditionally, independent of the tracer branch. Replace:

```python
                line = await stderr.readline()
                if not line:
                    break                              # EOF: agent exited
                if self._tracer is not None:
                    self._tracer.emit("agent", "stderr",
                                      text=line.decode("utf-8", "replace").rstrip("\n"))
```

with:

```python
                line = await stderr.readline()
                if not line:
                    break                              # EOF: agent exited
                text = line.decode("utf-8", "replace").rstrip("\n")
                self._stderr_tail.append(text)         # always buffer (independent of --debug)
                if self._tracer is not None:
                    self._tracer.emit("agent", "stderr", text=text)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_tui_disconnect_diag.py tests/test_tui_stderr_drain.py -q`
Expected: PASS — the new buffer tests pass AND the existing drain tests still pass (the tracer relay still receives `["line one", "line two"]`).

- [ ] **Step 5: Commit**

```bash
git add harness/tui/app.py tests/test_tui_disconnect_diag.py
git commit -m "feat(tui): always-on stderr ring buffer, cleared per connect"
```

---

### Task 3: Disconnect handler — flush drain, read returncode, show cause + tail

Replace the single-line generic disconnect message in `_send_prompt`'s except block with a handler that (1) awaits the concurrent stderr drain so the final crash lines are flushed, (2) reads/awaits the subprocess returncode, (3) builds a multi-line message with the formatted exit cause and the buffered stderr tail. Extracted into a helper method so it is testable without driving a full turn.

**Files:**
- Modify: `harness/tui/app.py` — `_send_prompt` except block (`app.py:1159-1161`); add a new async helper method `_report_disconnect`.
- Test: `tests/test_tui_disconnect_diag.py` (extend)

**Interfaces:**
- Consumes: `HarnessTui._format_exit` (Task 1), `self._stderr_tail` (Task 2), `self._proc`, `self._stderr_task`.
- Produces: `async def _report_disconnect(self, e: BaseException) -> None` on `HarnessTui` — flushes `self._stderr_task` (bounded 0.5s, guarded), resolves the exit cause from `self._proc`, and appends the multi-line error block via `self._append_line`. Called from the except block in place of the old single `_append_line`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tui_disconnect_diag.py`:

```python
def _drive_report(app, exc):
    """Run _report_disconnect on a bare app under a fresh loop, capturing
    every _append_line call into app._captured_lines."""
    app._captured_lines = []
    app._append_line = lambda s: app._captured_lines.append(s)

    async def go():
        await asyncio.wait_for(app._report_disconnect(exc), timeout=5.0)

    asyncio.run(go())
    return "\n".join(app._captured_lines)


def test_report_disconnect_shows_signal_and_stderr_tail():
    app = _bare_app()
    app._stderr_task = None                       # nothing to flush
    app._proc = _Proc(None, returncode=-signal.SIGSEGV)
    app._stderr_tail.extend(["Fatal Python error: Segmentation fault", "  frame 0"])

    out = _drive_report(app, ConnectionError("Connection closed"))

    assert "agent disconnected" in out
    assert "killed by SIGSEGV" in out
    assert "Fatal Python error: Segmentation fault" in out
    assert "frame 0" in out
    assert "Connection closed" in out             # original exception text retained


def test_report_disconnect_flushes_drain_before_reading_tail():
    """The handler must await the running drain so the LAST stderr line (the
    crash cause), which arrives after a delay, is included — not raced past."""
    app = _bare_app()
    app._proc = _Proc(None, returncode=-signal.SIGSEGV)

    async def slow_drain():
        await asyncio.sleep(0.05)                 # final line lands late
        app._stderr_tail.append("LATE crash line")

    async def go():
        app._captured_lines = []
        app._append_line = lambda s: app._captured_lines.append(s)
        app._stderr_task = asyncio.create_task(slow_drain())
        await asyncio.wait_for(app._report_disconnect(ConnectionError("x")), timeout=5.0)
        return "\n".join(app._captured_lines)

    out = asyncio.run(go())
    assert "LATE crash line" in out, out          # proves we awaited the drain


def test_report_disconnect_unknown_when_returncode_none():
    app = _bare_app()
    app._stderr_task = None
    app._proc = _Proc(None, returncode=None)      # not reaped; wait() also yields None below

    # _Proc has no wait(); give it one that returns immediately with no code
    async def _wait():
        return None
    app._proc.wait = _wait

    out = _drive_report(app, ConnectionError("x"))
    assert "exit status unknown" in out, out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_tui_disconnect_diag.py -q -k report_disconnect`
Expected: FAIL — `AttributeError: 'HarnessTui' object has no attribute '_report_disconnect'`.

- [ ] **Step 3: Write minimal implementation**

**(a)** Add the helper method to `HarnessTui` (place it right after `_send_prompt`, before `_drain_queue` at `app.py:1178`):

```python
    async def _report_disconnect(self, e: BaseException) -> None:
        """Surface WHY the agent subprocess died: exit code / signal + last
        stderr lines. Flushes the concurrent stderr drain first so the final
        crash lines (which race the stdout-EOF that triggered `e`) are present."""
        # 1) Flush the drain so the last stderr lines land in the buffer.
        #    shield() so a timeout here never CANCELS the drain task — it is
        #    owned by _teardown, not by this best-effort handler.
        task = self._stderr_task
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=0.5)
            # CancelledError is BaseException-derived (NOT caught by Exception in
            # 3.11, verified) — list it explicitly so a cancel while we wait is
            # swallowed too. Diagnostics are best-effort: never block or re-raise.
            except (asyncio.CancelledError, Exception):
                pass
        # 2) Resolve the exit cause. (asyncio.TimeoutError IS an Exception in
        #    3.11 — it is the builtin TimeoutError — so `except Exception` covers
        #    the wait() timeout; a None rc just renders "exit status unknown".)
        rc = getattr(self._proc, "returncode", None)
        if rc is None and self._proc is not None:
            try:
                rc = await asyncio.wait_for(self._proc.wait(), timeout=0.5)
            except Exception:
                rc = None
        # 3) Build the multi-line message.
        self._append_line(_c("error", f"agent disconnected — restart to continue ({e})"))
        self._append_line(_c("error", f"process: {self._format_exit(rc)}"))
        if self._stderr_tail:
            self._append_line(_c("muted", "last stderr:"))
            for ln in self._stderr_tail:
                self._append_line(_c("muted", f"  {ln}"))
```

**(b)** In `_send_prompt`'s except block, replace lines 1159-1161:

```python
        except Exception as e:
            self._apply(TurnEnded(ok=False))
            self._append_line(_c("error", f"agent disconnected — restart to continue ({e})"))
```

with:

```python
        except Exception as e:
            self._apply(TurnEnded(ok=False))
            await self._report_disconnect(e)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_tui_disconnect_diag.py -q`
Expected: PASS (all tasks' tests green, including the flush-ordering test).

- [ ] **Step 5: Commit**

```bash
git add harness/tui/app.py tests/test_tui_disconnect_diag.py
git commit -m "feat(tui): surface subprocess exit cause + stderr tail on disconnect"
```

---

### Task 4: Full-suite regression + manual sanity note

Confirm nothing else that asserts on the old single-line disconnect message regressed, and run the whole targeted suite.

**Files:**
- Possibly modify: any existing test that asserted the EXACT old disconnect string (search first; only touch if it now over-asserts).
- Test: whole suite.

**Interfaces:**
- Consumes: all prior tasks.
- Produces: nothing new.

- [ ] **Step 1: Search for tests that assert on the old message**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/ -q -k "disconnect or stderr"` and also `grep -rn "agent disconnected" tests/`.
Expected: only `tests/test_tui_disconnect_diag.py` (and possibly the docstring in `tests/test_acp_agent.py:171`, which is a comment, not an assertion). If any OTHER test asserts the exact old string `"agent disconnected — restart to continue (...)"` as the *only* line, update it to assert substring `"agent disconnected"` (the message is now multi-line). Do not weaken any other assertion.

- [ ] **Step 2: Run the full targeted suite**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/ -q`
Expected: PASS — same green baseline as before plus the new `test_tui_disconnect_diag.py` cases. No new failures.

- [ ] **Step 3: Commit any test fixups (only if Step 1 required changes)**

```bash
git add tests/
git commit -m "test(tui): update disconnect-message assertions for multi-line diagnostics"
```

If Step 1 required no changes, skip this commit.

---

## Self-Review Notes

- **Spec coverage:** §"Always-on stderr ring buffer" → Task 2; §"Exit-cause formatting on disconnect" steps 1-4 → Tasks 1+3 (flush-drain = Task 3 step 1; returncode read = Task 3 step 2; `_format_exit` = Task 1; multi-line build = Task 3 step 3); §"deque clear on connect" → Task 2(c); §Testing bullets → Tasks 1-4 tests (format_exit, integration message, drain-flush ordering, deque clear, tracer-independence, full suite); §"covers mid-prompt disconnect only" → Global Constraints + Task 3 scope.
- **Placeholder scan:** none — every step shows exact code/commands.
- **Type consistency:** `_format_exit(returncode: int | None) -> str` used identically in Tasks 1 and 3; `_stderr_tail` deque and `_stderr_task` referenced consistently; `_report_disconnect(self, e)` signature matches its call site.
