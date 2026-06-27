# Multi-tool Surface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `dn`'s agent a real multi-tool surface — a `harness/tools/` registry plus `Read`/`Write`/`Edit` tools — so the model can do structured file ops instead of shelling out, with `bash` kept as one tool among many.

**Architecture:** Three override points, all in code `harness/` already owns, zero `upstream/` edits. (1) A `harness/tools/` package: one `Tool` per file bundling schema + `execute`. (2) `StreamingLitellmModel` gains a `registry` ctor arg and overrides `_query` (both branches) to send every tool's schema, plus `_parse_actions` to route tool calls by name. (3) `TracingAgent.execute_actions` dispatches each action to its tool — bash stays on the `env.execute` path (preserving the `Submitted` finish), file tools call `tool.execute`. A missing `tool_name` defaults to `"bash"` for backward compatibility with the canned mock model.

**Tech Stack:** Python 3.11, pytest. Vendored mini-swe-agent engine (`upstream/`, never edited). litellm for the real model path.

**Spec:** `docs/superpowers/specs/2026-06-27-multi-tool-surface-design.md` (issue #60, Slice 1).

## Global Constraints

- Always work in the git worktree, never on `main` (AGENTS.md #1). This plan runs on branch `worktree-multi-tool-surface-spec`.
- **Zero edits under `upstream/`** (AGENTS.md #4). Every override is in `harness/`.
- Run tests from the worktree root with the worktree as cwd: `.venv/bin/python -m pytest tests/ -q` (target `tests/` only). Editable-install shadowing bites otherwise (see persona C1 hotfix lesson).
- Commit-message trailer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- Tool result contract (every `tool.execute` returns this exact shape): `{"output": str, "returncode": int, "exception_info": str | None}`. `output` is ALWAYS a `str`.
- Backward-compat invariant: an action with no `"tool_name"` key dispatches as `bash`. The canned mock model (`harness/models_mock.py`) emits bash-only actions without `tool_name`; all existing tests must stay green.
- Match surrounding style (AGENTS.md #5): new params thread exactly like the existing block params.

---

### Task 1: The `Tool` base + registry

**Files:**
- Create: `harness/tools/__init__.py`
- Create: `harness/tools/base.py`
- Create: `harness/tools/registry.py`
- Test: `tests/test_tools_registry.py`

**Interfaces:**
- Consumes: nothing (leaf).
- Produces:
  - `harness.tools.base.Tool` — a `Protocol` (or ABC) with attributes/methods: `name: str`, `schema: dict`, `display_label(args: dict) -> str`, `execute(args: dict, env) -> dict`.
  - `harness.tools.registry.build_registry() -> list[Tool]` — returns a FRESH list each call. In Task 1 it returns `[BashTool()]` only; Tasks 2–4 append `ReadTool`, `WriteTool`, `EditTool`.
  - `harness.tools.bash.BashTool` — `name="bash"`, `schema=BASH_TOOL` (imported from `minisweagent.models.utils.actions_toolcall`), `display_label(args)` returns `args.get("command", "")`, `execute` raises `NotImplementedError` (bash is dispatched via `env.execute`, never `tool.execute` — see Task 6).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tools_registry.py
import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

from harness.tools.registry import build_registry
from harness.tools.base import Tool


def test_registry_returns_fresh_list_each_call():
    a = build_registry()
    b = build_registry()
    assert a is not b  # fresh instance — never a shared module-global

def test_registry_contains_bash_with_valid_schema():
    names = [t.name for t in build_registry()]
    assert "bash" in names
    bash = next(t for t in build_registry() if t.name == "bash")
    assert bash.schema["function"]["name"] == "bash"
    assert bash.display_label({"command": "ls -la"}) == "ls -la"

def test_every_tool_satisfies_the_protocol():
    for t in build_registry():
        assert isinstance(t.name, str) and t.name
        assert isinstance(t.schema, dict)
        assert callable(t.display_label)
        assert callable(t.execute)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tools_registry.py -q`
Expected: FAIL — `ModuleNotFoundError: harness.tools`.

- [ ] **Step 3: Write minimal implementation**

```python
# harness/tools/__init__.py
```

```python
# harness/tools/base.py
"""The Tool surface: one object per tool, bundling its model-facing JSON schema
with its execution. Tools return the upstream observation shape so the existing
formatter renders them uniformly. Pure data + a callable; no I/O at import."""

from __future__ import annotations

from typing import Protocol


class Tool(Protocol):
    name: str
    schema: dict

    def display_label(self, args: dict) -> str:
        """Short human label for the 'action' trace/TUI event."""
        ...

    def execute(self, args: dict, env) -> dict:
        """Run the tool. Return {"output": str, "returncode": int,
        "exception_info": str | None}."""
        ...
```

```python
# harness/tools/bash.py
"""BashTool: schema only. Bash is never dispatched via execute(); the agent
routes it through env.execute so the environment's Submitted-on-completion
mechanism stays intact. execute() therefore raises if ever called."""

from __future__ import annotations

from minisweagent.models.utils.actions_toolcall import BASH_TOOL


class BashTool:
    name = "bash"
    schema = BASH_TOOL

    def display_label(self, args: dict) -> str:
        return args.get("command", "")

    def execute(self, args: dict, env) -> dict:
        raise NotImplementedError("bash is dispatched via env.execute, not Tool.execute")
```

```python
# harness/tools/registry.py
"""build_registry(): the live tool list for one agent construction. FRESH list
per call — never a module-global — because multiple model instances (worker vs.
chat, per-persona) must not share mutable tool state."""

from __future__ import annotations

from harness.tools.base import Tool
from harness.tools.bash import BashTool


def build_registry() -> list[Tool]:
    return [BashTool()]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tools_registry.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add harness/tools/__init__.py harness/tools/base.py harness/tools/bash.py harness/tools/registry.py tests/test_tools_registry.py
git commit -m "feat(tools): Tool base + registry with BashTool

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `ReadTool`

**Files:**
- Create: `harness/tools/read.py`
- Modify: `harness/tools/registry.py` (append `ReadTool()`)
- Test: `tests/test_tools_files.py` (create; Tasks 3–4 extend it)

**Interfaces:**
- Consumes: `Tool` shape (Task 1); the result contract.
- Produces: `harness.tools.read.ReadTool` — `name="read"`, schema with one required string property `path`; `display_label(args)` returns `f"read {args.get('path','')}"`; `execute(args, env)` reads `args["path"]` relative to `env.config.cwd` if not absolute, returns whole-file contents. Hit → `{"output": <contents>, "returncode": 0, "exception_info": None}`; missing/unreadable → `{"output": <error str>, "returncode": 1, "exception_info": None}`. NO offset/limit (YAGNI, per spec §5).

> NOTE: `LocalEnvironment` stores its working dir at `env.config.cwd` (a str). Resolve a relative `path` against it with `pathlib`. Grep `upstream/src/minisweagent/environments/local.py` to confirm the attribute name before relying on it.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tools_files.py
import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

from minisweagent.environments.local import LocalEnvironment
from harness.tools.read import ReadTool


def test_read_returns_contents_on_hit(tmp_path):
    (tmp_path / "a.txt").write_text("hello\nworld\n")
    env = LocalEnvironment(cwd=str(tmp_path))
    out = ReadTool().execute({"path": "a.txt"}, env)
    assert out["returncode"] == 0
    assert out["output"] == "hello\nworld\n"
    assert out["exception_info"] is None

def test_read_missing_file_is_returncode_1_not_exception(tmp_path):
    env = LocalEnvironment(cwd=str(tmp_path))
    out = ReadTool().execute({"path": "nope.txt"}, env)
    assert out["returncode"] == 1
    assert isinstance(out["output"], str) and out["output"]

def test_read_display_label():
    assert ReadTool().display_label({"path": "x/y.py"}) == "read x/y.py"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py -q`
Expected: FAIL — `ModuleNotFoundError: harness.tools.read`.

- [ ] **Step 3: Write minimal implementation**

```python
# harness/tools/read.py
"""ReadTool: whole-file read. Whole file only (no offset/limit — bash sed -n
covers ranges). Errors surface as returncode=1, matching a failed shell read,
so the model reacts the same way it does to bash failures."""

from __future__ import annotations

from pathlib import Path

READ_TOOL = {
    "type": "function",
    "function": {
        "name": "read",
        "description": "Read a text file and return its full contents.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "File path (absolute, or relative to the working directory)."}},
            "required": ["path"],
        },
    },
}


class ReadTool:
    name = "read"
    schema = READ_TOOL

    def display_label(self, args: dict) -> str:
        return f"read {args.get('path', '')}"

    def execute(self, args: dict, env) -> dict:
        p = Path(args["path"])
        if not p.is_absolute():
            p = Path(env.config.cwd) / p
        try:
            return {"output": p.read_text(), "returncode": 0, "exception_info": None}
        except Exception as e:
            return {"output": f"read failed: {e}", "returncode": 1, "exception_info": None}
```

```python
# harness/tools/registry.py — update import + list
from harness.tools.read import ReadTool
...
def build_registry() -> list[Tool]:
    return [BashTool(), ReadTool()]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add harness/tools/read.py harness/tools/registry.py tests/test_tools_files.py
git commit -m "feat(tools): ReadTool (whole-file read)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `WriteTool`

**Files:**
- Create: `harness/tools/write.py`
- Modify: `harness/tools/registry.py` (append `WriteTool()`)
- Test: `tests/test_tools_files.py` (extend)

**Interfaces:**
- Produces: `harness.tools.write.WriteTool` — `name="write"`, schema with required string props `path` and `content`; `display_label` returns `f"write {args.get('path','')}"`; `execute` writes `content` to `path` (creating parent dirs), overwriting if present. Success → `{"output": <summary str>, "returncode": 0, "exception_info": None}`; failure → returncode 1. Raw write — NO read-before-overwrite gate (spec §5, deferred).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tools_files.py  (append)
from harness.tools.write import WriteTool


def test_write_creates_file(tmp_path):
    env = LocalEnvironment(cwd=str(tmp_path))
    out = WriteTool().execute({"path": "new.txt", "content": "abc"}, env)
    assert out["returncode"] == 0
    assert (tmp_path / "new.txt").read_text() == "abc"

def test_write_overwrites_existing(tmp_path):
    (tmp_path / "f.txt").write_text("old")
    env = LocalEnvironment(cwd=str(tmp_path))
    out = WriteTool().execute({"path": "f.txt", "content": "new"}, env)
    assert out["returncode"] == 0
    assert (tmp_path / "f.txt").read_text() == "new"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py -k write -q`
Expected: FAIL — `ModuleNotFoundError: harness.tools.write`.

- [ ] **Step 3: Write minimal implementation**

```python
# harness/tools/write.py
"""WriteTool: create or overwrite a file. Raw write — the 'look before you
overwrite' rule stays prompt-level guidance; a hard read-gate needs read-tracking
state dn does not have yet (deferred)."""

from __future__ import annotations

from pathlib import Path

WRITE_TOOL = {
    "type": "function",
    "function": {
        "name": "write",
        "description": "Create or overwrite a text file with the given content.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path (absolute, or relative to the working directory)."},
                "content": {"type": "string", "description": "Full file content to write."},
            },
            "required": ["path", "content"],
        },
    },
}


