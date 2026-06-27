# Unified `--debug` Trace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A durable JSONL trace at `harness/runs/<ts>/trace.jsonl` covering both the dn↔agent ACP boundary and the agent's internal loop, gated behind `--debug`, written by the TUI alone.

**Architecture:** The agent relays its events over the existing `with_meta()` → `field_meta["harness"]` ACP channel; the TUI is the sole writer. A new `DebugTracer` wraps the existing `Emitter`. Off by default and byte-identical to today's wire when off.

**Tech Stack:** Python 3.10+, `acp` (agent-client-protocol), Textual, existing `harness.events.Emitter`, pytest.

## Global Constraints

- Always work in the worktree `/Users/alberto/Work/Quiubo/harness/.claude/worktrees/debug-trace` (branch `worktree-debug-trace`). Never edit the primary checkout.
- Test command (run from the worktree root): `.venv/bin/python -m pytest tests/ -q`
- **No-op invariant:** when `--debug` is off, the ACP wire (`field_meta["harness"]` payloads) MUST be byte-identical to today. No `trace` key is added when off.
- Trace file lives under `harness/runs/<ts>/` (already gitignored via `.gitignore:4`).
- Reuse `harness.events.Emitter` / `Event`; do not write a second JSONL writer.
- Precedence for the flag everywhere: CLI flag > `HARNESS_DEBUG` env > `done.conf [harness] debug` > default off. Mirror the existing model/yolo resolution in `tui_main.py` / `acp_main.py`.
- The agent's stdout IS the ACP wire — the agent MUST NOT print to stdout. All agent-side trace goes over `with_meta`, never `print`.

---

### Task 1: `DebugTracer` — the single writer

**Files:**
- Create: `harness/debug_trace.py`
- Test: `tests/test_debug_trace.py`

**Interfaces:**
- Consumes: `harness.events.Emitter`, `harness.events.Event`.
- Produces:
  - `class DebugTracer` with:
    - `DebugTracer.open(run_dir: Path) -> DebugTracer` — classmethod; opens `run_dir/"trace.jsonl"` via an `Emitter` (real wall-clock, `console=False`). Creates `run_dir` if missing.
    - `emit(self, source: str, type: str, **data) -> None` — writes one event; prepends `source` into the event by emitting `type=type` with `data={"source": source, **data}`... NO — see Step 3: `source` is a top-level field, so `emit` builds the line directly.
    - `close(self) -> None`
  - `class NullTracer` with the same `emit`/`close` signatures, both no-ops. Used when `--debug` is off so call sites are unconditional.
  - `def make_tracer(enabled: bool, run_dir: Path) -> DebugTracer | NullTracer`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_debug_trace.py
import json
from pathlib import Path

from harness.debug_trace import DebugTracer, NullTracer, make_tracer


def _lines(p: Path):
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def test_tracer_writes_source_and_monotonic_seq(tmp_path):
    t = DebugTracer.open(tmp_path)
    t.emit("dn", "tx.prompt", sid="s1", turn=1, text="hi")
    t.emit("agent", "llm.call", sid="s1", turn=1, n_calls=1)
    t.close()
    rows = _lines(tmp_path / "trace.jsonl")
    assert [r["seq"] for r in rows] == [0, 1]
    assert rows[0]["source"] == "dn"
    assert rows[0]["type"] == "tx.prompt"
    assert rows[0]["data"] == {"sid": "s1", "turn": 1, "text": "hi"}
    assert rows[1]["source"] == "agent"
    assert isinstance(rows[0]["t"], float)


def test_open_creates_missing_dir(tmp_path):
    sub = tmp_path / "runs" / "20260627-000000"
    t = DebugTracer.open(sub)
    t.emit("dn", "x")
    t.close()
    assert (sub / "trace.jsonl").exists()


def test_null_tracer_writes_nothing(tmp_path):
    t = NullTracer()
    t.emit("dn", "tx.prompt", sid="s1")   # must not raise
    t.close()
    assert not (tmp_path / "trace.jsonl").exists()


