# Surface subprocess exit cause on agent disconnect

## Problem

When the agent subprocess dies, the TUI catches the resulting exception
generically at `harness/tui/app.py:1159-1161`:

```python
except Exception as e:
    self._apply(TurnEnded(ok=False))
    self._append_line(_c("error", f"agent disconnected — restart to continue ({e})"))
```

`{e}` is whatever the ACP connection layer raised — in practice
`ConnectionError("Connection closed")`, raised when the client detects the
agent subprocess's stdout has reached EOF (the OS process exited). This string
carries no information about **why** the process exited: no exit code, no
signal, no final output. Every occurrence of this bug today is undiagnosable
from the TUI alone.

This was investigated this session as a follow-up to a (disproven) hypothesis
that an in-process `BaseException` escaping `HarnessAgent.prompt()` could
cause the disconnect. Adversarial review (Opus 4.8), verified independently
against the installed `agent-client-protocol` library
(`acp/task/supervisor.py:46-48`, `acp/task/dispatcher.py:88`), established
that ACP requests run as isolated `asyncio.Task`s whose cancellation/crash is
swallowed by the task supervisor before ever reaching the connection — an
in-process Python exception on one request cannot close the connection or
kill the process. "Connection closed" specifically means the **OS process
itself died** (segfault, unhandled error on a non-asyncio thread, `os._exit`,
SIGKILL/OOM, etc.) — a different class of problem that requires
subprocess-level diagnostics, not an exception guard.

Existing infrastructure already pipes the agent's stderr
(`harness/tui/app.py:299-317`, `_drain_stderr`) to prevent the pipe buffer
from filling and deadlocking the agent mid-turn (a prior, unrelated bug fixed
in PR #91). Today that drained stderr is only **relayed to the `--debug`
trace** (`if self._tracer is not None: self._tracer.emit(...)`) — discarded
entirely otherwise — and even under `--debug`, this session's investigation
found the resulting `harness.log` empty for a reproduced disconnect, so the
existing relay path is not reliably capturing the cause either.

## Goal

When the agent disconnects, show the actual exit cause inline, in the TUI,
every time — not gated behind `--debug`, not requiring a trace-file
post-mortem. Specifically: the subprocess's exit code or terminating signal,
plus its last few lines of stderr.

This is diagnostics only. It does not fix whatever causes the subprocess to
die — it makes the next occurrence immediately actionable instead of opaque.

## Design

Two additions, both in `harness/tui/app.py`, around the existing
`HarnessTui` connection-management code (`_connect`, `_drain_stderr`,
`_teardown`, `_send_prompt`).

### 1. Always-on stderr ring buffer

Add a small fixed-size buffer (`collections.deque(maxlen=20)`) as an instance
attribute on `HarnessTui`, e.g. `self._stderr_tail`. Initialize it in
`__init__` alongside the other connection-state attributes (near line 123,
where `self._proc = None` is set).

In `_drain_stderr` (`app.py:299-317`), append each decoded line to
`self._stderr_tail` **unconditionally** — independent of the existing
`if self._tracer is not None:` branch, which stays as-is for the `--debug`
trace relay. The two are independent consumers of the same drained line; nei-
ther should gate the other.

Reset/clear `self._stderr_tail` on each successful `_connect()` (or accept
that it naturally rolls over via `maxlen` — a fresh connect doesn't need an
explicit clear since the deque just fills with the new process's output as it
drains; a stale line from a prior process would only linger if the new
process produces fewer than 20 stderr lines before the next disconnect, which
is an acceptable edge case for a diagnostic aid, not worth extra state
management).

### 2. Exit-cause formatting on disconnect

In `_send_prompt`'s `except Exception as e:` block (`app.py:1159-1161`),
before appending the error line:

1. Determine the subprocess's exit status. `self._proc` (an
   `asyncio.subprocess.Process`) should already have `returncode` set by the
   time the connection-closed exception surfaces (the process exited before
   the pipe EOF was observed). Read `self._proc.returncode` directly — no
   blocking `wait()` needed; if it happens to still be `None` (process hasn't
   been reaped yet), fall back to a short bounded wait
   (`asyncio.wait_for(self._proc.wait(), timeout=0.5)`) and treat a timeout as
   "exit status unknown" rather than blocking the UI.
2. Format it via a small helper, e.g. `_format_exit(returncode: int | None) -> str`:
   - `None` → `"exit status unknown"`
   - `>= 0` → `f"exited with code {returncode}"`
   - `< 0` → resolve the signal name via `signal.Signals(-returncode).name`
     (fall back to the raw number if `ValueError`, e.g. an unrecognized
     signal) → `f"killed by {name}"`
3. Build the on-screen message as multiple lines: the existing
   `"agent disconnected — restart to continue"` header, then the formatted
   exit cause, then (if `self._stderr_tail` is non-empty) a `"last stderr:"`
   section with each buffered line indented. Keep the original `({e})`
   suffix on the header line — it's still useful context (distinguishes a
   `ConnectionError` from some other exception type at this catch site) and
   costs nothing to retain.

Example on-screen result:
```
agent disconnected — restart to continue (Connection closed)
process: killed by SIGSEGV
last stderr:
  Fatal Python error: Segmentation fault
  ...
```

### Why this scope, not broader

- **Not fixing the crash itself** — per the user's explicit prioritization
  this session, diagnostics come first; the fix depends on knowing the cause.
- **Not changing the `--debug` trace relay** — it already works for
  full-session post-mortem analysis when `--debug` is on; this is additive,
  for the always-on inline case.
- **Not adding a ring buffer for *all* trace events**, only stderr — stdout/
  stderr is the one channel that currently has zero non-`--debug` visibility
  for a process-death case; other diagnostics (ACP protocol messages, tool
  calls) are already visible in the TUI's own transcript as the turn was in
  progress.
- **Not persisting stderr to disk unconditionally** — that was raised as a
  broader alternative during scoping and explicitly not chosen; in-memory,
  inline display is the agreed scope.

## Testing

- Unit test `_format_exit` directly: `None`, `0`, a positive code, a negative
  signal-encoded value (e.g. `-11` → `"killed by SIGSEGV"`), and an
  unrecognized negative value (falls back gracefully, doesn't raise).
- Integration-style test on `HarnessTui._send_prompt`'s exception path:
  monkeypatch `self._conn.prompt` to raise, set `self._proc.returncode` to a
  known value and `self._stderr_tail` to known lines, assert the appended
  error text contains the formatted exit cause and the stderr lines.
- Confirm `_drain_stderr` still appends to `self._stderr_tail` regardless of
  `self._tracer` being `NullTracer` vs a real tracer (both branches exercised).
- Re-run the full suite (`.venv/bin/python -m pytest tests/ -q`) to confirm no
  regression to existing stderr-drain / disconnect-message tests.

## Risks

- **`self._proc.returncode` read race:** if read too early (process hasn't
  actually exited yet, and the `ConnectionError` came from something else
  entirely, e.g. a transport-level error unrelated to process death), the
  formatted message could misleadingly say "exit status unknown" or show a
  stale/`None` value. This is acceptable — the message is best-effort
  diagnostics, not a guarantee, and `None`/unknown is an honest, non-mislead-
  ing fallback covered by the design above.
- **Stderr lines could contain sensitive data** (e.g. an API key in a
  traceback line from a misconfigured request). This is no different from the
  existing `--debug` trace relay's exposure (same source, same content) — not
  a new risk introduced by this change, but worth noting it's still only
  shown locally in the user's own TUI, never transmitted anywhere.
