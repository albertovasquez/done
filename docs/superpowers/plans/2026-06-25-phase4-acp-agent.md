# Phase 4 — ACP Agent — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the engine a full Agent Client Protocol (ACP) agent — a JSON-RPC-over-stdio server that editor/TUI clients (Zed, our smoke client, later Toad) launch and drive — reusing Phases 0–3 unchanged via an interface-wrapping adapter.

**Architecture:** New adapter layer on the official `acp` SDK. The load-bearing seam is `AcpEnvironment(LocalEnvironment)`, whose `execute()` gets the engine's FULL command output and injects pre-exec permission + a cancel checkpoint (the lossy Phase-0 events can't provide these). `HarnessAgent(acp.Agent)` runs the Router/skills/agent per `session/prompt` turn, streaming `session/update`s; Router/ChatHandler/agent run in an executor so the async loop stays responsive. `events.jsonl` still flows as a separate sink.

**Tech Stack:** Python ≥3.10 (`.venv` 3.11), `agent-client-protocol` SDK (imports as `acp`, v0.10.1), asyncio, pytest. Reuses Router (P2), skills (P3), MiniSweAgentRunner/TracingAgent (P1), LocalEnvironment (subclassed).

## Global Constraints

- **Zero upstream edits.** Nothing under `upstream/` changes. `LocalEnvironment` is *subclassed* (`AcpEnvironment`), not edited.
- **Phases 0–3 untouched.** `router.py`, `skills.py`, `runner.py`, `tracing_agent.py`, `events.py`, `chat_handler.py` are not modified. All existing tests stay green.
- **Run tests as** `.venv/bin/python -m pytest tests/<file> -v` — scope to `tests/`, NEVER bare `pytest` (it walks `upstream/tests/` and errors on optional deps).
- **Dependency:** `agent-client-protocol` (imports as `acp`), installed in `.venv`. NOT `acp-sdk` (a different protocol — name collision).
- **STDOUT IS THE WIRE.** Under the ACP agent nothing may write to stdout except JSON-RPC. Set `os.environ["MSWEA_SILENT_STARTUP"]="1"` BEFORE importing `minisweagent`; construct `Emitter(console=False)`; never `echo=print`. (LocalEnvironment pipes subprocess stdout — local.py:82-83 — so command output is safe.)
- **A turn never crashes the process.** Every `prompt()` resolves with a `stop_reason` (or a JSON-RPC error), never an unhandled exception.
- **Cancel is best-effort at command boundaries** (Phase-1 runner has no cooperative cancel). Do NOT claim instant cancellation.
- **Test style:** pytest, no mocking the SDK, real files via `tmp_path`, non-trivial assertions, reuse `build_mock_model` for offline integration tests.

## Verified SDK API (introspected from installed v0.10.1 — use these EXACT names)

- Subclass `acp.Agent`; override: `initialize(self, protocol_version, client_capabilities=None, client_info=None, **kw) -> InitializeResponse`, `new_session(self, cwd, additional_directories=None, mcp_servers=None, **kw) -> NewSessionResponse`, `prompt(self, prompt, session_id, message_id=None, **kw) -> PromptResponse`, `cancel(self, session_id, **kw) -> None`, `load_session(self, cwd, session_id, ... ) -> LoadSessionResponse | None`.
- The agent receives a `Client` handle via `on_connect(self, conn)`. Call back: `await conn.session_update(session_id, update)`, `await conn.request_permission(...)`, `await conn.read_text_file(...)`, `await conn.write_text_file(...)`, `await conn.create_terminal(...)`.
- Builders (module-level in `acp`): `start_tool_call(tool_call_id, title, *, kind=None, status=None, content=None, ...) -> ToolCallStart`, `update_tool_call(tool_call_id, *, status=None, content=None, ...) -> ToolCallProgress`, `tool_content(block) -> ContentToolCallContent`, `update_agent_message_text(text) -> AgentMessageChunk`, `text_block(text) -> TextContentBlock`.
- `_meta` is the `field_meta` attribute on every schema model (e.g. `chunk.field_meta = {...}`).
- Responses: `InitializeResponse(protocol_version=, agent_capabilities=)`, `NewSessionResponse(session_id=)`, `PromptResponse(stop_reason=)`. `acp.PROTOCOL_VERSION == 1`.
- `ClientCapabilities` has `.fs` and `.terminal` (gate L3). `AgentCapabilities(load_session=True)` (gate L4).
- Entrypoint: `await acp.run_agent(agent_instance)` (stdio loop). Smoke client: `acp.spawn_agent_process(to_client, command, *args, cwd=, env=)` → async-yields `(connection, process)`.
- Prompt blocks: `TextContentBlock` with `.text` (also Image/Audio/Resource variants).

---

## File Structure

- `tests/test_acp_sdk_contract.py` (NEW) — pins the SDK API exists (Task 1).
- `trace/acp_emit.py` (NEW) — pure ACP update builders (Task 2).
- `trace/acp_env.py` (NEW) — `AcpEnvironment(LocalEnvironment)`, the seam (Task 3).
- `trace/acp_session.py` (NEW) — `SessionStore`/`SessionState` (Task 4).
- `trace/acp_agent.py` (NEW) — `HarnessAgent(acp.Agent)` (Task 5, the integration).
- `trace/acp_main.py` (NEW) — stdio entrypoint (Task 6).
- `tests/test_acp_smoke.py` (NEW) — smoke client + integration (Task 6).
- Layers 2–4 extend `acp_env.py`/`acp_agent.py` (Tasks 7–9).
- `README.md`, `docs/learning-log.md` (Task 10).