class WriteTool:
    name = "write"
    schema = WRITE_TOOL

    def display_label(self, args: dict) -> str:
        return f"write {args.get('path', '')}"

    def execute(self, args: dict, env) -> dict:
        p = Path(args["path"])
        if not p.is_absolute():
            p = Path(env.config.cwd) / p
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(args["content"])
            return {"output": f"wrote {p}", "returncode": 0, "exception_info": None}
        except Exception as e:
            return {"output": f"write failed: {e}", "returncode": 1, "exception_info": None}
```

```python
# harness/tools/registry.py — update import + list
from harness.tools.write import WriteTool
...
    return [BashTool(), ReadTool(), WriteTool()]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py -k write -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/tools/write.py harness/tools/registry.py tests/test_tools_files.py
git commit -m "feat(tools): WriteTool (create/overwrite)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `EditTool` (exact-string replace, unique-match)

**Files:**
- Create: `harness/tools/edit.py`
- Modify: `harness/tools/registry.py` (append `EditTool()`)
- Test: `tests/test_tools_files.py` (extend)

**Interfaces:**
- Produces: `harness.tools.edit.EditTool` — `name="edit"`, schema with required string props `path`, `old_string`, `new_string`; `display_label` returns `f"edit {args.get('path','')}"`; `execute` replaces the UNIQUE occurrence of `old_string` with `new_string`. 0 matches → returncode 1 ("not found"); >1 match → returncode 1 ("ambiguous; add context"); exactly 1 → replace, returncode 0.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tools_files.py  (append)
from harness.tools.edit import EditTool