def test_make_tracer_dispatch(tmp_path):
    assert isinstance(make_tracer(False, tmp_path), NullTracer)
    assert isinstance(make_tracer(True, tmp_path), DebugTracer)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_debug_trace.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'harness.debug_trace'`

- [ ] **Step 3: Write minimal implementation**

```python
# harness/debug_trace.py
"""DebugTracer: the single writer of the unified --debug trace.

One file (runs/<ts>/trace.jsonl), one writer (the TUI). Wraps the existing
Emitter so the JSONL line shape stays consistent with the CLI's events.jsonl,
but adds a top-level `source` field ("dn" | "agent") so a reader can tell which
process spoke. When --debug is off, NullTracer is used and every call site is a
no-op (preserves the byte-identical-wire invariant)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from harness.events import Emitter


class DebugTracer:
    def __init__(self, emitter: Emitter) -> None:
        self._emitter = emitter

    @classmethod
    def open(cls, run_dir: str | Path) -> "DebugTracer":
        run_dir = Path(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        # real wall-clock so two processes' events order correctly; console off
        # (the trace is a file, never printed — stdout is the ACP wire on the agent).
        emitter = Emitter(run_dir / "trace.jsonl", clock=time.time, console=False)
        return cls(emitter)

    def emit(self, source: str, type: str, **data: Any) -> None:
        # Build the event through the Emitter (keeps seq monotonic + the file
        # handle), but stamp `source` as a sibling of seq/t/type/data. We do this
        # by writing the line ourselves through the emitter's file via emit() then
        # post-processing is ugly; instead emit a normal event and let to_dict
        # carry source inside data is WRONG (spec wants top-level). So we override
        # the line here: emit builds the Event, we re-serialize with source.
        ev = self._emitter._next_event(type, **data)   # noqa: SLF001 — same package contract
        line = {"seq": ev.seq, "t": ev.t, "source": source, "type": ev.type, "data": ev.data}
        self._emitter._fh.write(json.dumps(line) + "\n")  # noqa: SLF001
        self._emitter._fh.flush()

    def close(self) -> None:
        self._emitter.close()


class NullTracer:
    def emit(self, source: str, type: str, **data: Any) -> None:
        return None

    def close(self) -> None:
        return None


def make_tracer(enabled: bool, run_dir: str | Path) -> "DebugTracer | NullTracer":
    return DebugTracer.open(run_dir) if enabled else NullTracer()
```

> **Note for the implementer:** `_next_event`, `_fh` are used across the `harness` package boundary deliberately — `events.py` already exposes `_next_event` for `_EventSource` subclasses and `write_event`. If a reviewer objects to reaching into `_fh`, the alternative is to add a `source` field to `Event` itself; do NOT do that in this task — it would ripple into the CLI's `events.jsonl` schema. Keep the coupling local to `DebugTracer`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_debug_trace.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add harness/debug_trace.py tests/test_debug_trace.py
git commit -m "feat(trace): DebugTracer — single JSONL writer with source field"
```

---

### Task 2: Flag resolution — `--debug` end to end

**Files:**
- Modify: `harness/tui_main.py` (argparse ~line 80; agent_cmd build ~line 111; `HarnessTui(...)` ~line 116)
- Modify: `harness/acp_main.py` (argparse ~line 71; `HarnessAgent(...)` ~line 140)
- Create: `harness/debug_flag.py` (the shared resolver)
- Test: `tests/test_debug_flag.py`

**Interfaces:**
- Consumes: nothing from prior tasks.
- Produces:
  - `def resolve_debug(flag: bool, env: dict, conf_debug: bool | None) -> bool` in `harness/debug_flag.py` — pure precedence: `flag or env.get("HARNESS_DEBUG") == "1" or bool(conf_debug)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_debug_flag.py
from harness.debug_flag import resolve_debug


def test_flag_wins():
    assert resolve_debug(True, {}, None) is True


def test_env_enables():
    assert resolve_debug(False, {"HARNESS_DEBUG": "1"}, None) is True
    assert resolve_debug(False, {"HARNESS_DEBUG": "0"}, None) is False


def test_conf_enables():
    assert resolve_debug(False, {}, True) is True


def test_default_off():
    assert resolve_debug(False, {}, None) is False
    assert resolve_debug(False, {}, False) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_debug_flag.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'harness.debug_flag'`

- [ ] **Step 3: Write the resolver**

```python
# harness/debug_flag.py
"""Resolve the --debug gate with one precedence rule, shared by both
entrypoints: CLI flag > HARNESS_DEBUG env > done.conf [harness] debug > off."""

from __future__ import annotations

from typing import Mapping


def resolve_debug(flag: bool, env: Mapping[str, str], conf_debug: bool | None) -> bool:
    if flag:
        return True
    if env.get("HARNESS_DEBUG") == "1":
        return True
    return bool(conf_debug)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_debug_flag.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Wire `--debug` into `acp_main.py`**

In `harness/acp_main.py`, after the `--persona` argument (~line 74), add:

```python
    parser.add_argument("--debug", action="store_true",
                        help="emit a JSONL trace of the dn↔agent loop (relayed to the TUI)")
```

After `args = parser.parse_args(argv)` resolve it (env/done.conf checked here too so the standalone `acp_main` path honors them):

```python
    from harness import config
    from harness.debug_flag import resolve_debug
    try:
        conf_debug = config.harness_debug()        # added in Step 7; returns bool | None
    except Exception:
        conf_debug = None
    debug = resolve_debug(args.debug, os.environ, conf_debug)
```

Pass it into the agent constructor (~line 140):

```python
    agent = HarnessAgent(
        model_factory=_model_factory(args.model),
        agent_cfg=_load_agent_cfg(),
        skills_dir=roots,
        router=Router(complete_fn, catalog=skills.load_catalog(roots)),
        worker_model_id=worker_model_id,
        yolo=args.yolo,
        backend=args.model,
        workspace_dir=workspace_dir,
        debug=debug,
    )
```

- [ ] **Step 6: Wire `--debug` into `tui_main.py`**

In `harness/tui_main.py`, add the argument next to `--persona` (~line 85):

```python
    parser.add_argument("--debug", action="store_true",
                        help="write a JSONL trace of this run to harness/runs/<ts>/trace.jsonl")
```

Resolve and pass through to the agent subprocess argv (~line 111, where `agent_cmd` is built) and to the app (~line 116):

```python
    from harness.debug_flag import resolve_debug
    from harness import config as _cfg
    try:
        _conf_debug = _cfg.harness_debug()
    except Exception:
        _conf_debug = None
    debug = resolve_debug(args.debug, os.environ, _conf_debug)
    ...
    if debug:
        agent_cmd.append("--debug")   # the subprocess relays trace payloads when set
    app = HarnessTui(agent_cmd=agent_cmd, cwd=cwd, model=backend,
                     worker_model_id=worker_model_id, version=VERSION,
                     yolo=resolved_yolo, persona=args.persona, debug=debug)
```

> Match the EXACT existing `HarnessTui(...)` call — copy its current kwargs and add `debug=debug`. Do not guess other kwarg names.

- [ ] **Step 7: Add the `done.conf` reader**

In `harness/config.py`, add a reader mirroring the existing accessors (find an existing `def yolo_pinned(` for the pattern):

```python
def harness_debug() -> bool | None:
    """Return the [harness] debug flag from done.conf, or None if unset."""
    conf = _load()                      # use whatever the module's existing loader is named
    section = conf.get("harness", {}) if isinstance(conf, dict) else {}
    val = section.get("debug")
    return val if isinstance(val, bool) else None
```

> **Implementer:** open `harness/config.py`, find how `yolo_pinned` loads the TOML (the loader function + the dict shape), and mirror it EXACTLY. The pseudocode above is the contract, not the literal code — adapt to the real loader name.

- [ ] **Step 8: Run the full suite (no regressions)**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS (all existing tests still green; new flag is inert until Tasks 3–4 use it)

- [ ] **Step 9: Commit**

```bash
git add harness/debug_flag.py tests/test_debug_flag.py harness/acp_main.py harness/tui_main.py harness/config.py
git commit -m "feat(trace): --debug flag resolution (flag>env>done.conf) across both entrypoints"
```

---

### Task 3: Agent-side relay — boundary events

**Files:**
- Modify: `harness/acp_agent.py` (`__init__` ~line 32; the boundary emit sites)
- Modify: `harness/acp_emit.py` (add a `trace_meta` helper)
- Test: `tests/test_trace_relay.py`

**Interfaces:**
- Consumes: `HarnessAgent(debug=...)` (from Task 2).
- Produces:
  - In `acp_emit.py`: `def trace_event(type: str, **data) -> dict` returning `{"type": type, "data": data}` — the relay payload shape the TUI unpacks in Task 4.
  - The agent, when `self._debug`, attaches `{"trace": trace_event(...)}` onto an empty `message_chunk("")` via the existing `with_meta()` at each boundary.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trace_relay.py
from harness.acp_emit import trace_event, with_meta, message_chunk


def test_trace_event_shape():
    ev = trace_event("tx.prompt", sid="s1", turn=1)
    assert ev == {"type": "tx.prompt", "data": {"sid": "s1", "turn": 1}}


def test_with_meta_carries_trace():
    upd = with_meta(message_chunk(""), {"trace": trace_event("llm.call", n_calls=1)})
    harness_meta = upd.field_meta["harness"]
    assert harness_meta["trace"]["type"] == "llm.call"
    assert harness_meta["trace"]["data"] == {"n_calls": 1}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_trace_relay.py -q`
Expected: FAIL with `ImportError: cannot import name 'trace_event'`

- [ ] **Step 3: Add `trace_event` to `acp_emit.py`**

Append to `harness/acp_emit.py`:

```python
def trace_event(type: str, **data) -> dict:
    """Relay payload for the --debug trace: the TUI unpacks
    field_meta['harness']['trace'] and writes it with source='agent'."""
    return {"type": type, "data": data}
```

- [ ] **Step 4: Run the relay-shape test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_trace_relay.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Accept `debug` in `HarnessAgent.__init__`**

In `harness/acp_agent.py`, add the parameter to `__init__` (~line 32) and store it:

```python
    def __init__(self, *, model_factory, agent_cfg, skills_dir: list[Path], router: Router,
                 worker_model_id, yolo: bool = False, backend: str = "vibeproxy",
                 workspace_dir: Path | None = None, debug: bool = False):
        ...
        self._debug = debug
```

Also thread `debug` through `build_harness_agent` (~line 450) so the factory keeps parity:

```python
def build_harness_agent(*, model_factory, agent_cfg, skills_dir, router,
                        worker_model_id=None, workspace_dir=None, debug=False):
    return HarnessAgent(..., workspace_dir=workspace_dir, debug=debug)
```

- [ ] **Step 6: Add a relay helper + emit boundary events**

In `harness/acp_agent.py`, import `trace_event` (add to the existing `from harness.acp_emit import ...` line) and add a small async helper on the agent:

```python
    async def _trace(self, session_id, type, **data):
        """Relay one trace event to the TUI sole-writer, only when --debug.
        Rides the existing with_meta channel; a no-op (and zero wire bytes)
        when debug is off, preserving the byte-identical-wire invariant."""
        if not self._debug:
            return
        await self._conn.session_update(
            session_id, with_meta(message_chunk(""), {"trace": trace_event(type, **data)}))
```

Call `await self._trace(...)` at the boundary points in `prompt()`:
- right after classification: `await self._trace(session_id, "task.classified", sid=session_id, task_type=cls.task_type, skills=cls.skills, confidence=cls.confidence)`
- on the clarify branch: `await self._trace(session_id, "clarify", sid=session_id, question=q)`
- on the chat branch return: `await self._trace(session_id, "chat.done", sid=session_id)`
- after the agent-path engine returns: `await self._trace(session_id, "run.finished", sid=session_id, stop_reason=stop_reason)`

> These are EXTRA emits; do not remove or alter the existing `with_meta` chip emits (task_classified/persona/skill_load). The trace is additive.

- [ ] **Step 7: Run the full suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS (all green; the no-op tests in tests/test_*persona*/no-op still pass because `_trace` is gated on `self._debug` which defaults False)

- [ ] **Step 8: Commit**

```bash
git add harness/acp_emit.py harness/acp_agent.py tests/test_trace_relay.py
git commit -m "feat(trace): agent relays boundary events over with_meta when --debug"
```

---

### Task 4: TUI writes the relayed + dn-side events

**Files:**
- Modify: `harness/tui/app.py` (`__init__` ~line 80; `on_session_update` ~line 814; `action_cancel` ~line 901; `_send_prompt`; permission resolve in `on_permission_request` ~line 887; teardown)
- Test: `tests/test_tui_trace_write.py`

**Interfaces:**
- Consumes: `DebugTracer`/`make_tracer` (Task 1), `field_meta["harness"]["trace"]` relay payload (Task 3), `HarnessTui(debug=...)` (Task 2).
- Produces: a `harness/runs/<ts>/trace.jsonl` containing interleaved `source:"dn"` and `source:"agent"` events for a run.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tui_trace_write.py
import json
from pathlib import Path

from harness.debug_trace import DebugTracer


class _FakeUpdate:
    def __init__(self, harness_meta):
        self.field_meta = {"harness": harness_meta}


def _extract_agent_trace(tracer, update):
    """Mirror the app's on_session_update trace hook in isolation."""
    meta = getattr(update, "field_meta", None)
    if isinstance(meta, dict):
        tr = (meta.get("harness") or {}).get("trace")
        if isinstance(tr, dict):
            tracer.emit("agent", tr["type"], **tr.get("data", {}))