---

## Task 1: SDK contract test (install + pin the API)

**Files:**
- Create: `tests/test_acp_sdk_contract.py`

**Interfaces:**
- Produces: confirmation the `acp` API the plan relies on exists. No code consumed by later tasks beyond "the SDK is installed."

- [ ] **Step 1: Install the SDK into the venv**

Run: `.venv/bin/pip install "agent-client-protocol"`
Expected: installs `acp` (v0.10.x). (Already installed during planning; this makes it explicit/reproducible.)

- [ ] **Step 2: Write the contract test**

```python
import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

import inspect
import acp


def test_acp_api_surface_exists():
    # the symbols the Phase-4 adapter depends on
    for name in ["Agent", "Client", "run_agent", "spawn_agent_process",
                 "start_tool_call", "update_tool_call", "tool_content",
                 "update_agent_message_text", "text_block", "PROTOCOL_VERSION"]:
        assert hasattr(acp, name), f"acp missing {name}"
    assert acp.PROTOCOL_VERSION == 1

    # Agent hooks we override, with the param names we use
    init_sig = inspect.signature(acp.Agent.initialize)
    assert "protocol_version" in init_sig.parameters
    prompt_sig = inspect.signature(acp.Agent.prompt)
    assert {"prompt", "session_id"} <= set(prompt_sig.parameters)
    assert "session_id" in inspect.signature(acp.Agent.cancel).parameters

    # _meta channel: field_meta exists on the chunk model we tag
    from acp.schema import AgentMessageChunk
    assert "field_meta" in AgentMessageChunk.model_fields


def test_not_the_wrong_acp_package():
    # guard the name collision: this must be Zed's ACP, which exposes Agent+run_agent
    assert hasattr(acp, "Agent") and hasattr(acp, "run_agent")
```

- [ ] **Step 3: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_acp_sdk_contract.py -v`
Expected: PASS (2 tests). If FAIL on import, the SDK isn't installed — redo Step 1.

- [ ] **Step 4: Commit**

```bash
git add tests/test_acp_sdk_contract.py
git commit -m "test(acp): pin agent-client-protocol SDK API surface"
```

---

## Task 2: ACP update builders — `trace/acp_emit.py`

**Files:**
- Create: `trace/acp_emit.py`
- Test: `tests/test_acp_emit.py`

**Interfaces:**
- Consumes: `acp` builders.
- Produces:
  - `tool_call_start(tool_call_id: str, command: str) -> ToolCallStart`
  - `tool_call_done(tool_call_id: str, output: dict) -> ToolCallProgress` (output is the LocalEnvironment dict `{output, returncode, exception_info}`; status `completed` if returncode==0 else `failed`; full `output` text as content)
  - `message_chunk(text: str) -> AgentMessageChunk`
  - `with_meta(update, harness_meta: dict)` — attaches `harness_meta` to `update.field_meta` under key `"harness"`, returns the update

- [ ] **Step 1: Write the failing tests**

```python
import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

from trace.acp_emit import tool_call_start, tool_call_done, message_chunk, with_meta


def test_tool_call_start_is_pending_execute():
    tc = tool_call_start("tc1", "ls -la")
    assert tc.tool_call_id == "tc1"
    assert tc.status == "pending"
    assert tc.kind == "execute"
    assert "ls -la" in tc.title


def test_tool_call_done_completed_carries_full_output():
    out = {"output": "hello\nworld", "returncode": 0, "exception_info": ""}
    tc = tool_call_done("tc1", out)
    assert tc.tool_call_id == "tc1"
    assert tc.status == "completed"
    # full output present (not truncated)
    rendered = str(tc.content)
    assert "hello\nworld" in rendered


def test_tool_call_done_failed_on_nonzero():
    out = {"output": "boom", "returncode": 1, "exception_info": "x"}
    assert tool_call_done("tc1", out).status == "failed"


def test_message_chunk_carries_full_text():
    big = "x" * 5000                      # longer than the 120-char event preview
    chunk = message_chunk(big)
    assert big in str(chunk.content)


def test_with_meta_attaches_under_harness_key():
    chunk = with_meta(message_chunk("hi"), {"task_type": "code_fix"})
    assert chunk.field_meta["harness"]["task_type"] == "code_fix"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_acp_emit.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'trace.acp_emit'`.

- [ ] **Step 3: Write `trace/acp_emit.py`**

```python
"""Pure builders for ACP session/update objects. The only module that knows
ACP's update shapes; acp_env/acp_agent call these. No JSON-RPC, no I/O —
unit-testable in isolation."""

from __future__ import annotations

from typing import Any

from acp import (
    start_tool_call,
    update_tool_call,
    tool_content,
    text_block,
    update_agent_message_text,
)


def tool_call_start(tool_call_id: str, command: str):
    return start_tool_call(tool_call_id, f"$ {command}", kind="execute", status="pending")


