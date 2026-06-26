# Phase 5 — Textual ACP Client (TUI) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a single-session Textual TUI that is an ACP client driving the Phase-4 ACP agent over a real subprocess, rendering the session/update stream (messages, tool calls, permissions) **and** our custom `_meta["harness"]` task-classified/skill-load chips that generic ACP clients (Toad/Zed) drop.

**Architecture:** Three layers + entrypoint, no cycles. `render.py` is pure (no Textual, no acp connection): it maps ACP update objects → `RenderedItem` and `field_meta` → chip strings. `client.py` implements `acp.Client`: each callback marshals to the app via `post_message` (and, for permission, awaits an `asyncio.Future` resolved by a modal button). `app.py` is the Textual `App`: widgets, the agent-subprocess lifecycle, and message handlers that call `render.py`. All run on Textual's single asyncio loop; `conn.prompt` runs in an async worker (`thread=False`); the agent is a real subprocess via `acp.spawn_agent_process`.

**Tech Stack:** Python 3.11 (`.venv`), Textual 8.2.7, `agent-client-protocol` 0.10.1 (imports as `acp`), pytest.

## Global Constraints

- Zero upstream edits — `upstream/` stays vendored unmodified. (HARD.)
- Official SDK only: `agent-client-protocol` (imports `acp`); never `acp-sdk`. Do not roll our own JSON-RPC.
- Version facts: pip package `agent-client-protocol == 0.10.1`; `acp.PROTOCOL_VERSION == 1`; generated `schema.py` header says schema ref `v0.12.2` (generator tag, not the pkg version — ignore).
- Tests run as `.venv/bin/python -m pytest tests/` — scoped to `tests/`, NEVER bare `pytest` (it walks `upstream/tests/`).
- The agent runs as a separate subprocess via `acp.spawn_agent_process` (the ACP boundary must stay real). The only exception: the fake-agent script used by pilot/capability tests.
- `examples/sample-repo/calculator.py` is a fixture that ships buggy (`return a - b`). Any demo that fixes it runs on a temp copy; the fixture is restored.
- v1 advertises `ClientCapabilities(elicitation=ElicitationCapabilities())` — NO `fs`, NO `terminal` capability. The agent then falls back to its own `LocalEnvironment`.
- `AllowedOutcome` REQUIRES the discriminator `outcome="selected"` (omitting it raises pydantic `ValidationError`). `DeniedOutcome(outcome="cancelled")`.
- `RichLog.write()` only appends and returns the widget (NOT a line handle) — tool status is append-only in v1 (no in-place row update).
- Branch `phase5-textual-acp-client` already exists and holds the committed spec. Work on it.

**Verified runtime facts the code below relies on (do not re-derive):**
- `acp.start_tool_call(id, title, kind=, status=)` → `ToolCallStart` with `.tool_call_id`, `.title`, `.status` (str e.g. `'pending'`).
- `acp.update_tool_call(id, status=, content=[...])` → `ToolCallProgress` with `.status`, `.content` (list). `content[0]` is `ContentToolCallContent`; `content[0].content` is a `TextContentBlock` with `.text`.
- `acp.update_agent_message_text(text)` → `AgentMessageChunk` with `.content` = `TextContentBlock` (`.text`).
- Status may arrive as the str `'completed'` OR the stringified enum `'ToolCallStatus.failed'` — handle both.
- Every update object has a `.field_meta` attribute (our `_meta`), `None` by default.
- `App.run_worker(work, thread=False)` (default) runs an async coroutine on the app loop. `Input.Submitted` is the submit message. `App.run_test()` yields a pilot.
- `acp.Agent`, `acp.run_agent`, `acp.NewSessionResponse`, `acp.PromptResponse`, `acp.InitializeResponse`, `acp.PROTOCOL_VERSION` exist for the fake-agent script.

---

## File Structure

| File | Responsibility |
|---|---|
| `trace/tui/__init__.py` | package marker (empty) |
| `trace/tui/render.py` | PURE: `render_update`, `harness_chips`, `status_style`, `RenderedItem` |
| `trace/tui/messages.py` | Textual `Message` subclasses: `SessionUpdate`, `PermissionRequest` |
| `trace/tui/client.py` | `TuiClient` — implements `acp.Client`; callbacks → `post_message` / Future |
| `trace/tui/app.py` | `HarnessTui(App)` + `PermissionModal(ModalScreen)`: widgets, lifecycle, handlers |
| `trace/tui/app.tcss` | layout + status/chip colors |
| `trace/tui_main.py` | entrypoint (`--model`, `--cwd`) |
| `tests/test_tui_render.py` | pure render unit tests |
| `tests/test_tui_pilot.py` | 1–2 Textual pilot smokes (fake-agent subprocess) |
| `tests/test_tui_capabilities.py` | proves fs/terminal stubs are never called under elicitation-only caps |
| `tests/fake_agent.py` | tiny `acp.Agent` script for pilot/capability tests (test helper, importable + runnable) |
| `README.md`, `docs/learning-log.md` | Phase 5 entry |