def test_relayed_agent_event_is_written(tmp_path):
    t = DebugTracer.open(tmp_path)
    upd = _FakeUpdate({"trace": {"type": "llm.call", "data": {"n_calls": 1}}})
    _extract_agent_trace(t, upd)
    t.close()
    rows = [json.loads(l) for l in (tmp_path / "trace.jsonl").read_text().splitlines()]
    assert rows[0]["source"] == "agent"
    assert rows[0]["type"] == "llm.call"
    assert rows[0]["data"] == {"n_calls": 1}
```

> This test pins the extraction CONTRACT (the helper the app will call). Step 4 puts the same logic in the app; the test guards the shape so a refactor can't silently break it.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tui_trace_write.py -q`
Expected: PASS for the helper-in-test... so instead assert against the REAL app helper. Rewrite the import to use the app's extractor:

Replace `_extract_agent_trace` with an import once Step 4 exists:
```python
from harness.tui.app import extract_agent_trace
```
Run again — Expected: FAIL with `ImportError: cannot import name 'extract_agent_trace'` (proving the test now binds the real code).

- [ ] **Step 3: Construct the tracer in the app**

In `harness/tui/app.py` `__init__` (~line 82), add `debug: bool = False` to the signature and store it; defer file creation to first connect (so a run that never sends never makes a file):