def tool_call_done(tool_call_id: str, output: dict):
    status = "completed" if output.get("returncode", -1) == 0 else "failed"
    body = output.get("output", "") or output.get("exception_info", "") or "(no output)"
    return update_tool_call(tool_call_id, status=status,
                            content=[tool_content(text_block(body))])


def message_chunk(text: str):
    return update_agent_message_text(text)


def with_meta(update, harness_meta: dict[str, Any]):
    existing = update.field_meta or {}
    update.field_meta = {**existing, "harness": harness_meta}
    return update
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_acp_emit.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add trace/acp_emit.py tests/test_acp_emit.py
git commit -m "feat(acp): pure session/update builders (tool_call, message, _meta)"
```

---

## Task 3: The seam — `AcpEnvironment(LocalEnvironment)`

**Files:**
- Create: `trace/acp_env.py`
- Test: `tests/test_acp_env.py`

**Interfaces:**
- Consumes: upstream `LocalEnvironment` (subclass), `threading.Event`.
- Produces:
  - `AcpEnvironment(LocalEnvironment)` with ctor kwargs `on_command: Callable[[str, str, dict | None], None]`, `request_permission: Callable[[str], bool] | None = None`, `cancel_flag: threading.Event | None = None`, plus the usual `cwd`/`env`/`timeout` LocalEnvironment kwargs.
  - `execute(self, action, cwd="", *, timeout=None) -> dict` — checks cancel, fires `on_command("start", cmd, None)`, (if `request_permission`) gates before running, runs `super().execute(...)`, fires `on_command("done", cmd, out)`, returns the full output dict. Reject → `on_command("rejected", cmd, None)`, returns a denied dict, does NOT run.

- [ ] **Step 1: Write the failing tests**

```python
import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

import threading
from trace.acp_env import AcpEnvironment


def _env(tmp_path, **kw):
    return AcpEnvironment(cwd=str(tmp_path), **kw)


def test_executes_and_returns_full_output(tmp_path):
    calls = []
    env = _env(tmp_path, on_command=lambda phase, cmd, out: calls.append((phase, cmd, out)))
    result = env.execute({"command": "printf 'abc'"})
    assert result["returncode"] == 0
    assert "abc" in result["output"]                    # FULL output available at the seam
    phases = [c[0] for c in calls]
    assert phases == ["start", "done"]
    assert calls[1][2]["output"] == result["output"]    # done callback carries the full dict


def test_cancel_flag_skips_execution(tmp_path):
    flag = threading.Event(); flag.set()
    ran = []
    env = _env(tmp_path, on_command=lambda *a: ran.append(a), cancel_flag=flag)
    result = env.execute({"command": "printf 'should-not-run'"})
    assert result["returncode"] == -1
    assert "cancel" in result["exception_info"].lower()
    assert ran == []                                    # nothing fired; command never ran


def test_permission_reject_skips_execution(tmp_path):
    calls = []
    env = _env(tmp_path,
               on_command=lambda phase, cmd, out: calls.append(phase),
               request_permission=lambda cmd: False)    # deny
    result = env.execute({"command": "printf 'denied'"})
    assert result["returncode"] == -1
    assert "denied" not in result.get("output", "")     # the command did NOT run
    assert "start" in calls and "rejected" in calls and "done" not in calls