**Task order & dependency:** Task 1 (render core, pure, no deps) → Task 2 (messages + client, depends on render's `RenderedItem` only by name) → Task 3 (fake-agent test script) → Task 4 (app + tcss + entrypoint, depends on 1+2) → Task 5 (pilot smoke, depends on 3+4) → Task 6 (capability test, depends on 3) → Task 7 (docs). Tasks 3 and 6 could swap; keep this order.

---

### Task 1: Pure render core (`render.py`)

**Files:**
- Create: `trace/tui/__init__.py` (empty)
- Create: `trace/tui/render.py`
- Test: `tests/test_tui_render.py`

**Interfaces:**
- Consumes: nothing (pure; duck-types acp update objects via attributes).
- Produces:
  - `RenderedItem` (frozen dataclass): `kind: str, text: str="", id: str="", title: str="", status: str="", body: str=""`
  - `render_update(update) -> RenderedItem | None`
  - `harness_chips(field_meta: dict | None) -> list[str]`
  - `status_style(status) -> str`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tui_render.py`:

```python
import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

from types import SimpleNamespace as NS

from trace.tui.render import render_update, harness_chips, status_style, RenderedItem


# --- helpers: build stub update objects that duck-type the acp ones ---
def _msg(text):
    return NS(__class__=type("AgentMessageChunk", (), {}), content=NS(text=text))

# render_update dispatches on type(update).__name__, so name the stub classes.
def _named(name, **attrs):
    cls = type(name, (), {})
    obj = cls()
    for k, v in attrs.items():
        setattr(obj, k, v)
    return obj


def test_render_agent_message_chunk():
    u = _named("AgentMessageChunk", content=NS(text="hello"), field_meta=None)
    item = render_update(u)
    assert item == RenderedItem(kind="message", text="hello")


def test_render_user_message_chunk():
    u = _named("UserMessageChunk", content=NS(text="hi"), field_meta=None)
    assert render_update(u) == RenderedItem(kind="user", text="hi")


def test_render_agent_thought_chunk():
    u = _named("AgentThoughtChunk", content=NS(text="thinking"), field_meta=None)
    assert render_update(u) == RenderedItem(kind="thought", text="thinking")


def test_render_tool_call_start():
    u = _named("ToolCallStart", tool_call_id="tc1", title="$ echo hi", status="pending")
    assert render_update(u) == RenderedItem(kind="tool", id="tc1", title="$ echo hi", status="pending")


def test_render_tool_call_progress_with_body():
    content = [NS(content=NS(text="output here"))]
    u = _named("ToolCallProgress", tool_call_id="tc1", status="completed", content=content)
    assert render_update(u) == RenderedItem(kind="tool_update", id="tc1", status="completed", body="output here")


def test_render_tool_call_progress_no_content():
    u = _named("ToolCallProgress", tool_call_id="tc1", status="failed", content=None)
    assert render_update(u) == RenderedItem(kind="tool_update", id="tc1", status="failed", body="")


def test_render_unknown_returns_none():
    assert render_update(_named("AgentPlanUpdate", plan=[])) is None


def test_status_style_all_str_forms():
    assert status_style("pending") == "yellow"
    assert status_style("in_progress") == "blue"
    assert status_style("completed") == "green"
    assert status_style("failed") == "red"
    assert status_style("something-else") == "white"


def test_status_style_stringified_enum_forms():
    # the smoke tests showed status can arrive as "ToolCallStatus.failed"
    assert status_style("ToolCallStatus.failed") == "red"
    assert status_style("ToolCallStatus.completed") == "green"


def test_harness_chips_task_classified():
    fm = {"harness": {"task_classified": {"task_type": "code_fix", "skills": ["debugging"], "confidence": 0.9}}}
    assert harness_chips(fm) == ["classified: code_fix · skills: debugging · conf: 0.90"]


def test_harness_chips_task_classified_no_skills():
    fm = {"harness": {"task_classified": {"task_type": "chat_question", "skills": [], "confidence": 0.5}}}
    assert harness_chips(fm) == ["classified: chat_question · skills: — · conf: 0.50"]


def test_harness_chips_skill_load():
    fm = {"harness": {"skill_load": {"injected": ["a", "b"], "skipped": ["c"]}}}
    assert harness_chips(fm) == ["skills: 2 loaded, 1 skipped"]


def test_harness_chips_none_and_empty():
    assert harness_chips(None) == []
    assert harness_chips({}) == []
    assert harness_chips({"harness": {}}) == []


def test_harness_chips_malformed_never_raises():
    # missing nested keys must yield [], not raise
    assert harness_chips({"harness": {"task_classified": {}}}) == ["classified: ? · skills: — · conf: 0.00"]
    assert harness_chips({"harness": {"skill_load": {}}}) == ["skills: 0 loaded, 0 skipped"]
    assert harness_chips({"harness": "not-a-dict"}) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_tui_render.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'trace.tui'` (or import error).

- [ ] **Step 3: Implement `render.py`**

Create `trace/tui/__init__.py` (empty file).

Create `trace/tui/render.py`:

```python
"""Pure render core for the TUI. No Textual, no acp connection, no async —
turns ACP update objects into display-ready values, and reads our custom
field_meta["harness"] stream into chip strings (the bit generic clients drop).
Duck-types acp update objects via attributes so tests can pass plain stubs."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RenderedItem:
    kind: str                 # "message" | "thought" | "user" | "tool" | "tool_update"
    text: str = ""            # message/thought/user body
    id: str = ""              # tool_call_id (tool / tool_update correlation)
    title: str = ""           # "$ <command>" (tool)
    status: str = ""          # pending|in_progress|completed|failed
    body: str = ""            # tool output (tool_update)


_STATUS_COLORS = {
    "pending": "yellow",
    "in_progress": "blue",
    "completed": "green",
    "failed": "red",
}


def status_style(status) -> str:
    s = str(status)
    if "." in s:                     # "ToolCallStatus.failed" -> "failed"
        s = s.rsplit(".", 1)[-1]
    return _STATUS_COLORS.get(s, "white")


def render_update(update) -> RenderedItem | None:
    name = type(update).__name__
    if name in ("AgentMessageChunk", "UserMessageChunk", "AgentThoughtChunk"):
        kind = {"AgentMessageChunk": "message",
                "UserMessageChunk": "user",
                "AgentThoughtChunk": "thought"}[name]
        text = getattr(getattr(update, "content", None), "text", "") or ""
        return RenderedItem(kind=kind, text=text)
    if name == "ToolCallStart":
        return RenderedItem(kind="tool",
                            id=getattr(update, "tool_call_id", ""),
                            title=getattr(update, "title", ""),
                            status=str(getattr(update, "status", "")))
    if name == "ToolCallProgress":
        body = ""
        content = getattr(update, "content", None) or []
        if content:
            inner = getattr(content[0], "content", None)
            body = getattr(inner, "text", "") or ""
        return RenderedItem(kind="tool_update",
                            id=getattr(update, "tool_call_id", ""),
                            status=str(getattr(update, "status", "")),
                            body=body)
    return None                      # plan, current_mode_update, etc. — forward-compat


def harness_chips(field_meta: dict | None) -> list[str]:
    if not isinstance(field_meta, dict):
        return []
    harness = field_meta.get("harness")
    if not isinstance(harness, dict):
        return []
    chips: list[str] = []
    tc = harness.get("task_classified")
    if isinstance(tc, dict):
        task_type = tc.get("task_type", "?")
        skills = tc.get("skills") or []
        skills_str = ", ".join(skills) if skills else "—"
        conf = tc.get("confidence", 0.0) or 0.0
        chips.append(f"classified: {task_type} · skills: {skills_str} · conf: {conf:.2f}")
    sl = harness.get("skill_load")
    if isinstance(sl, dict):
        injected = sl.get("injected") or []
        skipped = sl.get("skipped") or []
        chips.append(f"skills: {len(injected)} loaded, {len(skipped)} skipped")
    return chips
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tui_render.py -q`
Expected: PASS (15 tests).

- [ ] **Step 5: Commit**

```bash
git add trace/tui/__init__.py trace/tui/render.py tests/test_tui_render.py
git commit -m "feat(tui): pure render core — update→RenderedItem, field_meta→chips, status→style"
```

---

### Task 2: Textual messages + `TuiClient` (`messages.py`, `client.py`)

**Files:**
- Create: `trace/tui/messages.py`
- Create: `trace/tui/client.py`
- Test: `tests/test_tui_client.py`

**Interfaces:**
- Consumes: nothing from Task 1 at runtime (client doesn't import render).
- Produces:
  - `SessionUpdate(Message)` with `.update` attribute.
  - `PermissionRequest(Message)` with `.options`, `.tool_call`, `.future` attributes.
  - `TuiClient(app)` implementing the `acp.Client` Protocol: `session_update`, `request_permission`, and benign-default fs/terminal/ext stubs. `request_permission` returns `RequestPermissionResponse(outcome=AllowedOutcome(outcome="selected", option_id=...))` or `DeniedOutcome(outcome="cancelled")`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tui_client.py`:

```python
import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

import asyncio
from types import SimpleNamespace as NS

from acp.schema import AllowedOutcome, DeniedOutcome, PermissionOption

from trace.tui.client import TuiClient
from trace.tui.messages import SessionUpdate, PermissionRequest


class _FakeApp:
    """Records posted Textual messages without a running app."""
    def __init__(self):
        self.posted = []
    def post_message(self, msg):
        self.posted.append(msg)


def test_session_update_posts_message():
    app = _FakeApp()
    client = TuiClient(app)
    update = NS(field_meta=None)
    asyncio.run(client.session_update("sid", update))
    assert len(app.posted) == 1
    assert isinstance(app.posted[0], SessionUpdate)
    assert app.posted[0].update is update


def test_request_permission_allow():
    app = _FakeApp()
    client = TuiClient(app)
    opts = [PermissionOption(kind="allow_once", name="Allow once", option_id="allow_once")]

    async def go():
        # run request_permission; resolve the future it posts with an option_id
        task = asyncio.ensure_future(
            client.request_permission(options=opts, session_id="sid", tool_call=NS())
        )
        await asyncio.sleep(0)                      # let it post + await
        req = app.posted[-1]
        assert isinstance(req, PermissionRequest)
        req.future.set_result("allow_once")         # simulate the modal button
        return await task

    resp = asyncio.run(go())
    assert isinstance(resp.outcome, AllowedOutcome)
    assert resp.outcome.outcome == "selected"
    assert resp.outcome.option_id == "allow_once"


def test_request_permission_reject():
    app = _FakeApp()
    client = TuiClient(app)

    async def go():
        task = asyncio.ensure_future(
            client.request_permission(options=[], session_id="sid", tool_call=NS())
        )
        await asyncio.sleep(0)
        app.posted[-1].future.set_result(None)      # None => reject
        return await task

    resp = asyncio.run(go())
    assert isinstance(resp.outcome, DeniedOutcome)
    assert resp.outcome.outcome == "cancelled"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_tui_client.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'trace.tui.client'`.

- [ ] **Step 3: Implement `messages.py` then `client.py`**

Create `trace/tui/messages.py`:

```python
"""Typed handoff between the acp.Client callbacks and the Textual app."""

from __future__ import annotations

import asyncio
from typing import Any

from textual.message import Message


class SessionUpdate(Message):
    """An ACP session/update notification, marshalled to the app for rendering."""
    def __init__(self, update: Any) -> None:
        super().__init__()
        self.update = update


class PermissionRequest(Message):
    """A permission request; the app resolves `future` with an option_id (allow)
    or None (reject)."""
    def __init__(self, options: Any, tool_call: Any, future: "asyncio.Future") -> None:
        super().__init__()
        self.options = options
        self.tool_call = tool_call
        self.future = future
```

Create `trace/tui/client.py`:

```python
"""TuiClient implements the acp.Client Protocol. Each callback marshals to the
Textual app via post_message; request_permission posts a modal request and awaits
an asyncio.Future the modal button resolves. Runs entirely on Textual's loop —
no threads. fs/terminal/ext methods are benign stubs: v1 advertises NEITHER fs
NOR terminal capability, so the agent never calls them (proven by
tests/test_tui_capabilities.py)."""

from __future__ import annotations

import asyncio
from typing import Any

from acp.schema import (
    AllowedOutcome,
    DeniedOutcome,
    RequestPermissionResponse,
)

from trace.tui.messages import SessionUpdate, PermissionRequest


class TuiClient:                      # implements the acp.Client Protocol
    def __init__(self, app) -> None:
        self._app = app

    async def session_update(self, session_id: str, update: Any, **kw: Any) -> None:
        self._app.post_message(SessionUpdate(update))

    async def request_permission(self, options: Any, session_id: str,
                                 tool_call: Any, **kw: Any) -> Any:
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._app.post_message(PermissionRequest(options, tool_call, fut))
        option_id = await fut
        if option_id:
            # AllowedOutcome REQUIRES outcome="selected" — omitting raises ValidationError.
            return RequestPermissionResponse(
                outcome=AllowedOutcome(outcome="selected", option_id=option_id))
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))

    # --- benign defaults: unused in v1 (no fs/terminal capability advertised) ---
    async def read_text_file(self, *a: Any, **k: Any) -> Any: return None
    async def write_text_file(self, *a: Any, **k: Any) -> Any: return None
    async def create_terminal(self, *a: Any, **k: Any) -> Any: return None
    async def terminal_output(self, *a: Any, **k: Any) -> Any: return None
    async def wait_for_terminal_exit(self, *a: Any, **k: Any) -> Any: return None
    async def release_terminal(self, *a: Any, **k: Any) -> Any: return None
    async def kill_terminal(self, *a: Any, **k: Any) -> Any: return None
    async def ext_method(self, method: str, params: dict) -> dict: return {}
    async def ext_notification(self, method: str, params: dict) -> None: return None

    def on_connect(self, conn: Any) -> None:
        pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tui_client.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add trace/tui/messages.py trace/tui/client.py tests/test_tui_client.py
git commit -m "feat(tui): TuiClient (acp.Client) + Textual messages; permission Future bridge"
```

---

### Task 3: Fake-agent test script (`tests/fake_agent.py`)

A tiny `acp.Agent` that pilot + capability tests launch via `spawn_agent_process`. It emits one message chunk carrying a `field_meta["harness"]["task_classified"]` so the pilot can assert the chip renders, and (when prompted with a magic word) issues a `request_permission` so Smoke 2 can drive the modal. It NEVER touches a real model.

**Files:**
- Create: `tests/fake_agent.py`
- Test: indirectly exercised by Tasks 5 & 6; add a tiny direct smoke here so this task is independently verifiable.

**Interfaces:**
- Produces: a runnable script `tests/fake_agent.py` such that
  `spawn_agent_process(client, ".venv/bin/python", "tests/fake_agent.py")` yields a working agent supporting `initialize`, `new_session`, `prompt`. On a prompt whose text contains `"PERMISSION"`, it calls `client.request_permission` once before finishing.

- [ ] **Step 1: Write the failing test**

Add `tests/test_fake_agent.py`:

```python
import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

import asyncio
from pathlib import Path
from typing import Any

import acp

REPO = Path(__file__).resolve().parent.parent
CMD = [str(REPO / ".venv/bin/python"), str(REPO / "tests/fake_agent.py")]


class _Collector:
    def __init__(self): self.updates = []
    async def session_update(self, session_id, update, **kw): self.updates.append(update)
    async def request_permission(self, options, session_id, tool_call, **kw):
        from acp.schema import RequestPermissionResponse, DeniedOutcome
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
    async def read_text_file(self, *a, **k): return None
    async def write_text_file(self, *a, **k): return None
    async def create_terminal(self, *a, **k): return None
    async def terminal_output(self, *a, **k): return None
    async def wait_for_terminal_exit(self, *a, **k): return None
    async def release_terminal(self, *a, **k): return None
    async def kill_terminal(self, *a, **k): return None
    async def ext_method(self, m, p): return {}
    async def ext_notification(self, m, p): return None
    def on_connect(self, conn): pass


def test_fake_agent_emits_harness_chip_meta():
    async def go():
        c = _Collector()
        async with acp.spawn_agent_process(c, CMD[0], *CMD[1:]) as (conn, _proc):
            await conn.initialize(protocol_version=acp.PROTOCOL_VERSION)
            new = await conn.new_session(cwd=str(REPO), mcp_servers=[])
            resp = await conn.prompt(prompt=[acp.text_block("hello")], session_id=new.session_id)
        # at least one update carries harness.task_classified
        metas = [u.field_meta for u in c.updates if getattr(u, "field_meta", None)]
        types = [m.get("harness", {}).get("task_classified", {}).get("task_type") for m in metas]
        assert "chat_question" in types, f"got {types!r}"
        assert resp.stop_reason == "end_turn"
    asyncio.run(go())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_fake_agent.py -q`
Expected: FAIL — `tests/fake_agent.py` does not exist (spawn error / file not found).

- [ ] **Step 3: Implement `tests/fake_agent.py`**

```python
#!/usr/bin/env python3
"""A minimal ACP agent for TUI tests. No real model. Emits one agent message
carrying a field_meta["harness"]["task_classified"] chip; if the prompt text
contains "PERMISSION", it requests permission once (so the modal flow can be
driven). STDOUT is the JSON-RPC wire — never print to stdout."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "upstream" / "src"))
sys.path.insert(0, str(REPO))

import acp
from acp import update_agent_message_text
from acp.schema import AgentCapabilities, PermissionOption, ToolCallUpdate


class FakeAgent(acp.Agent):
    def __init__(self):
        self._conn = None
        self._sessions = set()

    def on_connect(self, conn):
        self._conn = conn

    async def initialize(self, protocol_version, client_capabilities=None,
                         client_info=None, **kw):
        return acp.InitializeResponse(
            protocol_version=acp.PROTOCOL_VERSION,
            agent_capabilities=AgentCapabilities(load_session=False),
        )

    async def new_session(self, cwd, additional_directories=None, mcp_servers=None, **kw):
        sid = "fake-session"
        self._sessions.add(sid)
        return acp.NewSessionResponse(session_id=sid)

    async def prompt(self, prompt, session_id, message_id=None, **kw):
        text = "".join(getattr(b, "text", "") for b in prompt)

        # 1) emit a harness chip via field_meta (the differentiator under test)
        upd = update_agent_message_text("")
        upd.field_meta = {"harness": {"task_classified": {
            "task_type": "chat_question", "skills": [], "confidence": 1.0}}}
        await self._conn.session_update(session_id, upd)

        # 2) optionally drive a permission round-trip
        if "PERMISSION" in text:
            options = [
                PermissionOption(kind="allow_once", name="Allow once", option_id="allow_once"),
                PermissionOption(kind="reject_once", name="Reject", option_id="reject_once"),
            ]
            await self._conn.request_permission(
                options=options, session_id=session_id,
                tool_call=ToolCallUpdate(tool_call_id="tc1"))

        # 3) a normal agent message
        await self._conn.session_update(session_id, update_agent_message_text("done"))
        return acp.PromptResponse(stop_reason="end_turn")


async def _main():
    await acp.run_agent(FakeAgent())


if __name__ == "__main__":
    asyncio.run(_main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_fake_agent.py -q`
Expected: PASS (1 test).

- [ ] **Step 5: Commit**

```bash
git add tests/fake_agent.py tests/test_fake_agent.py
git commit -m "test(tui): minimal fake ACP agent for pilot/capability tests"
```

---

### Task 4: Textual app + tcss + entrypoint (`app.py`, `app.tcss`, `tui_main.py`)

**Files:**
- Create: `trace/tui/app.py`
- Create: `trace/tui/app.tcss`
- Create: `trace/tui_main.py`
- Test: none in this task (the app is exercised by the pilot in Task 5). This task's deliverable is verified by a manual boot + an import-smoke test below.

**Interfaces:**
- Consumes: `render_update`, `harness_chips`, `status_style`, `RenderedItem` (Task 1); `TuiClient`, `SessionUpdate`, `PermissionRequest` (Task 2).
- Produces:
  - `HarnessTui(App)`: ctor `HarnessTui(agent_cmd: list[str], cwd: str, model: str)`. Methods: `compose`, `on_mount`, `on_input_submitted`, `_send_prompt`, `on_session_update`, `on_permission_request`, `action_cancel`, `on_unmount`.
  - `PermissionModal(ModalScreen)`: ctor `PermissionModal(options, tool_call)`; dismisses with an `option_id` (str) or `None`.

- [ ] **Step 1: Write the failing import-smoke test**

Add `tests/test_tui_app_import.py`:

```python
import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")


def test_app_constructs_without_running():
    from trace.tui.app import HarnessTui, PermissionModal
    app = HarnessTui(agent_cmd=["x"], cwd=".", model="mock")
    assert app.agent_cmd == ["x"]
    assert app.cwd == "."
    assert app.model == "mock"
    assert PermissionModal  # importable
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tui_app_import.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'trace.tui.app'`.

- [ ] **Step 3: Implement `app.py`, `app.tcss`, `tui_main.py`**

Create `trace/tui/app.tcss`:

```css
Screen { layout: vertical; }
#transcript { height: 1fr; border: round $primary; padding: 0 1; }
#prompt { dock: bottom; }
.chip { color: $text-muted; text-style: dim; }
PermissionModal { align: center middle; }
PermissionModal #box { width: 70; border: thick $warning; padding: 1 2; background: $surface; }
PermissionModal #cmd { color: $warning; margin-bottom: 1; }
```

Create `trace/tui/app.py`:

```python
"""HarnessTui: a single-session Textual ACP client. Launches the harness agent
as a subprocess via spawn_agent_process, runs the connection on Textual's own
asyncio loop, and renders the session/update stream (messages, tool calls, and
the harness _meta chips) into a RichLog. Permission requests surface as a modal
whose button resolves the Future the TuiClient awaits.

RichLog.write() only appends (no line handle), so tool status is append-only:
ToolCallStart writes the "$ cmd" line; ToolCallProgress appends a follow-up
status line."""

from __future__ import annotations

from typing import Any

import acp
from acp.schema import ClientCapabilities, ElicitationCapabilities
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, RichLog

from trace.tui.client import TuiClient
from trace.tui.messages import SessionUpdate, PermissionRequest
from trace.tui.render import render_update, harness_chips, status_style

_GLYPH = {"completed": "✓", "failed": "✗"}


class PermissionModal(ModalScreen):
    """Renders ALL acp-provided options generically + a Reject path. Dismisses
    with the chosen option_id (str) or None (reject)."""

    def __init__(self, options, tool_call) -> None:
        super().__init__()
        self._options = options or []
        self._tool_call = tool_call

    def compose(self) -> ComposeResult:
        cmd = getattr(self._tool_call, "tool_call_id", "") or "permission requested"
        with Vertical(id="box"):
            yield Label(f"$ {cmd}", id="cmd")
            for opt in self._options:
                yield Button(getattr(opt, "name", "Allow"),
                             id=f"opt-{getattr(opt, 'option_id', 'allow')}")
            yield Button("Reject", id="opt-__reject__", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        oid = event.button.id[len("opt-"):]
        self.dismiss(None if oid == "__reject__" else oid)


class HarnessTui(App):
    CSS_PATH = "tui/app.tcss"
    BINDINGS = [("escape", "cancel", "Cancel turn")]

    def __init__(self, agent_cmd: list[str], cwd: str, model: str) -> None:
        super().__init__()
        self.agent_cmd = agent_cmd
        self.cwd = cwd
        self.model = model
        self._client = TuiClient(self)
        self._conn = None
        self._cm = None                       # the spawn_agent_process context manager
        self._session_id = None
        self._pending_perm = None             # the in-flight permission Future, if any

    def compose(self) -> ComposeResult:
        yield RichLog(id="transcript", highlight=False, markup=True, wrap=True)
        yield Input(placeholder="Type a prompt…", id="prompt")

    @property
    def _log(self) -> RichLog:
        return self.query_one("#transcript", RichLog)

    async def on_mount(self) -> None:
        log = self._log
        try:
            self._cm = acp.spawn_agent_process(self._client, self.agent_cmd[0],
                                               *self.agent_cmd[1:])
            self._conn, _proc = await self._cm.__aenter__()
            await self._conn.initialize(
                protocol_version=acp.PROTOCOL_VERSION,
                client_capabilities=ClientCapabilities(elicitation=ElicitationCapabilities()),
            )
            new = await self._conn.new_session(cwd=self.cwd, mcp_servers=[])
            self._session_id = new.session_id
            log.write(f"[dim]harness · {self.cwd} · model={self.model}[/dim]")
        except Exception as e:                # startup failure is fatal but must not crash the UI
            log.write(f"[red]could not start agent: {e}[/red]")
            self.query_one("#prompt", Input).disabled = True

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text or self._conn is None:
            return
        self._log.write(f"[bold]you:[/bold] {text}")
        prompt = self.query_one("#prompt", Input)
        prompt.value = ""
        prompt.disabled = True
        self.run_worker(self._send_prompt(text), thread=False)

    async def _send_prompt(self, text: str) -> None:
        log = self._log
        try:
            resp = await self._conn.prompt(
                prompt=[acp.text_block(text)], session_id=self._session_id)
            if getattr(resp, "stop_reason", "end_turn") != "end_turn":
                log.write(f"[dim]— turn ended: {resp.stop_reason} —[/dim]")
        except Exception as e:
            log.write(f"[red]agent disconnected — restart to continue ({e})[/red]")
        finally:
            self.query_one("#prompt", Input).disabled = False

    def on_session_update(self, msg: SessionUpdate) -> None:
        log = self._log
        for chip in harness_chips(getattr(msg.update, "field_meta", None)):
            log.write(f"[dim]\\[{chip}][/dim]")
        item = render_update(msg.update)
        if item is None:
            return
        if item.kind == "message":
            if item.text:
                log.write(f"[bold]agent:[/bold] {item.text}")
        elif item.kind == "thought":
            if item.text:
                log.write(f"[dim italic]{item.text}[/dim italic]")
        elif item.kind == "user":
            if item.text:
                log.write(f"[bold]you:[/bold] {item.text}")
        elif item.kind == "tool":
            color = status_style(item.status)
            log.write(f"[{color}]{item.title}[/{color}]")
        elif item.kind == "tool_update":
            color = status_style(item.status)
            glyph = _GLYPH.get(item.status, "")
            line = f"  [{color}]→ {item.status} {glyph}[/{color}]"
            if item.body:
                line += f"  {item.body.splitlines()[0][:120]}"
            log.write(line)

    def on_permission_request(self, msg: PermissionRequest) -> None:
        self._pending_perm = msg.future

        def _resolve(chosen) -> None:
            self._pending_perm = None
            if not msg.future.done():
                msg.future.set_result(chosen)

        self.push_screen(PermissionModal(msg.options, msg.tool_call), _resolve)

    async def action_cancel(self) -> None:
        if self._conn is not None and self._session_id is not None:
            await self._conn.cancel(session_id=self._session_id)

    async def on_unmount(self) -> None:
        # resolve any dangling permission Future to reject, then tear down the subprocess
        if self._pending_perm is not None and not self._pending_perm.done():
            self._pending_perm.set_result(None)
        if self._cm is not None:
            try:
                await self._cm.__aexit__(None, None, None)
            except Exception:
                pass
```

Create `trace/tui_main.py`:

```python
#!/usr/bin/env python3
"""TUI entrypoint: a Textual ACP client driving the harness agent subprocess.

Usage:
  .venv/bin/python trace/tui_main.py [--model mock|vibeproxy] [--cwd PATH]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "upstream" / "src"))
sys.path.insert(0, str(REPO_ROOT))

from trace.tui.app import HarnessTui  # noqa: E402


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Harness Textual ACP client")
    parser.add_argument("--model", choices=["mock", "vibeproxy"], default="mock")
    parser.add_argument("--cwd", default=".")
    args = parser.parse_args(argv)

    agent_cmd = [
        str(REPO_ROOT / ".venv/bin/python"),
        str(REPO_ROOT / "trace/acp_main.py"),
        "--model", args.model,
    ]
    cwd = str(Path(args.cwd).resolve())
    HarnessTui(agent_cmd=agent_cmd, cwd=cwd, model=args.model).run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the import-smoke test + verify the suite still passes**

Run: `.venv/bin/python -m pytest tests/test_tui_app_import.py -q`
Expected: PASS (1 test).

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS (all prior tests + the new ones).

- [ ] **Step 5: Commit**

```bash
git add trace/tui/app.py trace/tui/app.tcss trace/tui_main.py tests/test_tui_app_import.py
git commit -m "feat(tui): Textual app + permission modal + entrypoint (single-session ACP client)"
```

---

### Task 5: Pilot smoke tests (`test_tui_pilot.py`)

**Files:**
- Create: `tests/test_tui_pilot.py`

**Interfaces:**
- Consumes: `HarnessTui` (Task 4), `tests/fake_agent.py` (Task 3).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tui_pilot.py`:

```python
import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

import asyncio
from pathlib import Path

from trace.tui.app import HarnessTui
from textual.widgets import RichLog, Input

REPO = Path(__file__).resolve().parent.parent
FAKE_CMD = [str(REPO / ".venv/bin/python"), str(REPO / "tests/fake_agent.py")]


def _transcript_text(app) -> str:
    log = app.query_one("#transcript", RichLog)
    # RichLog stores rendered strips in .lines; join their plain text
    out = []
    for strip in log.lines:
        out.append("".join(seg.text for seg in strip._segments) if hasattr(strip, "_segments") else str(strip))
    return "\n".join(out)


def test_pilot_renders_harness_chip_end_to_end():
    """Boot app against the fake agent, type a prompt, assert the harness chip and
    the agent message both land in the transcript."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()                      # let on_mount finish (spawn+init+session)
            app.query_one("#prompt", Input).value = "hello"
            await pilot.press("enter")
            # wait for the worker turn + posted updates to render
            for _ in range(50):
                await pilot.pause()
                if "classified: chat_question" in _transcript_text(app):
                    break
            text = _transcript_text(app)
        assert "classified: chat_question" in text, f"chip missing.\n{text}"
        assert "agent:" in text and "done" in text, f"agent message missing.\n{text}"

    asyncio.run(go())


def test_pilot_permission_modal_reject():
    """Optional Smoke 2: fake agent requests permission; rejecting resolves the
    Future and the turn completes. If this proves flaky, it may be removed in
    review — the render smoke above is the required one."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#prompt", Input).value = "please PERMISSION now"
            await pilot.press("enter")
            # wait for the modal to appear, then reject
            modal_seen = False
            for _ in range(50):
                await pilot.pause()
                from trace.tui.app import PermissionModal
                if isinstance(app.screen, PermissionModal):
                    modal_seen = True
                    # press the Reject button
                    await pilot.click("#opt-__reject__")
                    break
            # let the turn finish
            for _ in range(50):
                await pilot.pause()
                if "done" in _transcript_text(app):
                    break
            text = _transcript_text(app)
        assert modal_seen, "permission modal never appeared"
        assert "done" in text, f"turn did not complete after reject.\n{text}"

    asyncio.run(go())
```

> **Implementer note:** `RichLog.lines` holds `Strip` objects; the `_transcript_text` helper extracts plain text. If the `Strip` internal API differs in 8.2.7, adapt the helper to read text (e.g. via `strip.text` if available) — assert on substrings, not exact formatting. The required assertion is Smoke 1 (chip + message). If Smoke 2's modal timing is flaky, raise it in review for a decision (drop to required-render-only), per the spec's "optional" note — do NOT silently delete it.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_tui_pilot.py -q`
Expected: FAIL initially if `_transcript_text` needs adapting, OR PASS if correct. (If failing on Strip internals, fix the helper, not the assertions.)

- [ ] **Step 3: Adapt `_transcript_text` if needed; make Smoke 1 pass**

If `strip._segments` is wrong for 8.2.7, replace the helper body with whatever extracts plain text from `RichLog.lines` (inspect a `Strip` at runtime: `.venv/bin/python -c "from textual.strip import Strip; print(dir(Strip))"`). Keep assertions on substrings.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tui_pilot.py -q`
Expected: PASS (Smoke 1 required; Smoke 2 if stable).

- [ ] **Step 5: Commit**

```bash
git add tests/test_tui_pilot.py
git commit -m "test(tui): pilot smokes — end-to-end chip render + permission modal reject"
```

---

### Task 6: Capability test — stubs provably unused (`test_tui_capabilities.py`)

Proves the `None` fs/terminal stubs are safe because the agent never calls them under elicitation-only caps. Drives the REAL agent (`trace/acp_main.py`) with a client whose fs/terminal stubs RAISE if invoked, through a command-running turn; the turn must complete without any raise (agent took the LocalEnvironment fallback).

**Files:**
- Create: `tests/test_tui_capabilities.py`

**Interfaces:**
- Consumes: the real agent at `trace/acp_main.py`; the `@needs_vibeproxy` guard pattern from `tests/test_acp_smoke.py`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_tui_capabilities.py`:

```python
import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

import asyncio
import shutil
from pathlib import Path

import pytest
import acp
from acp.schema import ClientCapabilities, ElicitationCapabilities, RequestPermissionResponse, AllowedOutcome

REPO = Path(__file__).resolve().parent.parent
AGENT_CMD = [str(REPO / ".venv/bin/python"), str(REPO / "trace/acp_main.py"), "--model", "mock"]
SAMPLE = REPO / "examples" / "sample-repo"


def _vibeproxy_up() -> bool:
    try:
        import urllib.request
        with urllib.request.urlopen("http://localhost:8317/v1/models", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


needs_vibeproxy = pytest.mark.skipif(not _vibeproxy_up(),
    reason="VibeProxy not reachable at localhost:8317 — classification test skipped")


class _StrictClient:
    """Allows permission, but RAISES if any fs/terminal method is called."""
    def __init__(self): self.updates = []
    async def session_update(self, session_id, update, **kw): self.updates.append(update)
    async def request_permission(self, options, session_id, tool_call, **kw):
        # allow so the command runs via LocalEnvironment
        return RequestPermissionResponse(outcome=AllowedOutcome(outcome="selected", option_id="allow_once"))
    async def read_text_file(self, *a, **k): raise AssertionError("read_text_file called")
    async def write_text_file(self, *a, **k): raise AssertionError("write_text_file called")
    async def create_terminal(self, *a, **k): raise AssertionError("create_terminal called")
    async def terminal_output(self, *a, **k): raise AssertionError("terminal_output called")
    async def wait_for_terminal_exit(self, *a, **k): raise AssertionError("wait_for_terminal_exit called")
    async def release_terminal(self, *a, **k): raise AssertionError("release_terminal called")
    async def kill_terminal(self, *a, **k): raise AssertionError("kill_terminal called")
    async def ext_method(self, m, p): return {}
    async def ext_notification(self, m, p): return None
    def on_connect(self, conn): pass


@needs_vibeproxy
def test_no_fs_or_terminal_calls_under_elicitation_only(tmp_path):
    repo = tmp_path / "sample-repo"
    shutil.copytree(SAMPLE, repo)
    target = repo / "calculator.py"
    assert "return a - b" in target.read_text(), "fixture sanity"

    async def go():
        client = _StrictClient()
        async with acp.spawn_agent_process(client, AGENT_CMD[0], *AGENT_CMD[1:]) as (conn, _proc):
            await conn.initialize(
                protocol_version=acp.PROTOCOL_VERSION,
                client_capabilities=ClientCapabilities(elicitation=ElicitationCapabilities()),
            )
            new = await conn.new_session(cwd=str(repo), mcp_servers=[])
            await conn.prompt(prompt=[acp.text_block(
                "Fix the bug in calculator.py: the add function returns a - b, it should return a + b."
            )], session_id=new.session_id)

    # The turn must complete WITHOUT any fs/terminal stub raising.
    asyncio.run(go())
    # and the command ran via LocalEnvironment fallback (file fixed)
    assert "return a + b" in target.read_text(), "LocalEnvironment fallback did not run the command"
```

- [ ] **Step 2: Run test to verify it fails (or skips cleanly)**

Run: `.venv/bin/python -m pytest tests/test_tui_capabilities.py -q`
Expected: FAIL only if the import path is wrong; if VibeProxy is down it SKIPS (acceptable). With VibeProxy up it should already pass once the file is correct — but write it first and run to confirm it is collected and the guard works.

- [ ] **Step 3: (no new impl)** — this task adds only the test; the behavior it asserts already exists in the agent. If the test fails with a real fs/terminal call, that is a genuine bug in the agent's capability gating — STOP and escalate (do not "fix" by loosening the test).

- [ ] **Step 4: Run test to verify it passes (VibeProxy up) or skips (down)**

Run: `.venv/bin/python -m pytest tests/test_tui_capabilities.py -q`
Expected: PASS (VibeProxy up) or SKIPPED (down).

- [ ] **Step 5: Commit**

```bash
git add tests/test_tui_capabilities.py
git commit -m "test(tui): prove fs/terminal stubs are never called under elicitation-only caps"
```

---

### Task 7: Docs — README + learning log

**Files:**
- Modify: `README.md`
- Modify: `docs/learning-log.md`

**Interfaces:** none.

- [ ] **Step 1: Add a Phase 5 section to `README.md`**

Find the Phase 4 entry; add after it (match existing heading style):

```markdown
### Phase 5 — Textual ACP client (TUI)

A single-session Textual TUI that is an **ACP client** driving the Phase-4 agent
as a subprocess. Run it:

    .venv/bin/python trace/tui_main.py --model mock          # or --model vibeproxy
    .venv/bin/python trace/tui_main.py --model mock --cwd ~/myproject

Type a prompt; watch the streaming session/update render — messages, tool-call
lines, permission prompts (as a modal), and the harness **chips**
(`classified: …`, `skills: N loaded`) that generic ACP clients (Toad/Zed) drop.
The TUI is `render.py` (pure update→display) + `client.py` (`acp.Client`) +
`app.py` (Textual shell), on the official `acp` SDK both ends.
```

- [ ] **Step 2: Add a Phase 5 entry to `docs/learning-log.md`**

Append (match existing format):

```markdown
## Phase 5 — Textual ACP client (TUI)

The engine was already an ACP agent (Phase 4), so the TUI is "the same engine,
our pixels." The lesson: **a generic client renders our agent but drops what
makes it ours.** Toad (the Textual/ACP north star) ignores `_meta` entirely —
every RPC handler accepts `_meta` and never reads it. So we built our own thin
client whose whole reason to exist is rendering the `_meta["harness"]`
classify/skill stream. The differentiator lives in the purest, most-tested layer
(`render.harness_chips`), pinned to the exact shape the agent emits.

Concurrency asymmetry worth remembering: the **agent** side needed a thread
bridge (the engine is blocking/sync); the **client** side needs none — it is
async all the way down. `conn.prompt` runs in an async Textual worker
(`thread=False`) only to keep the input handler responsive while the turn
streams; the SDK's receive/dispatch already run on the same loop.

Codex pre-impl review caught a CRITICAL: `AllowedOutcome` requires the
discriminator `outcome="selected"` — omitting it raises `ValidationError`, so
every Allow would have crashed. And it corrected a false assumption that
`RichLog.write()` returns an editable line handle (it appends only) → tool
status is append-only in v1.
```

- [ ] **Step 3: Run the full suite one last time**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS (all tests; capability test skips if VibeProxy down).

- [ ] **Step 4: Verify the fixture is intact**

Run: `grep -n "return a" examples/sample-repo/calculator.py`
Expected: shows `return a - b` (fixture unmodified).

- [ ] **Step 5: Commit**

```bash
git add README.md docs/learning-log.md
git commit -m "docs(tui): Phase 5 README + learning-log entry"
```

---

## Manual verification (after all tasks; not a test)

1. `.venv/bin/python trace/tui_main.py --model mock` → app boots, header shows cwd+model, type a prompt, see streaming output + chips. `Ctrl-Q` to quit.
2. (Optional, VibeProxy up) `--model vibeproxy` on a temp copy of the fixture → a real classify + agent turn renders chips; verify and discard the temp copy.

## Codex Review Gates (per spec)

Before Phase 5 is "complete," Codex reviews these and the result is recorded in `.superpowers/sdd/progress.md`: (1) ACP client/session lifecycle (Tasks 2,4), (2) permission round-trip (Tasks 2,4,5), (3) async/Textual worker design (Task 4), (4) `_meta["harness"]` rendering contract (Task 1), (5) pilot smoke design (Task 5). Block on protocol/async/lifecycle/test-correctness issues; do not block on style. The final whole-branch review (subagent-driven-development) on Opus covers these.