```python
    def __init__(self, agent_cmd, cwd, model, worker_model_id=None, version="0.5.0",
                 yolo=False, persona=None, debug=False):
        ...
        self._debug = debug
        self._tracer = None   # opened lazily in _connect / first send
```

In `_connect` (find it ~line 183 where `spawn_agent_process` is called), open the tracer once:

```python
        if self._debug and self._tracer is None:
            import time as _time
            from harness import paths
            from harness.debug_trace import make_tracer
            run_dir = paths.runs_dir() / _time.strftime("%Y%m%d-%H%M%S")
            self._tracer = make_tracer(True, run_dir)
        elif self._tracer is None:
            from harness.debug_trace import NullTracer
            self._tracer = NullTracer()
```

> **Implementer:** confirm `harness/paths.py` exposes a `runs_dir()` (the CLI's `run_traced.py` writes `runs/<ts>/` — find how it builds that path and reuse it; if it's a literal `Path("harness/runs")`, add a `runs_dir()` helper to `paths.py` returning that, and have `run_traced.py` use it too — DRY).

- [ ] **Step 4: Add the extraction helper + write on every update**

In `harness/tui/app.py`, add a module-level function (so the test can import it):

```python
def extract_agent_trace(tracer, update) -> None:
    """If `update` carries a relayed trace payload, write it with source='agent'."""
    meta = getattr(update, "field_meta", None)
    if isinstance(meta, dict):
        tr = (meta.get("harness") or {}).get("trace")
        if isinstance(tr, dict) and "type" in tr:
            tracer.emit("agent", tr["type"], **(tr.get("data") or {}))
```