def test_permission_allow_runs(tmp_path):
    env = _env(tmp_path, on_command=lambda *a: None, request_permission=lambda cmd: True)
    assert "ok" in env.execute({"command": "printf 'ok'"})["output"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_acp_env.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'trace.acp_env'`.

- [ ] **Step 3: Write `trace/acp_env.py`**

```python
"""AcpEnvironment: the seam that gives the ACP layer what the lossy Phase-0
events cannot — the FULL command output, a pre-exec permission gate, and a
cancel checkpoint. A subclass of upstream LocalEnvironment (subclass, NOT edit:
honors zero-upstream-edits). The agent runs this on Phase-1's worker thread; the
callbacks marshal to the async ACP loop in acp_agent."""

from __future__ import annotations

import threading
from typing import Any, Callable

from minisweagent.environments.local import LocalEnvironment


class AcpEnvironment(LocalEnvironment):
    def __init__(self, *,
                 on_command: Callable[[str, str, dict | None], None],
                 request_permission: Callable[[str], bool] | None = None,
                 cancel_flag: threading.Event | None = None,
                 **kwargs: Any):
        super().__init__(**kwargs)
        self._on_command = on_command
        self._request_permission = request_permission
        self._cancel_flag = cancel_flag

    def execute(self, action: dict, cwd: str = "", *, timeout: int | None = None) -> dict[str, Any]:
        if self._cancel_flag is not None and self._cancel_flag.is_set():
            return {"output": "", "returncode": -1, "exception_info": "cancelled"}
        command = action.get("command", "")
        self._on_command("start", command, None)
        if self._request_permission is not None and not self._request_permission(command):
            self._on_command("rejected", command, None)
            return {"output": "", "returncode": -1, "exception_info": "permission denied"}
        out = super().execute(action, cwd, timeout=timeout)   # REAL run; FULL output; may raise Submitted
        self._on_command("done", command, out)
        return out
```

NOTE to implementer: `super().execute` may raise `Submitted` (the task-complete
signal) — do NOT catch it here; let it propagate so the agent loop ends normally.
The `on_command("done", ...)` line is therefore skipped on the submit command,
which is correct (the submit echo has no meaningful tool output).

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_acp_env.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add trace/acp_env.py tests/test_acp_env.py
git commit -m "feat(acp): AcpEnvironment seam — full output, permission gate, cancel"
```

---

## Task 4: Session state — `trace/acp_session.py`

**Files:**
- Create: `trace/acp_session.py`
- Test: `tests/test_acp_session.py`

**Interfaces:**
- Produces:
  - `@dataclass SessionState` with `cwd: str`, `cancel_flag: threading.Event` (default-constructed), `history: list[dict]` (default empty).
  - `SessionStore` with `new(cwd: str) -> str` (returns a fresh session_id, registers state), `get(session_id: str) -> SessionState` (raises `KeyError` on unknown), `record(session_id, turn: dict)` (appends to history).

- [ ] **Step 1: Write the failing tests**

```python
import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

import pytest
from trace.acp_session import SessionStore, SessionState


def test_new_and_get_roundtrip(tmp_path):
    store = SessionStore()
    sid = store.new(cwd=str(tmp_path))
    st = store.get(sid)
    assert isinstance(st, SessionState)
    assert st.cwd == str(tmp_path)
    assert st.history == []
    assert not st.cancel_flag.is_set()


def test_unknown_session_raises():
    with pytest.raises(KeyError):
        SessionStore().get("nope")


def test_record_appends_history(tmp_path):
    store = SessionStore(); sid = store.new(cwd=str(tmp_path))
    store.record(sid, {"prompt": "fix it", "stop_reason": "end_turn"})
    assert store.get(sid).history == [{"prompt": "fix it", "stop_reason": "end_turn"}]


def test_ids_are_unique(tmp_path):
    store = SessionStore()
    assert store.new(cwd=str(tmp_path)) != store.new(cwd=str(tmp_path))
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_acp_session.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write `trace/acp_session.py`**

```python
"""Per-session state for the ACP agent: cwd, a cooperative cancel flag, and the
turn history (recorded from Layer 1 so Layer 4 session/load can replay it)."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from uuid import uuid4


@dataclass
class SessionState:
    cwd: str
    cancel_flag: threading.Event = field(default_factory=threading.Event)
    history: list[dict] = field(default_factory=list)


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, SessionState] = {}

    def new(self, cwd: str) -> str:
        session_id = uuid4().hex
        self._sessions[session_id] = SessionState(cwd=cwd)
        return session_id

    def get(self, session_id: str) -> SessionState:
        return self._sessions[session_id]            # KeyError on unknown — caller maps to JSON-RPC error

    def record(self, session_id: str, turn: dict) -> None:
        self._sessions[session_id].history.append(turn)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_acp_session.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add trace/acp_session.py tests/test_acp_session.py
git commit -m "feat(acp): SessionStore/SessionState (cwd, cancel flag, history)"
```

---

## Task 5: The agent — `trace/acp_agent.py` (Layer 1 integration)

**Files:**
- Create: `trace/acp_agent.py`

**Interfaces:**
- Consumes: `acp.Agent`, `acp_emit` (Task 2), `AcpEnvironment` (Task 3), `SessionStore` (Task 4), `Router`/`skills`/`ChatHandler`/`MiniSweAgentRunner`/`build_mock_model` (Phases 1–3), `acp.run_agent`.
- Produces: `HarnessAgent(acp.Agent)` driving a full turn; `build_harness_agent(*, model_factory, agent_cfg, skills_dir, router_complete) -> HarnessAgent` factory.

This is the integration task — no standalone unit test file of its own (it's exercised end-to-end by the smoke test in Task 6, which is where async + subprocess can be driven realistically). The implementer writes it to satisfy Task 6's smoke tests.

- [ ] **Step 1: Write `trace/acp_agent.py`**

```python
"""HarnessAgent: the ACP agent. Per session/prompt turn: classify (Router) →
emit _meta → dispatch chat/ambiguous/agent. The agent loop runs on a worker
thread (via run_in_executor) with an AcpEnvironment whose callbacks marshal
session/update notifications back to the event loop. Router/ChatHandler also run
in the executor so the async loop stays responsive to session/cancel."""

from __future__ import annotations

import asyncio
import functools
from pathlib import Path

import acp

from trace import skills
from trace.acp_emit import tool_call_start, tool_call_done, message_chunk, with_meta
from trace.acp_env import AcpEnvironment
from trace.acp_session import SessionStore
from trace.router import Router, Classification
from trace.chat_handler import ChatHandler
from minisweagent.agents.default import DefaultAgent  # for type only; we build via factory


class HarnessAgent(acp.Agent):
    def __init__(self, *, model_factory, agent_cfg, skills_dir: Path, router: Router,
                 worker_model_id):
        self._model_factory = model_factory
        self._agent_cfg = agent_cfg
        self._skills_dir = skills_dir
        self._router = router
        self._worker_model_id = worker_model_id
        self._store = SessionStore()
        self._conn = None

    def on_connect(self, conn) -> None:
        self._conn = conn

    async def initialize(self, protocol_version, client_capabilities=None,
                         client_info=None, **kw):
        self._client_caps = client_capabilities
        return acp.InitializeResponse(
            protocol_version=acp.PROTOCOL_VERSION,
            agent_capabilities=acp.schema.AgentCapabilities(load_session=True),
        )

    async def new_session(self, cwd, additional_directories=None, mcp_servers=None, **kw):
        return acp.NewSessionResponse(session_id=self._store.new(cwd=cwd))

    async def cancel(self, session_id, **kw) -> None:
        try:
            self._store.get(session_id).cancel_flag.set()
        except KeyError:
            pass

    async def prompt(self, prompt, session_id, message_id=None, **kw):
        loop = asyncio.get_running_loop()
        try:
            state = self._store.get(session_id)
        except KeyError:
            raise acp.RequestError.invalid_params() if hasattr(acp.RequestError, "invalid_params") \
                else ValueError(f"unknown session {session_id}")
        state.cancel_flag.clear()
        text = "".join(getattr(b, "text", "") for b in prompt)

        # 1) classify in the executor (sync litellm call must not block the loop)
        try:
            cls: Classification = await loop.run_in_executor(None, self._router.classify, text)
        except Exception as e:  # router/VibeProxy unreachable
            await self._conn.session_update(session_id,
                message_chunk(f"router unavailable: {e}"))
            return acp.PromptResponse(stop_reason="refusal")

        meta = {"task_type": cls.task_type, "skills": cls.skills,
                "confidence": cls.confidence}
        await self._conn.session_update(session_id,
            with_meta(message_chunk(""), {"task_classified": meta}))

        if cls.needs_clarification or cls.task_type == "ambiguous":
            q = cls.clarifying_question or "Could you clarify the task?"
            await self._conn.session_update(session_id, message_chunk(q))
            self._store.record(session_id, {"prompt": text, "stop_reason": "end_turn",
                                            "kind": "clarify"})
            return acp.PromptResponse(stop_reason="end_turn")

        if cls.task_type == "chat_question":
            handler = ChatHandler(self._worker_model_id)
            answer = await loop.run_in_executor(None, handler.answer, text)
            await self._conn.session_update(session_id, message_chunk(answer))
            self._store.record(session_id, {"prompt": text, "stop_reason": "end_turn",
                                            "kind": "chat"})
            return acp.PromptResponse(stop_reason="end_turn")

        # agent path
        load = skills.compose(self._skills_dir, cls.skills)
        await self._conn.session_update(session_id,
            with_meta(message_chunk(""),
                      {"skill_load": {"injected": load.injected, "skipped": load.skipped}}))
        stop_reason = await self._run_agent_turn(loop, session_id, state, text, load.block)
        self._store.record(session_id, {"prompt": text, "stop_reason": stop_reason,
                                        "kind": "agent"})
        return acp.PromptResponse(stop_reason=stop_reason)

    async def _run_agent_turn(self, loop, session_id, state, text, skill_block) -> str:
        tc_counter = {"n": 0}

        def on_command(phase: str, command: str, out: dict | None) -> None:
            # runs on the worker thread → marshal to the loop and block until sent
            if phase == "start":
                tc_counter["n"] += 1
                state._last_tc_id = f"tc{tc_counter['n']}"          # transient, on the state obj
                upd = tool_call_start(state._last_tc_id, command)
            elif phase in ("done", "rejected"):
                result = out if out is not None else {"output": "permission denied",
                                                      "returncode": -1, "exception_info": ""}
                upd = tool_call_done(getattr(state, "_last_tc_id", "tc0"), result)
            else:
                return
            fut = asyncio.run_coroutine_threadsafe(
                self._conn.session_update(session_id, upd), loop)
            fut.result()

        env = AcpEnvironment(cwd=state.cwd, on_command=on_command,
                             request_permission=None,            # Layer 2 wires this
                             cancel_flag=state.cancel_flag)

        def run_engine() -> str:
            from trace.tracing_agent import TracingAgent
            from trace.events import Emitter
            emitter = Emitter("/dev/null", clock=lambda: 0.0, console=False)  # ACP carries the stream
            cfg = dict(self._agent_cfg)
            agent = TracingAgent(self._model_factory(), env, emitter=emitter,
                                 skill_block=skill_block, **cfg)
            try:
                result = agent.run(text)
                return result.get("exit_status", "end_turn")
            except BaseException:
                return "refusal"

        if state.cancel_flag.is_set():
            return "cancelled"
        exit_status = await loop.run_in_executor(None, run_engine)
        # surface the agent's final assistant text (full content, not the preview event)
        # (the smoke test asserts a tool_call happened; final text is best-effort here)
        if state.cancel_flag.is_set():
            return "cancelled"
        return "end_turn" if exit_status in ("Submitted", "end_turn") else "end_turn"
```

NOTE to implementer: the exact `RequestError` construction for an unknown
session may differ across SDK patch versions — use whatever the installed
`acp.RequestError` provides for "invalid params" (check
`dir(acp.RequestError)`); the fallback `ValueError` is acceptable if no
classmethod exists. Confirm against the installed SDK and adjust this one line;
the smoke test only requires that an unknown session does not crash the process.
Also: `Emitter("/dev/null", ...)` — if opening `/dev/null` is awkward on the
platform, write to a temp path under the session cwd; the point is `console=False`
and that ACP (not the file) carries the client stream.

- [ ] **Step 2: Smoke-import it (no unit test; Task 6 drives it)**

Run: `.venv/bin/python -c "import sys; sys.path[:0]=['upstream/src','.']; import trace.acp_agent; print('import ok')"`
Expected: `import ok` (no syntax/import errors).

- [ ] **Step 3: Commit**

```bash
git add trace/acp_agent.py
git commit -m "feat(acp): HarnessAgent — per-turn route/dispatch over ACP (Layer 1)"
```

---

## Task 6: Entrypoint + smoke client + integration tests

**Files:**
- Create: `trace/acp_main.py`
- Create: `tests/test_acp_smoke.py`

**Interfaces:**
- Consumes: `HarnessAgent` (Task 5), `acp.run_agent`, `acp.spawn_agent_process`.
- Produces: a runnable agent (`python -m trace.acp_main` or `trace/acp_main.py`) and an integration test driving it via a real subprocess.

- [ ] **Step 1: Write `trace/acp_main.py`**

```python
#!/usr/bin/env python3
"""ACP agent entrypoint: serve the harness over JSON-RPC on stdio.

STDOUT IS THE WIRE — set MSWEA_SILENT_STARTUP before importing minisweagent and
never print to stdout. Usage (a client launches this):
  .venv/bin/python trace/acp_main.py [--model mock|vibeproxy]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

os.environ.setdefault("MSWEA_SILENT_STARTUP", "1")   # BEFORE minisweagent import

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "upstream" / "src"))
sys.path.insert(0, str(REPO_ROOT))