def test_edit_replaces_unique_match(tmp_path):
    (tmp_path / "c.py").write_text("return a - b\n")
    env = LocalEnvironment(cwd=str(tmp_path))
    out = EditTool().execute({"path": "c.py", "old_string": "a - b", "new_string": "a + b"}, env)
    assert out["returncode"] == 0
    assert (tmp_path / "c.py").read_text() == "return a + b\n"

def test_edit_zero_match_is_returncode_1(tmp_path):
    (tmp_path / "c.py").write_text("x = 1\n")
    env = LocalEnvironment(cwd=str(tmp_path))
    out = EditTool().execute({"path": "c.py", "old_string": "nope", "new_string": "y"}, env)
    assert out["returncode"] == 1
    assert (tmp_path / "c.py").read_text() == "x = 1\n"  # unchanged

def test_edit_multi_match_is_returncode_1_and_no_write(tmp_path):
    (tmp_path / "c.py").write_text("v = 1\nv = 1\n")
    env = LocalEnvironment(cwd=str(tmp_path))
    out = EditTool().execute({"path": "c.py", "old_string": "v = 1", "new_string": "v = 2"}, env)
    assert out["returncode"] == 1
    assert (tmp_path / "c.py").read_text() == "v = 1\nv = 1\n"  # unchanged — ambiguous, no silent replace-all
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py -k edit -q`
Expected: FAIL — `ModuleNotFoundError: harness.tools.edit`.

- [ ] **Step 3: Write minimal implementation**

```python
# harness/tools/edit.py
"""EditTool: exact-string replace of the UNIQUE occurrence. 0 matches or >1
matches both fail (returncode 1) with no write — the model must supply enough
context to make old_string unique. Mirrors Claude Code's Edit."""

from __future__ import annotations

from pathlib import Path

EDIT_TOOL = {
    "type": "function",
    "function": {
        "name": "edit",
        "description": "Replace the unique occurrence of old_string with new_string in a file. Fails if old_string is absent or appears more than once.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path (absolute, or relative to the working directory)."},
                "old_string": {"type": "string", "description": "Exact text to replace. Must be unique in the file."},
                "new_string": {"type": "string", "description": "Replacement text."},
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
}