At the TOP of `on_session_update` (right after the gen/session freshness guards, ~line 824), call it and record the dn-side receipt:

```python
        if self._tracer is not None:
            extract_agent_trace(self._tracer, msg.update)
            self._tracer.emit("dn", "rx.update", sid=msg.session_id,
                              kind=type(msg.update).__name__)
```

> Place this BEFORE the early `return` for `stream_reset` (line ~833) so trace events on empty-chunk updates are still recorded.

- [ ] **Step 5: Emit dn-side TX + permission events**

Find `_send_prompt` (grep `def _send_prompt` in `app.py`). Right where the prompt text is sent to the agent, add:

```python
        if self._tracer is not None:
            self._tracer.emit("dn", "tx.prompt", sid=self._session_id, text=text)
```

In `action_cancel` (~line 901), before/after `await self._conn.cancel(...)`:

```python
        if self._tracer is not None:
            self._tracer.emit("dn", "tx.cancel", sid=self._session_id)
```

In `on_permission_request`'s `_resolve` (~line 890), record the decision:

```python
        def _resolve(chosen) -> None:
            self._pending_perm = None
            if self._tracer is not None:
                self._tracer.emit("dn", "perm", command=command,
                                  decision="allowed" if chosen else "denied")
            if not msg.future.done():
                msg.future.set_result(chosen)
```