import acp  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from trace.acp_agent import HarnessAgent  # noqa: E402
from trace.router import Router, complete  # noqa: E402
from trace import skills  # noqa: E402


def _load_agent_cfg() -> dict:
    import yaml
    cfg = yaml.safe_load((REPO_ROOT / "upstream/src/minisweagent/config/mini.yaml").read_text())
    return cfg["agent"]


def _model_factory(model_choice: str):
    if model_choice == "mock":
        from trace.models_mock import build_mock_model
        return build_mock_model
    def make():
        from minisweagent.models.litellm_model import LitellmModel
        return LitellmModel(
            model_name="openai/" + os.getenv("VIBEPROXY_MODEL", "gpt-5.4"),
            model_kwargs={"api_base": os.getenv("VIBEPROXY_BASE_URL", "http://localhost:8317/v1"),
                          "api_key": os.getenv("VIBEPROXY_API_KEY", "dummy-not-used")},
            cost_tracking="ignore_errors")
    return make


async def _main(argv=None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=["mock", "vibeproxy"], default="mock")
    args = parser.parse_args(argv)
    load_dotenv(REPO_ROOT / ".env")
    worker_model_id = None if args.model == "mock" else os.getenv("VIBEPROXY_MODEL", "gpt-5.4")
    agent = HarnessAgent(
        model_factory=_model_factory(args.model),
        agent_cfg=_load_agent_cfg(),
        skills_dir=REPO_ROOT / "skills",
        router=Router(complete, catalog=skills.load_catalog(REPO_ROOT / "skills")),
        worker_model_id=worker_model_id)
    await acp.run_agent(agent)