class EditTool:
    name = "edit"
    schema = EDIT_TOOL

    def display_label(self, args: dict) -> str:
        return f"edit {args.get('path', '')}"

    def execute(self, args: dict, env) -> dict:
        p = Path(args["path"])
        if not p.is_absolute():
            p = Path(env.config.cwd) / p
        try:
            text = p.read_text()
        except Exception as e:
            return {"output": f"edit failed: {e}", "returncode": 1, "exception_info": None}
        count = text.count(args["old_string"])
        if count == 0:
            return {"output": "edit failed: old_string not found", "returncode": 1, "exception_info": None}
        if count > 1:
            return {"output": f"edit failed: old_string appears {count} times; add surrounding context to make it unique", "returncode": 1, "exception_info": None}
        p.write_text(text.replace(args["old_string"], args["new_string"]))
        return {"output": f"edited {p}", "returncode": 0, "exception_info": None}
```

```python
# harness/tools/registry.py — update import + list
from harness.tools.edit import EditTool
...
    return [BashTool(), ReadTool(), WriteTool(), EditTool()]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py -q`
Expected: PASS (all file-tool tests).

- [ ] **Step 5: Commit**

```bash
git add harness/tools/edit.py harness/tools/registry.py tests/test_tools_files.py
git commit -m "feat(tools): EditTool (unique-match exact replace)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Model seam — send the registry's schemas + parse by name

**Files:**
- Modify: `harness/streaming_model.py` (add `registry` ctor arg; override both `_query` branches and `_parse_actions`)
- Test: `tests/test_streaming_model_tools.py` (create)

**Interfaces:**
- Consumes: `build_registry()` (Task 1); `BASH_TOOL`, `parse_toolcall_actions`, `FormatError` from upstream.
- Produces: `StreamingLitellmModel(..., registry: list[Tool] | None = None)`. When `registry` is None it defaults to `build_registry()`. `_query` (both the streaming branch AND the blocking/fallback branch) sends `tools=[t.schema for t in self.registry]`. `_parse_actions(response)` returns actions `{"tool_name": name, "args": dict, "tool_call_id": id}`, and for the bash tool ALSO sets `"command"`. Unknown name or malformed args → `FormatError` (response persisted by the inherited `query()`).

> NOTE: the inherited `LitellmModel.query()` (`upstream/.../litellm_model.py:81-105`) calls `self._parse_actions(response)` and persists the response on `FormatError`. We override `_parse_actions`; we do NOT touch `query()`. The blocking `_query` we override is the `super()._query` call inside our own `streaming_model.py` (the `on_delta is None` branch at line 37 and the empty-stream fallback at line 59) — both currently delegate to upstream which hardcodes `tools=[BASH_TOOL]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_streaming_model_tools.py
import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

import json
import types
import pytest

from minisweagent.exceptions import FormatError
from harness.streaming_model import StreamingLitellmModel


def _resp(tool_calls, finish_reason="tool_calls"):
    """Minimal object graph matching response.choices[0].message.tool_calls."""
    msg = types.SimpleNamespace(tool_calls=[
        types.SimpleNamespace(id=tc["id"],
                              function=types.SimpleNamespace(name=tc["name"], arguments=tc["args"]))
        for tc in tool_calls])
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg, finish_reason=finish_reason)])


def _model():
    return StreamingLitellmModel(model_name="vibeproxy/x", cost_tracking="ignore_errors")


def test_query_sends_every_registered_schema(monkeypatch):
    captured = {}
    def fake_completion(**kwargs):
        captured.update(kwargs)
        return _resp([{"id": "c0", "name": "bash", "args": json.dumps({"command": "ls"})}])
    import litellm
    monkeypatch.setattr(litellm, "completion", fake_completion)
    m = _model()  # on_delta is None -> blocking branch
    m._query([{"role": "user", "content": "hi"}])
    sent = {t["function"]["name"] for t in captured["tools"]}
    assert {"bash", "read", "write", "edit"} <= sent

def test_parse_bash_action_has_command_and_tool_name():
    actions = _model()._parse_actions(_resp([{"id": "c0", "name": "bash", "args": json.dumps({"command": "ls"})}]))
    assert actions[0]["tool_name"] == "bash"
    assert actions[0]["command"] == "ls"        # env.execute compatibility
    assert actions[0]["tool_call_id"] == "c0"

def test_parse_file_tool_action_has_args_no_command():
    actions = _model()._parse_actions(_resp([{"id": "c1", "name": "read", "args": json.dumps({"path": "a.txt"})}]))
    assert actions[0]["tool_name"] == "read"
    assert actions[0]["args"] == {"path": "a.txt"}
    assert "command" not in actions[0]

def test_parse_unknown_tool_raises_formaterror_naming_it():
    with pytest.raises(FormatError) as ei:
        _model()._parse_actions(_resp([{"id": "c2", "name": "frobnicate", "args": "{}"}]))
    assert "frobnicate" in str(ei.value.messages[0]["content"])

def test_parse_bad_json_args_raises_formaterror():
    with pytest.raises(FormatError):
        _model()._parse_actions(_resp([{"id": "c3", "name": "read", "args": "{not json"}]))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_streaming_model_tools.py -q`
Expected: FAIL — `_query` sends only `[BASH_TOOL]` (first test) and `_parse_actions` produces bash-only `{"command","tool_call_id"}` shapes / does not raise on unknown names.

- [ ] **Step 3: Write minimal implementation**

Edit `harness/streaming_model.py`:

```python
# add imports at top
import json
from minisweagent.exceptions import FormatError
from jinja2 import StrictUndefined, Template
from harness.tools.registry import build_registry
```