> `command` is defined just below in the current code (`title[2:]...`). Move the `command = ...` line ABOVE `_resolve`'s definition so it's in scope, or recompute inside `_resolve`. Keep it simple: hoist the two `title`/`command` lines above `_resolve`.

- [ ] **Step 6: Close the tracer on teardown**

Find `_teardown` (grep `def _teardown`). Add, before the connection is torn down:

```python
        if self._tracer is not None:
            self._tracer.close()
            self._tracer = None
```

- [ ] **Step 7: Run the trace-write test + full suite**

Run: `.venv/bin/python -m pytest tests/test_tui_trace_write.py tests/ -q`
Expected: PASS (extraction test green; no regressions)

- [ ] **Step 8: Commit**

```bash
git add harness/tui/app.py harness/paths.py tests/test_tui_trace_write.py
git commit -m "feat(trace): TUI writes relayed agent events + dn-side tx/cancel/perm"
```

---

### Task 5: Inner-loop relay — the real `TracingAgent` event stream

**Key discovery:** `harness/tracing_agent.py` ALREADY emits a complete structured stream to its `emitter`: `run.started`, `llm.call` (n, n_messages), `llm.return` (n, cost, n_actions, content_preview), `action` (command), `action.done` (returncode, output_bytes), `run.finished` (ok, exit_status, n_calls, total_cost, elapsed_s). Today `acp_agent.py:412` pipes all of it to `Emitter("/dev/null", …)`. The firehose the user wants is to **stop discarding this** — replace the `/dev/null` emitter with one that relays each event over `with_meta`.

**Files:**
- Create: `harness/relay_emitter.py`
- Modify: `harness/acp_agent.py` (`run_engine` ~line 409–412; pass `loop`/`session_id`/`conn` into it)
- Test: `tests/test_relay_emitter.py`

**Interfaces:**
- Consumes: `harness.events.Emitter` / `Event`, `harness.acp_emit.trace_event` + `with_meta` + `message_chunk`, the running event `loop`, the agent `conn`, `session_id`, `self._debug`.
- Produces:
  - `class RelayEmitter(Emitter)` whose `write_event(event)` ALSO marshals the event to the loop as a relayed trace payload. Subclasses `Emitter` so `TracingAgent` (which calls `emit`/`set_clock`) needs no change. Still writes to a sink path (use `/dev/null`) so the parent contract holds; the relay is the added behavior.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_relay_emitter.py
"""RelayEmitter forwards every TracingAgent event to a sink callback in the
with_meta trace shape, while remaining a drop-in Emitter."""
from harness.relay_emitter import RelayEmitter


def test_relay_emitter_forwards_events():
    seen = []
    em = RelayEmitter("/dev/null", clock=lambda: 0.0, relay=lambda ev: seen.append(ev))
    em.emit("llm.call", n=1, n_messages=4)
    em.emit("action", command="pytest -q")
    assert [s["type"] for s in seen] == ["llm.call", "action"]
    assert seen[0]["data"] == {"n": 1, "n_messages": 4}
    assert seen[1]["data"] == {"command": "pytest -q"}