if __name__ == "__main__":
    asyncio.run(_main())
```

- [ ] **Step 2: Write the smoke/integration tests**

```python
import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

import asyncio
from pathlib import Path

import pytest
import acp


REPO = Path(__file__).resolve().parent.parent
AGENT_CMD = [str(REPO / ".venv/bin/python"), str(REPO / "trace/acp_main.py"), "--model", "mock"]


class _CollectingClient(acp.Client):
    def __init__(self):
        self.updates = []          # raw receive order
    def on_connect(self, conn): self._conn = conn
    async def session_update(self, session_id, update, **kw):
        self.updates.append(update)
    async def request_permission(self, *a, **kw):
        # default: allow (Layer 1 has no permission anyway)
        raise NotImplementedError


async def _drive(prompt_text, cwd):
    client = _CollectingClient()
    async with _spawn(client) as conn:
        await conn.initialize(protocol_version=acp.PROTOCOL_VERSION)
        new = await conn.new_session(cwd=str(cwd), mcp_servers=[])
        resp = await conn.prompt(
            prompt=[acp.text_block(prompt_text)], session_id=new.session_id)
        return client.updates, resp


def _spawn(client):
    # acp.spawn_agent_process yields (connection, process); wrap as async ctx
    return acp.spawn_agent_process(client, AGENT_CMD[0], *AGENT_CMD[1:])


def test_initialize_and_new_session(tmp_path):
    async def go():
        client = _CollectingClient()
        async with _spawn(client) as conn:
            init = await conn.initialize(protocol_version=acp.PROTOCOL_VERSION)
            assert init.protocol_version == acp.PROTOCOL_VERSION
            new = await conn.new_session(cwd=str(tmp_path), mcp_servers=[])
            assert new.session_id
    asyncio.run(go())


def test_chat_question_does_not_run_agent(tmp_path):
    updates, resp = asyncio.run(_drive("what is 1+1", tmp_path))
    assert resp.stop_reason == "end_turn"
    # task.classified _meta present and chat_question; NO tool_call update
    metas = [u.field_meta for u in updates if getattr(u, "field_meta", None)]
    assert any(m.get("harness", {}).get("task_classified", {}).get("task_type") == "chat_question"
               for m in metas)
    assert not any(type(u).__name__.startswith("ToolCall") for u in updates)