```python
# __init__ — accept a registry (fresh default), keep on_delta behavior
    def __init__(self, *, on_delta: Callable[[str], None] | None = None, registry=None, **kwargs):
        super().__init__(**kwargs)
        self.on_delta = on_delta
        self.registry = registry if registry is not None else build_registry()

    def _tool_schemas(self) -> list[dict]:
        return [t.schema for t in self.registry]
```

```python
# _query — BOTH branches send the full registry. Replace the existing body:
    def _query(self, messages, **kwargs):
        if self.on_delta is None:
            # blocking path — was super()._query (which hardcodes [BASH_TOOL]).
            # Re-issue here with the full tool list so mock/CLI/non-streaming see every tool.
            try:
                return litellm.completion(
                    model=self.config.model_name, messages=messages,
                    tools=self._tool_schemas(),
                    **(self.config.model_kwargs | kwargs),
                )
            except litellm.exceptions.AuthenticationError as e:
                e.message += " You can permanently set your API key with `mini-extra config set KEY VALUE`."
                raise
        chunks = []
        try:
            stream = litellm.completion(
                model=self.config.model_name, messages=messages,
                tools=self._tool_schemas(), stream=True,
                **(self.config.model_kwargs | kwargs),
            )
            for chunk in stream:
                chunks.append(chunk)
                piece = _extract_delta(chunk)
                if piece:
                    self.on_delta(piece)
        except litellm.exceptions.AuthenticationError as e:
            e.message += " You can permanently set your API key with `mini-extra config set KEY VALUE`."
            raise
        rebuilt = litellm.stream_chunk_builder(chunks, messages=messages)
        if rebuilt is None and not chunks:
            # empty-stream fallback: re-issue blocking WITH the full tool list
            # (NOT super()._query, which would re-hardcode [BASH_TOOL]).
            return self._query(messages, **kwargs)  # on_delta is set, so guard against recursion:
        return rebuilt
```

> RECURSION GUARD: the fallback calls `self._query` while `on_delta` is set, which would re-enter the streaming branch. Instead, fall back by temporarily clearing on_delta. Implement the fallback as:
> ```python
>         if rebuilt is None and not chunks:
>             saved, self.on_delta = self.on_delta, None
>             try:
>                 return self._query(messages, **kwargs)   # now takes the blocking branch
>             finally:
>                 self.on_delta = saved
> ```

```python
# _parse_actions — override upstream's bash-only parse, route by tool name
    def _parse_actions(self, response) -> list[dict]:
        tool_calls = response.choices[0].message.tool_calls or []
        if not tool_calls:
            # reuse upstream's "no tool calls" FormatError via the bash parser
            from minisweagent.models.utils.actions_toolcall import parse_toolcall_actions
            return parse_toolcall_actions(
                tool_calls, format_error_template=self.config.format_error_template,
                template_kwargs={"finish_reason": response.choices[0].finish_reason})
        by_name = {t.name: t for t in self.registry}
        actions = []
        for tc in tool_calls:
            name = tc.function.name
            err = ""
            try:
                args = json.loads(tc.function.arguments)
            except Exception as e:
                args, err = {}, f"Error parsing arguments for tool '{name}': {e}."
            if name not in by_name:
                err += f"Unknown tool '{name}'. Available: {', '.join(by_name)}."
            if not isinstance(args, dict):
                err += f"Arguments for tool '{name}' must be a JSON object."
            if err:
                raise FormatError({
                    "role": "user",
                    "content": Template(self.config.format_error_template, undefined=StrictUndefined).render(
                        actions=[], error=err.strip(), finish_reason=response.choices[0].finish_reason),
                    "extra": {"interrupt_type": "FormatError"},
                })
            action = {"tool_name": name, "args": args, "tool_call_id": tc.id}
            if name == "bash":
                action["command"] = args.get("command", "")
            actions.append(action)
        return actions
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_streaming_model_tools.py -q`
Expected: PASS (5 tests). Then confirm no regression in the streaming model's existing tests: `.venv/bin/python -m pytest tests/ -k streaming -q`.

- [ ] **Step 5: Commit**