def test_relay_emitter_is_an_emitter():
    from harness.events import Emitter
    em = RelayEmitter("/dev/null", clock=lambda: 0.0, relay=lambda ev: None)
    assert isinstance(em, Emitter)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_relay_emitter.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'harness.relay_emitter'`

- [ ] **Step 3: Write `RelayEmitter`**

```python
# harness/relay_emitter.py
"""RelayEmitter: a drop-in Emitter that ALSO forwards each event to a relay
callback (used by the agent to push TracingAgent's event stream to the TUI over
ACP). Subclassing Emitter means TracingAgent — which only calls emit()/set_clock()
— needs no change; we just stop sending its events to /dev/null."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from harness.events import Emitter, Event


class RelayEmitter(Emitter):
    def __init__(self, jsonl_path: str | Path, *, clock, relay: Callable[[dict], None],
                 console: bool = False):
        super().__init__(jsonl_path, clock=clock, console=console)
        self._relay = relay

    def write_event(self, event: Event) -> None:
        super().write_event(event)            # keep the file/console sink behavior
        try:
            self._relay({"type": event.type, "data": dict(event.data)})
        except Exception:                      # observation must never abort the observed
            pass
```

- [ ] **Step 4: Run the relay-emitter test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_relay_emitter.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Use `RelayEmitter` in `run_engine` when `--debug`**

In `harness/acp_agent.py`, `run_engine` (~line 409) currently does:

```python
            emitter = Emitter("/dev/null", clock=lambda: 0.0, console=False)  # ACP carries the stream
```

Replace with a debug-gated choice. `run_engine` is a closure inside `_run_agent_turn`, so `loop`, `session_id`, and `self` are in scope. Build a relay that marshals each event to the loop as a trace payload:

```python
            from harness.events import Emitter
            if self._debug:
                from harness.relay_emitter import RelayEmitter
                def _relay(ev):
                    # ev == {"type","data"}; push it to the TUI sole-writer over ACP.
                    upd = with_meta(message_chunk(""),
                                    {"trace": {"type": ev["type"],
                                               "data": {"sid": session_id, **ev["data"]}}})
                    asyncio.run_coroutine_threadsafe(
                        self._conn.session_update(session_id, upd), loop).result()
                emitter = RelayEmitter("/dev/null", clock=lambda: 0.0, relay=_relay)
            else:
                emitter = Emitter("/dev/null", clock=lambda: 0.0, console=False)
```

> `with_meta` and `message_chunk` are already imported at the top of `acp_agent.py`. The relay reuses the SAME `{"trace": {"type","data"}}` shape the TUI already unpacks (Task 4's `extract_agent_trace`), so no TUI change is needed — `llm.call`, `llm.return`, `action`, `action.done`, `run.started`, `run.finished` all flow through automatically with their existing payloads. This delivers the firehose (real LLM-call + action events with cost/returncode/preview) by REUSING the stream that was being discarded.

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS (all green; the `else` branch is byte-identical to today, so non-debug behavior is unchanged)

- [ ] **Step 7: Commit**

```bash
git add harness/relay_emitter.py harness/acp_agent.py tests/test_relay_emitter.py
git commit -m "feat(trace): relay TracingAgent's full event stream over ACP when --debug"
```

---

### Task 6: Textual devtools + docs + manual smoke

**Files:**
- Modify: `pyproject.toml` (dev dependencies)
- Modify: `harness/tui/app.py` (replace any `print(` with `self.log(`)
- Create: `docs/debugging.md`

**Interfaces:**
- Consumes: everything prior.
- Produces: a documented two-terminal `textual console` workflow + the trace-file workflow; `textual-dev` available.

- [ ] **Step 1: Add `textual-dev` to dev dependencies**

In `pyproject.toml`, find the dev/optional dependency group (look for `[project.optional-dependencies]` or a `dev = [...]` / tool.uv dev group). Add:

```toml
textual-dev>=1.7
```

> Match the file's existing version-pin style (e.g. `textual>=8,<9`). If unsure of an upper bound, use `textual-dev>=1.7` with no upper bound to match looser pins in the file.

- [ ] **Step 2: Replace `print(` with `self.log(` in the TUI**

Run to find them: `grep -rn "print(" harness/tui/`
For each in `harness/tui/app.py` that runs inside the App, replace `print(x)` with `self.log(x)`. (Leave any `print` that is genuinely pre-App startup, but there should be none in the App body.)

- [ ] **Step 3: Run the full suite (no regressions)**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS

- [ ] **Step 4: Manual smoke — trace file is produced**

Run the TUI against the mock model with `--debug`, send one prompt, quit:

```bash
.venv/bin/python harness/tui_main.py --model mock --debug --cwd .
# (send a prompt like "list files", then quit with Ctrl+C / the quit binding)
ls -t harness/runs/*/trace.jsonl | head -1
```

Expected: a `trace.jsonl` exists. Inspect it:

```bash
.venv/bin/python -c "import json,sys,glob; p=sorted(glob.glob('harness/runs/*/trace.jsonl'))[-1]; [print(json.loads(l)['source'], json.loads(l)['type']) for l in open(p)]"
```

Expected output includes interleaved `dn tx.prompt`, `agent task.classified`, `dn rx.update`, etc. Confirm BOTH `dn` and `agent` sources appear (proves the relay round-trips).

- [ ] **Step 5: Manual smoke — no-op when off**

```bash
COUNT_BEFORE=$(ls harness/runs/ 2>/dev/null | wc -l)
.venv/bin/python harness/tui_main.py --model mock --cwd .   # NO --debug; send a prompt, quit
COUNT_AFTER=$(ls harness/runs/ 2>/dev/null | wc -l)
echo "before=$COUNT_BEFORE after=$COUNT_AFTER"
```

Expected: `before == after` (no new run dir created when `--debug` is off).

- [ ] **Step 6: Write `docs/debugging.md`**

```markdown
# Debugging the harness

## Trace file (`--debug`)

Run with `--debug` (or `export HARNESS_DEBUG=1`, or set `[harness] debug = true`
in done.conf) to write a unified JSONL trace of the run to
`harness/runs/<timestamp>/trace.jsonl`.

Each line is one event: `{seq, t, source, type, data}` where `source` is `dn`
(the TUI) or `agent` (relayed from the agent subprocess over ACP). The file is
time-ordered across both processes — read it top to bottom as one conversation,
or hand it to a model: "here's the trace, find the bug."

Filter one session/turn with `grep`/`jq`:

    jq -c 'select(.data.sid=="<sid>")' harness/runs/<ts>/trace.jsonl

Event types: `tx.prompt`, `tx.cancel`, `rx.update`, `perm` (dn);
`task.classified`, `llm.call`, `action`, `tool.start`, `run.finished` (agent).
Reserved for the future cron model: `cron.fire`, `cron.tick`, `cron.error`.

## Live TUI console (`textual console`)

The trace file covers the agent. To watch the **TUI** side live (widget events,
`self.log(...)` output) without corrupting the screen:

    # terminal A
    .venv/bin/textual console
    # terminal B
    .venv/bin/textual run --dev harness.tui_main:main

`textual console` only sees the TUI process (the agent's stdout is the ACP
wire), so use it together with the trace file, not instead of it.
```

- [ ] **Step 7: Final full suite + commit**

```bash
.venv/bin/python -m pytest tests/ -q
git add pyproject.toml harness/tui/app.py docs/debugging.md
git commit -m "feat(trace): textual-dev console + debugging docs; self.log over print"
```

---

## Self-Review notes (filled at write time)

- **Spec coverage:** sink/file (Task 1), one-writer relay (Tasks 3–4), gating flag>env>done.conf (Task 2), firehose inner loop via the real `TracingAgent` stream (Task 5), textual devtools + docs (Task 6), cron vocabulary (documented in Task 6 docs + reserved in schema; no code). ✅
- **Firehose delivered:** Task 5 reuses `TracingAgent`'s existing event stream (`llm.call`/`llm.return` with cost+preview, `action`/`action.done` with returncode+bytes, `run.started`/`run.finished`) by swapping the discarded `/dev/null` emitter for a `RelayEmitter`. `content_preview` is 120 chars (TracingAgent's existing cap), not the full body — if a future need requires full prompt/response text, widen the preview in `tracing_agent.py:135`; that's a one-line change in a separate PR and is the only payload-truncation in the trace. Action output bodies stream live via the existing tool_call_done path and `output_bytes` is recorded; full action output text is in the tool-call updates already rendered.
- **Type consistency:** `trace_event(type, **data) -> {"type","data"}` used identically in Tasks 3/4; the relay payload shape `{"trace": {"type","data"}}` is identical in Tasks 3 (boundary), 5 (inner loop), and unpacked once by `extract_agent_trace` in Task 4; `tracer.emit(source, type, **data)` identical in Tasks 1/4; `resolve_debug(flag, env, conf)` identical in Task 2 sites. ✅