```

NOTE to implementer: `acp.spawn_agent_process` returns an async iterator/context
yielding `(connection, process)`. Adapt `_spawn` to the installed SDK's exact
shape — it may be `async with acp.spawn_agent_process(...) as (conn, proc):` or an
`async for`. Verify with `help(acp.spawn_agent_process)` and the SDK's
`examples/client.py`. The tests' INTENT is fixed (initialize→new_session→prompt,
assert _meta + no tool_call for chat); only the connection boilerplate adapts.
Add the code-fix integration test (asserts skill.load _meta + a ToolCall update +
fix applied in a temp repo) and the cancel test once the connection helper works.
A stdout-purity check: capture the subprocess stdout and assert every line parses
as JSON (no banner/log leaked).

- [ ] **Step 3: Run the smoke tests**

Run: `.venv/bin/python -m pytest tests/test_acp_smoke.py -v`
Expected: PASS. If the connection helper shape is wrong, fix `_spawn` per the SDK
(this is the one place that needs SDK-shape adaptation).

- [ ] **Step 4: Verify the whole suite still green**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all prior tests + the new ACP tests PASS.

- [ ] **Step 5: Commit**

```bash
git add trace/acp_main.py tests/test_acp_smoke.py
git commit -m "feat(acp): stdio entrypoint + smoke client integration tests (Layer 1)"
```

---

## Task 7: Layer 2 — permissions

**Files:**
- Modify: `trace/acp_agent.py` (wire `request_permission` into the `AcpEnvironment`)
- Test: `tests/test_acp_smoke.py` (add a permission test)

**Interfaces:**
- Consumes: `Client.request_permission` (SDK), the `AcpEnvironment.request_permission` hook (already present from Task 3).

- [ ] **Step 1: Write the failing permission test**

Add to `tests/test_acp_smoke.py` a `_CollectingClient` variant whose
`request_permission` returns a REJECT outcome, drive a code-fix prompt, and
assert: a `ToolCall` start appeared but its update is `failed` and the file was
NOT modified.

```python
def test_permission_reject_skips_command(tmp_path):
    # client that rejects every permission request
    # drive a code-fix prompt; assert tool_call_update status == failed and
    # the target file is unchanged.
    ...  # implementer fills in using the connection helper from Task 6
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_acp_smoke.py::test_permission_reject_skips_command -v`
Expected: FAIL (permission not yet wired — command runs anyway).

- [ ] **Step 3: Wire permission in `acp_agent._run_agent_turn`**

Replace `request_permission=None` with a bridge that, when the client advertised
permission support, calls `self._conn.request_permission(...)` from the worker
thread via `run_coroutine_threadsafe(...).result()` and maps the outcome to a
bool:

```python
def request_permission(command: str) -> bool:
    if self._client_caps is None:           # no client perms → auto-allow (standalone)
        return True
    req = acp.RequestPermissionRequest(...)  # build with the command's tool_call + options
    fut = asyncio.run_coroutine_threadsafe(self._conn.request_permission(req), loop)
    outcome = fut.result()
    return _is_allow(outcome)                # selected allow_* → True; reject_*/cancelled → False
env = AcpEnvironment(cwd=state.cwd, on_command=on_command,
                     request_permission=request_permission, cancel_flag=state.cancel_flag)
```

NOTE to implementer: build `RequestPermissionRequest` and read its response using
the installed SDK's exact shapes (`dir(acp.RequestPermissionRequest)`,
`acp.RequestPermissionResponse`; options use `PermissionOptionKind`
allow_once/allow_always/reject_once/reject_always). The `_is_allow` helper checks
the selected option's kind. Keep auto-allow when `self._client_caps` lacks
permission support.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_acp_smoke.py -v`
Expected: PASS (permission test + existing).

- [ ] **Step 5: Commit**

```bash
git add trace/acp_agent.py tests/test_acp_smoke.py
git commit -m "feat(acp): Layer 2 — request_permission before shell actions"
```

---

## Task 8: Layer 3 — fs/terminal delegation

**Files:**
- Modify: `trace/acp_env.py` (capability-gated delegation), `trace/acp_agent.py` (pass client caps + conn)
- Test: `tests/test_acp_env.py` / `tests/test_acp_smoke.py`

**Interfaces:**
- Consumes: `Client.read_text_file`/`write_text_file`/`create_terminal`/`terminal_output` (SDK), `ClientCapabilities.fs`/`.terminal`.

- [ ] **Step 1: Write the failing test**

In `tests/test_acp_smoke.py`, a client that advertises `terminal` capability and
records `create_terminal` calls; drive a code-fix prompt; assert the command was
executed via the client terminal path (the client's `create_terminal` was
called), not LocalEnvironment. And a client WITHOUT the capability → falls back to
LocalEnvironment (command still runs, file changes).

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_acp_smoke.py -k delegation -v`
Expected: FAIL (delegation not implemented).

- [ ] **Step 3: Implement capability-gated delegation in `AcpEnvironment`**

Add an optional `client_terminal: Callable | None` (and `client_fs`) injected by
`acp_agent` only when the client advertised the capability. In `execute()`, when
`client_terminal` is set, route command execution through it (create terminal →
wait for exit → read output → return the same dict shape); otherwise
`super().execute()`. Keep the `on_command` start/done callbacks identical so the
ACP stream is the same regardless of execution path.

```python
# acp_env.py execute(), after the permission gate:
if self._client_terminal is not None:
    out = self._client_terminal(command)        # returns {output, returncode, exception_info}