```bash
git add harness/streaming_model.py tests/test_streaming_model_tools.py
git commit -m "feat(streaming_model): send registry schemas + parse tools by name

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Dispatch — route each action to its tool (`TracingAgent`)

**Files:**
- Modify: `harness/tracing_agent.py:147-165` (`execute_actions`); add a `registry` ctor arg to `__init__` (`:34-42`)
- Test: `tests/test_tracing_agent_tools.py` (create)

**Interfaces:**
- Consumes: `build_registry()` (Task 1); the result contract; the existing `Submitted` import (already in `tracing_agent.py:28`).
- Produces: `TracingAgent(..., registry=None)` (defaults to `build_registry()`). `execute_actions` dispatches per action: `name = action.get("tool_name", "bash")`; `bash` → `self.env.execute(action)` inside the existing `try/except Submitted`; any other registered tool → `tool.execute(action["args"], self.env)`; unknown name → `FormatError`. Output paired via the UNCHANGED `format_observation_messages`.

> BACKWARD-COMPAT: the canned mock model emits actions with `command`/`tool_call_id` and NO `tool_name`. `action.get("tool_name", "bash")` makes those dispatch as bash — every existing test stays green.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tracing_agent_tools.py
import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

import yaml
from pathlib import Path
from minisweagent.environments.local import LocalEnvironment
from harness.events import Emitter
from harness.models_mock import build_mock_model
from harness.tracing_agent import TracingAgent


def _agent(tmp_path, cwd):
    cfg = yaml.safe_load(Path("upstream/src/minisweagent/config/mini.yaml").read_text())["agent"]
    cfg["output_path"] = str(tmp_path / "traj.json")
    emitter = Emitter(tmp_path / "events.jsonl", clock=lambda: 0.0, console=False)
    return TracingAgent(build_mock_model(), LocalEnvironment(cwd=str(cwd)), emitter=emitter, **cfg)


def test_file_tool_action_dispatches_to_tool(tmp_path):
    (tmp_path / "a.txt").write_text("data")
    agent = _agent(tmp_path, tmp_path)
    msg = {"extra": {"actions": [{"tool_name": "read", "args": {"path": "a.txt"}, "tool_call_id": "c0"}]}}
    out_msgs = agent.execute_actions(msg)
    assert out_msgs[0]["role"] == "tool"
    assert out_msgs[0]["tool_call_id"] == "c0"
    assert "data" in out_msgs[0]["content"]

def test_action_without_tool_name_dispatches_as_bash(tmp_path):
    agent = _agent(tmp_path, tmp_path)
    msg = {"extra": {"actions": [{"command": "echo hi", "tool_call_id": "c1"}]}}
    out_msgs = agent.execute_actions(msg)  # must NOT raise; bash path
    assert out_msgs[0]["tool_call_id"] == "c1"
    assert "hi" in out_msgs[0]["content"]

def test_mixed_actions_pair_to_correct_ids(tmp_path):
    (tmp_path / "b.txt").write_text("zzz")
    agent = _agent(tmp_path, tmp_path)
    msg = {"extra": {"actions": [
        {"command": "echo one", "tool_call_id": "a"},
        {"tool_name": "read", "args": {"path": "b.txt"}, "tool_call_id": "b"},
    ]}}
    out = agent.execute_actions(msg)
    by_id = {m["tool_call_id"]: m["content"] for m in out}
    assert "one" in by_id["a"] and "zzz" in by_id["b"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tracing_agent_tools.py -q`
Expected: FAIL — `read` action: current loop does `self.env.execute(action)`, which has no `command` key → bash error / wrong output (file tool not dispatched).

- [ ] **Step 3: Write minimal implementation**

Add `registry` to `__init__` (after the existing block params at `tracing_agent.py:34-42`):

```python
    def __init__(self, model, env, *, emitter: Emitter, skill_block: str = "",
                 persona_block: str = "", memory_block: str = "",
                 base_block: str = "", registry=None, **kwargs):
        super().__init__(model, env, **kwargs)
        self._emitter = emitter
        self._skill_block = skill_block
        self._persona_block = persona_block
        self._memory_block = memory_block
        self._base_block = base_block
        from harness.tools.registry import build_registry
        self._registry = registry if registry is not None else build_registry()
        self._tools_by_name = {t.name: t for t in self._registry}
        self._run_start = time.time()
```

Replace `execute_actions` (`:147-165`) with the multi-tool dispatch:

```python
    # --- seam 3: tool dispatch (bash via env; file tools via Tool.execute) ---
    def execute_actions(self, message: dict) -> list[dict]:
        outputs = []
        for action in message.get("extra", {}).get("actions", []):
            name = action.get("tool_name", "bash")   # missing => bash (mock back-compat)
            tool = self._tools_by_name.get(name)
            if tool is None:
                raise FormatError({"role": "user",
                                   "content": f"Unknown tool '{name}'.",
                                   "extra": {"interrupt_type": "FormatError"}})
            label = action.get("command") if name == "bash" else tool.display_label(action.get("args", {}))
            self._emitter.emit("action", command=label or "")
            if name == "bash":
                try:
                    output = self.env.execute(action)
                except Submitted:
                    self._emitter.emit("action.done", returncode=0, output_bytes=0)
                    raise
            else:
                output = tool.execute(action.get("args", {}), self.env)
            outputs.append(output)
            self._emitter.emit("action.done",
                               returncode=output.get("returncode", -1),
                               output_bytes=len(str(output.get("output", "")).encode("utf-8")))
        return self.add_messages(
            *self.model.format_observation_messages(message, outputs, self.get_template_vars())
        )
```

> `FormatError` is already imported at `tracing_agent.py:28`. The dispatch-time unknown-tool guard is belt-and-suspenders (Task 5's parse already rejects unknown names) but protects the ACP path and any hand-built action.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tracing_agent_tools.py -q`
Expected: PASS (3 tests). Then the full tracing-agent suite (mock back-compat): `.venv/bin/python -m pytest tests/ -k tracing_agent -q`.

- [ ] **Step 5: Commit**

```bash
git add harness/tracing_agent.py tests/test_tracing_agent_tools.py
git commit -m "feat(tracing_agent): dispatch actions to tools; bash stays on env path

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Wire the registry into the real model construction sites

**Files:**
- Modify: `harness/acp_main.py:54-62` (`StreamingLitellmModel(...)` factory — pass `registry`)
- Modify: `harness/run_traced.py:48-54` (`_build_vibeproxy_model` — switch to `StreamingLitellmModel` with `registry`)
- Modify: `harness/acp_agent.py:541-545` and `harness/runner.py:89-92` (pass `registry` to `TracingAgent`, sourced once so model + agent share the SAME list)
- Test: `tests/test_multitool_wiring.py` (create)

**Interfaces:**
- Consumes: `build_registry()`, `StreamingLitellmModel(registry=...)` (Task 5), `TracingAgent(registry=...)` (Task 6).
- Produces: real coding paths construct one registry and hand the SAME list to both the model and the agent.

> WHY model AND agent get the registry: the model needs schemas (what to advertise); the agent needs the tool objects (how to dispatch). They must agree, so build once and pass the same list to both. In mock mode the model ignores `registry` (the canned model has no `_query` tool list), but the AGENT still needs it to dispatch any `tool_name` actions — so always pass it to `TracingAgent`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_multitool_wiring.py
import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

from harness.tools.registry import build_registry


def test_streaming_model_default_registry_has_file_tools():
    from harness.streaming_model import StreamingLitellmModel
    m = StreamingLitellmModel(model_name="vibeproxy/x", cost_tracking="ignore_errors")
    names = {t.name for t in m.registry}
    assert {"bash", "read", "write", "edit"} <= names

def test_tracing_agent_default_registry_has_file_tools(tmp_path):
    import yaml
    from pathlib import Path
    from minisweagent.environments.local import LocalEnvironment
    from harness.events import Emitter
    from harness.models_mock import build_mock_model
    from harness.tracing_agent import TracingAgent
    cfg = yaml.safe_load(Path("upstream/src/minisweagent/config/mini.yaml").read_text())["agent"]
    a = TracingAgent(build_mock_model(), LocalEnvironment(cwd=str(tmp_path)),
                     emitter=Emitter(tmp_path / "e.jsonl", clock=lambda: 0.0, console=False), **cfg)
    assert {"read", "write", "edit"} <= set(a._tools_by_name)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_multitool_wiring.py -q`
Expected: the first test PASSES already (Task 5 default), the second PASSES already (Task 6 default). If BOTH pass, the defaults already wire correctly — proceed to make the explicit-pass-through changes below so model and agent SHARE one list (not two independent defaults).

- [ ] **Step 3: Write minimal implementation**

`harness/acp_main.py:54-62` — pass an explicit registry so it can be shared:

```python
    def make(current_model=None):
        from harness.streaming_model import StreamingLitellmModel
        from harness.tools.registry import build_registry
        from harness import vibeproxy
        model_id = current_model or vibeproxy.default_model()
        return StreamingLitellmModel(
            model_name=vibeproxy.model_id(model_id),
            model_kwargs=vibeproxy.model_kwargs(),
            cost_tracking="ignore_errors",
            registry=build_registry(),
        )
    return make
```

`harness/run_traced.py:48-54` — switch the standalone real model to the streaming subclass (inherits LitellmModel behavior; on_delta defaults None so it's byte-identical on the blocking path) so the CLI path also advertises file tools:

```python
def _build_vibeproxy_model():
    from harness.streaming_model import StreamingLitellmModel
    from harness.tools.registry import build_registry
    return StreamingLitellmModel(
        model_name=vibeproxy.model_id(vibeproxy.default_model()),
        model_kwargs=vibeproxy.model_kwargs(),
        cost_tracking="ignore_errors",
        registry=build_registry(),
    )
```

`harness/runner.py:89-92` — `Runner` should accept a registry and forward it (source it from the model when present, else build). Minimal: add `registry=None` to `Runner.run(...)` signature and pass to `TracingAgent`:

```python
    def run(self, task: str, *, skill_block: str = "", persona_block: str = "",
            memory_block: str = "", base_block: str = "", registry=None, **kwargs) -> Iterator[Event]:
        ...
        agent = TracingAgent(self._model, self._env, emitter=emitter,
                             skill_block=skill_block, persona_block=persona_block,
                             memory_block=memory_block, base_block=base_block,
                             registry=(registry if registry is not None
                                       else getattr(self._model, "registry", None)),
                             **self._agent_cfg)
```

`harness/acp_agent.py:541-545` — share the model's registry with the agent:

```python
                model_obj = self._model_factory(model_id if model_id is not None else self._worker_model_id)
                agent = TracingAgent(model_obj, env,
                                     emitter=emitter, skill_block=skill_block,
                                     persona_block=persona_block, memory_block=memory_block,
                                     base_block=base_block,
                                     registry=getattr(model_obj, "registry", None),
                                     **cfg)
```

> `getattr(model_obj, "registry", None)` is None for the mock model → `TracingAgent` falls back to `build_registry()`, so the agent still dispatches file tools in mock mode. For the real model, agent and model share the identical list.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_multitool_wiring.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add harness/acp_main.py harness/run_traced.py harness/runner.py harness/acp_agent.py tests/test_multitool_wiring.py
git commit -m "feat(wiring): share tool registry across real model + agent paths

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Re-add the base-prompt "file tools over shell" line

**Files:**
- Modify: `harness/base_prompt.py` (`BASE_POLICY` working-principles block)
- Test: `tests/test_base_prompt.py` (add an assertion)

**Interfaces:**
- Consumes: nothing new.
- Produces: `BASE_POLICY` now contains a "prefer the dedicated Read/Write/Edit tools over shelling out for file ops" line. Does NOT add any parallel-tool-calls line (deferred).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_base_prompt.py  (add)
def test_policy_mentions_dedicated_file_tools_over_shell():
    from harness import base_prompt
    body = base_prompt.BASE_POLICY.lower()
    assert "read" in body and "edit" in body
    assert "prefer" in body  # the file-tools-over-shell guidance line

def test_policy_does_not_promise_parallel_tool_calls():
    from harness import base_prompt
    assert "parallel" not in base_prompt.BASE_POLICY.lower()  # deferred follow-up
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_base_prompt.py -k file_tools -q`
Expected: FAIL — the line is not present yet.

- [ ] **Step 3: Write minimal implementation**

In `harness/base_prompt.py`, add one bullet to the `# Working principles` list in `BASE_POLICY` (after the file_path:line_number line):

```python
- Prefer the dedicated Read, Write, and Edit tools over shelling out with cat/\
sed for file operations: they are precise and traceable. Use bash for everything \
else.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_base_prompt.py -q`
Expected: PASS (existing + 2 new).

- [ ] **Step 5: Commit**

```bash
git add harness/base_prompt.py tests/test_base_prompt.py
git commit -m "feat(base_prompt): prefer dedicated file tools over shell (now true)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Full-suite green + primary-checkout check

**Files:**
- Possibly modify: any test that asserted the old bash-only baseline (e.g. a test asserting `tools == [BASH_TOOL]`).

**Interfaces:**
- Consumes: everything above.
- Produces: a green suite; primary checkout untouched.

- [ ] **Step 1: Run the whole suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS (all green). If a pre-existing test asserted the single-bash-tool baseline (search: `grep -rn "BASH_TOOL\|\[bash\]\|tools=\[" tests/`), update it to the new multi-tool baseline and note the change in the commit body.

- [ ] **Step 2: Verify primary checkout untouched**

Run: `git -C /Users/alberto/Work/Quiubo/harness status --short`
Expected: empty output (all work is in the worktree).

- [ ] **Step 3: Commit any test-baseline updates**

```bash
git add -A
git commit -m "test: update baselines to the multi-tool surface; suite green

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- Spec §1 (Tool package + fresh registry) → Task 1. ✓
- Spec §2a (both `_query` branches send full registry) → Task 5, Step 3 (`_query` rewrite + recursion-guarded fallback). ✓
- Spec §2b (parse by name, finish_reason kept, name the bad tool, bad-args = FormatError, bash carries `command`+`tool_name`) → Task 5 (`_parse_actions`). ✓
- Spec §3 (uniform `{output,returncode,exception_info}`, output is str) → Tasks 2–4 result contract; reuse of `format_observation_messages` verified in Task 6. ✓
- Spec §4 (bash stays on env path inside try/except Submitted; file tools via execute; unknown-name guarded; display_label drives emit; missing tool_name => bash) → Task 6. ✓
- Spec §5 (Edit 0/multi → rc 1; Write raw, no gate; Read no offset/limit) → Tasks 2/3/4. ✓
- Spec §6 (plug into coding paths; chat path out of scope) → Task 7 (acp_main, run_traced, acp_agent, runner); chat path untouched. ✓
- Spec §7 (re-add file-tools-over-shell; NOT parallel line) → Task 8. ✓
- Spec §8 (test plan) → tests across Tasks 1–8; Task 9 full green. ✓
- Spec §9 (deferred: parallel calls, write-gate, ToolSearch) → not implemented, asserted absent (Task 8 parallel test). ✓

**Placeholder scan:** No TBD/TODO. Every code step shows the code. Task 5's recursion-guard is shown as the corrected fallback block (the inline `self._query` note is followed by the explicit save/restore implementation — implement the latter). Task 7 Step 2 explicitly handles the "defaults already pass" case (make the changes anyway for the shared-list invariant).

**Type consistency:** `registry` is a `list[Tool]` at every site: `StreamingLitellmModel.__init__` (T5), `TracingAgent.__init__` (T6), `Runner.run` (T7). `build_registry() -> list[Tool]` (T1) feeds all. Result dict keys `output`/`returncode`/`exception_info` identical across Tasks 2–6. `_tools_by_name` (T6) and `by_name` (T5) are local dicts keyed by `tool.name`. `_parse_actions` returns `{"tool_name","args","tool_call_id"[, "command"]}` (T5), consumed by `execute_actions` via `action.get("tool_name","bash")` / `action["args"]` (T6) — consistent.

**One implementer caution:** Task 5's `_query` blocking branch duplicates upstream's AuthenticationError-message tweak. That duplication is intentional (we cannot call `super()._query`, which re-hardcodes `[BASH_TOOL]`). Pinned to upstream v2.4.2 — verify against `litellm_model.py:64-74` before upgrading upstream.