else:
    out = super().execute(action, cwd, timeout=timeout)
self._on_command("done", command, out)
return out
```

NOTE to implementer: the client terminal flow is create_terminal →
wait_for_terminal_exit → terminal_output → release_terminal; marshal each via
`run_coroutine_threadsafe(...).result()` (we're on the worker thread). Build the
requests with the installed SDK types. fs delegation (read/write_text_file) is
analogous but only matters if the agent reads/writes files directly — our agent
shells out, so terminal is the primary path; implement fs only if a test needs
it, else note it deferred.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_acp_smoke.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trace/acp_env.py trace/acp_agent.py tests/test_acp_smoke.py
git commit -m "feat(acp): Layer 3 — capability-gated fs/terminal delegation"
```

---

## Task 9: Layer 4 — session resume (`session/load`)

**Files:**
- Modify: `trace/acp_agent.py` (implement `load_session`)
- Test: `tests/test_acp_smoke.py`

**Interfaces:**
- Consumes: `SessionState.history` (recorded since Task 5), `Client.session_update`.
- Produces: `HarnessAgent.load_session` replaying history as `session/update`s.

- [ ] **Step 1: Write the failing test**

Drive two prompts in one session (record history), then call `session/load` for
that session_id and assert the agent replays the prior turns as `session/update`
notifications (e.g. an `agent_message_chunk` per recorded turn) and returns a
`LoadSessionResponse`.

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_acp_smoke.py -k load -v`
Expected: FAIL (load_session not implemented / returns None).

- [ ] **Step 3: Implement `load_session`**

```python
async def load_session(self, cwd, session_id, additional_directories=None,
                       mcp_servers=None, **kw):
    try:
        state = self._store.get(session_id)
    except KeyError:
        # unknown session: register an empty one at this cwd
        sid = self._store.new(cwd=cwd)
        return acp.LoadSessionResponse()
    for turn in state.history:
        await self._conn.session_update(
            session_id, message_chunk(f"[resumed] {turn.get('kind','turn')}: {turn.get('prompt','')}"))
    return acp.LoadSessionResponse()
```

NOTE to implementer: confirm `LoadSessionResponse()` construction and whether the
SDK requires `load_session` capability to be advertised (we set
`AgentCapabilities(load_session=True)` in `initialize`, Task 5). Adjust the replay
content shape if a richer per-turn record is available.

- [ ] **Step 4: Run to verify it passes + whole suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add trace/acp_agent.py tests/test_acp_smoke.py
git commit -m "feat(acp): Layer 4 — session/load replays turn history"
```

---

## Task 10: Docs — README + learning-log

**Files:**
- Modify: `README.md` (add ACP setup + run), `docs/learning-log.md` (Phase 4 entry)

- [ ] **Step 1: Update README**

Add an "ACP agent" section: `pip install agent-client-protocol` in setup; how a
client launches `trace/acp_main.py`; note it's the engine-as-server inversion.

- [ ] **Step 2: Append a Phase 4 learning-log entry (~20 lines, log's voice)**

Cover: the architectural inversion (engine = ACP server); the interface-wrapping
seam (`AcpEnvironment`) vs the abandoned event-translation idea (and WHY — lossy
events, no pre-exec hook, no cancel — the Codex catch); `_meta` for our
routing/skills observability; best-effort cancel as an honest limitation; the
async↔sync executor bridge.

- [ ] **Step 3: Run the whole suite once more**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all pass. Record the count.

- [ ] **Step 4: Commit**

```bash
git add README.md docs/learning-log.md
git commit -m "docs: Phase 4 — ACP agent setup + learning-log"
```

---

## Self-Review (completed by plan author)

**Spec coverage:** SDK contract → T1; builders (`acp_emit`) → T2; the seam
(`AcpEnvironment`: full output + permission + cancel) → T3; session state → T4;
`HarnessAgent` + Router-per-turn + executor + `_meta` → T5; entrypoint + smoke
client + stdout purity → T6; Layer 2 permissions → T7; Layer 3 fs/terminal → T8;
Layer 4 resume → T9; docs → T10. The three Codex CRITICALs map to T3 (full
output + permission + cancel seam) and are proven by `tests/test_acp_env.py`. The
two IMPORTANTs map to T5 (executor for Router/Chat/agent) and T6 (stdout purity).

**Placeholder scan:** the integration-test connection boilerplate (T6/T7/T8/T9)
intentionally carries "NOTE to implementer" blocks because the exact
`spawn_agent_process` / `RequestPermissionRequest` / terminal-flow shapes must be
adapted to the installed SDK at implementation time — the test INTENT and
assertions are concrete; only the SDK connection wiring is left to verify against
`help()`/`examples/`. This is honest deferral of SDK-shape details, not vague
work. All pure-logic tasks (T2, T3, T4) have complete code.

**Type consistency:** `AcpEnvironment(on_command, request_permission, cancel_flag)`,
`SessionStore.new/get/record`, `SessionState(cwd, cancel_flag, history)`,
`tool_call_start/tool_call_done/message_chunk/with_meta`, `HarnessAgent` methods
match the verified SDK signatures (`initialize/new_session/prompt/cancel/load_session`)
across tasks.
