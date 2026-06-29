# Permission Gate + Path Confinement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route every filesystem-touching tool (bash + read/write/edit) through one permission decision, confine file-tool paths to allowed roots, and fail closed (not open) for risky ops when no prompt channel exists. Closes #102, #106, #107.

**Architecture:** A new stdlib-only leaf `harness/permcheck.py` defines a `PermissionRequest` and path classification. The single decision function `check_permission(req) -> bool` lives in `acp_agent`; `AcpEnvironment` (bash) and `tracing_agent.execute_actions` (file tools) both wrap their action into a `PermissionRequest` and call it. File tools resolve paths once via `permcheck`, the gate approves the resolved path, and write/edit re-check the parent before touching disk.

**Tech Stack:** Python 3.11+, pytest. No new dependencies (permcheck is stdlib-only, mirroring `harness/textgate.py`).

## Global Constraints

- **Worktree:** all edits happen in `.worktrees/perm-gate-confinement` (branch `perm-gate-confinement`). Never touch the primary checkout.
- **Test command (from worktree root):** `.venv/bin/python -m pytest tests/ -q`
- **Zero upstream edits:** do not modify anything under `upstream/`. `AcpEnvironment` stays a subclass.
- **Leaf purity:** `harness/permcheck.py` imports stdlib only — no `harness.*` imports (prevents the dispatch-chain import cycle, same rule as `textgate.py`).
- **Byte-identical-wire / no-op-without-persona:** bash prompting behavior, the `$ <command>` title, and the no-persona path must be unchanged.
- **YOLO override:** `_auto_allow()` (yolo) must still short-circuit to allow-all, for bash AND file tools.
- **Bash is NOT path-confined** (by design): it is gated as a whole command only. Do not add bash content parsing.

---

### Task 1: `permcheck.py` — request shape + path classification

**Files:**
- Create: `harness/permcheck.py`
- Test: `tests/test_permcheck.py`

**Interfaces:**
- Consumes: nothing (stdlib leaf).
- Produces:
  - `PermissionRequest` dataclass with fields `kind: str` (`"bash"`|`"file"`), `command: str | None = None`, `path: Path | None = None`, `is_write: bool = False`, `is_exec: bool = False`, `outside_roots: bool = False`.
  - `classify_path(raw: str, roots: Sequence[Path]) -> tuple[Path, bool]` → `(resolved_path, outside_roots)`.
  - `parent_escapes(resolved: Path, roots: Sequence[Path]) -> bool` → True if the resolved path's parent directory resolves outside every root.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_permcheck.py
from pathlib import Path

import pytest

from harness.permcheck import PermissionRequest, classify_path, parent_escapes


def test_request_defaults():
    r = PermissionRequest(kind="file", path=Path("/x"), is_write=True)
    assert r.kind == "file" and r.is_write is True
    assert r.is_exec is False and r.outside_roots is False and r.command is None


def test_relative_path_anchors_to_first_root(tmp_path):
    resolved, outside = classify_path("sub/f.txt", [tmp_path])
    assert resolved == (tmp_path / "sub" / "f.txt").resolve()
    assert outside is False


def test_dotdot_escape_is_outside(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    resolved, outside = classify_path("../secret.txt", [root])
    assert outside is True
    assert ".." not in str(resolved)          # normalized away


def test_absolute_outside_root_is_outside(tmp_path):
    resolved, outside = classify_path("/etc/passwd", [tmp_path])
    assert outside is True


def test_symlink_escape_is_outside(tmp_path):
    root = tmp_path / "proj"; root.mkdir()
    outside_dir = tmp_path / "out"; outside_dir.mkdir()
    (root / "link").symlink_to(outside_dir)   # root/link -> ../out
    resolved, outside = classify_path("link/f.txt", [root])
    assert outside is True


def test_tilde_expands(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    resolved, outside = classify_path("~/f.txt", [tmp_path])
    assert resolved == (tmp_path / "f.txt").resolve()
    assert outside is False


def test_exact_root_is_inside(tmp_path):
    resolved, outside = classify_path(str(tmp_path), [tmp_path])
    assert outside is False


def test_second_root_accepted(tmp_path):
    cwd = tmp_path / "cwd"; cwd.mkdir()
    ws = tmp_path / "ws"; ws.mkdir()
    resolved, outside = classify_path(str(ws / "MEMORY.md"), [cwd, ws])
    assert outside is False


def test_nonexistent_leaf_under_valid_parent(tmp_path):
    # leaf does not exist yet (a fresh write) but parent is inside root
    resolved, outside = classify_path("new.txt", [tmp_path])
    assert outside is False


def test_parent_escapes_true_when_parent_outside(tmp_path):
    root = tmp_path / "proj"; root.mkdir()
    resolved = Path("/etc/x")
    assert parent_escapes(resolved, [root]) is True


def test_parent_escapes_false_when_parent_inside(tmp_path):
    resolved = tmp_path / "a" / "b.txt"
    assert parent_escapes(resolved, [tmp_path]) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_permcheck.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'harness.permcheck'`.

- [ ] **Step 3: Write the implementation**

```python
# harness/permcheck.py
"""Leaf permission/path helpers shared by the dispatch chokepoint and file tools.
No harness imports — keeps the dispatch chain cycle-free (same rule as
textgate.py). Defines the structured PermissionRequest the single decision
function consumes, plus path normalization/confinement against allowed roots."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass
class PermissionRequest:
    kind: str                          # "bash" | "file"
    command: str | None = None
    path: Path | None = None
    is_write: bool = False
    is_exec: bool = False
    outside_roots: bool = False


def _real_roots(roots: Sequence[Path]) -> list[Path]:
    return [Path(os.path.realpath(str(r))) for r in roots]


def _inside(resolved: Path, real_roots: Sequence[Path]) -> bool:
    return any(resolved == r or r in resolved.parents for r in real_roots)


def classify_path(raw: str, roots: Sequence[Path]) -> tuple[Path, bool]:
    """Resolve `raw` (expanduser, anchor relative paths to the first root, collapse
    `..`/symlinks via realpath) and report whether it lands outside every root.
    For a non-existent leaf, realpath resolves the existing parent prefix and
    appends the rest literally — correct for fresh writes."""
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = Path(roots[0]) / p
    resolved = Path(os.path.realpath(str(p)))
    return resolved, not _inside(resolved, _real_roots(roots))


def parent_escapes(resolved: Path, roots: Sequence[Path]) -> bool:
    """True if the parent directory of `resolved` resolves outside every root.
    Called immediately before write/edit touches disk — the TOCTOU re-check.
    Re-realpaths the parent so a parent symlinked out-of-root after approval is
    caught. Same boundary the gate enforced, re-validated at write time."""
    parent = Path(os.path.realpath(str(resolved.parent)))
    return not _inside(parent, _real_roots(roots))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_permcheck.py -q`
Expected: PASS (10 passed).

- [ ] **Step 5: Commit**

```bash
git add harness/permcheck.py tests/test_permcheck.py
git commit -m "feat(perm): permcheck leaf — PermissionRequest + path confinement (#106)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: File tools accept a resolved path + write/edit re-check the parent

**Files:**
- Modify: `harness/tools/read.py:32-39`, `harness/tools/write.py:33-42`, `harness/tools/edit.py:34-49`
- Test: `tests/test_tools_files.py` (add cases)

**Interfaces:**
- Consumes: `permcheck.parent_escapes` (Task 1).
- Produces: file tools whose `execute(args, env)` honor a pre-resolved path. When `args["__resolved_path"]` is present (a `Path`, injected by the chokepoint in Task 5), the tool uses it verbatim instead of re-resolving from `env.config.cwd`. write/edit call `parent_escapes` against `env._allowed_roots` (Task 4) before writing and abort with returncode 1 if the parent escapes. Backward-compatible: with no `__resolved_path` and no `_allowed_roots`, behavior is the legacy cwd-anchored resolve (so direct unit calls and any non-chokepoint caller still work).

> **Why `__resolved_path` injection rather than re-resolving in the tool:** the gate (Task 5) approves the path returned by `classify_path`; the tool must write *that exact* path so approved-path == written-path (no TOCTOU divergence). The chokepoint passes the resolved path down via the args dict.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_tools_files.py  (append)
from pathlib import Path

from harness.tools.write import WriteTool
from harness.tools.edit import EditTool
from harness.tools.read import ReadTool


class _Env:
    def __init__(self, cwd, roots=None):
        self.config = type("C", (), {"cwd": str(cwd)})()
        if roots is not None:
            self._allowed_roots = roots


def test_write_uses_resolved_path_override(tmp_path):
    target = tmp_path / "out.txt"
    env = _Env(tmp_path, roots=[tmp_path])
    out = WriteTool().execute({"path": "ignored", "content": "hi",
                               "__resolved_path": target}, env)
    assert out["returncode"] == 0
    assert target.read_text() == "hi"


def test_write_aborts_when_parent_escapes_roots(tmp_path):
    root = tmp_path / "proj"; root.mkdir()
    target = Path("/etc/should_not_write.txt")
    env = _Env(root, roots=[root])
    out = WriteTool().execute({"path": "x", "content": "x",
                               "__resolved_path": target}, env)
    assert out["returncode"] == 1
    assert "outside" in out["output"].lower()
    assert not target.exists()


def test_edit_uses_resolved_path_override(tmp_path):
    target = tmp_path / "f.txt"; target.write_text("alpha beta")
    env = _Env(tmp_path, roots=[tmp_path])
    out = EditTool().execute({"path": "ignored", "old_string": "beta",
                              "new_string": "gamma", "__resolved_path": target}, env)
    assert out["returncode"] == 0
    assert target.read_text() == "alpha gamma"


def test_read_uses_resolved_path_override(tmp_path):
    target = tmp_path / "r.txt"; target.write_text("payload")
    env = _Env(tmp_path, roots=[tmp_path])
    out = ReadTool().execute({"path": "ignored", "__resolved_path": target}, env)
    assert out["returncode"] == 0 and out["output"] == "payload"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py -q -k "resolved or escapes"`
Expected: FAIL — write ignores `__resolved_path` / no parent check (writes to legacy cwd path or to `/etc`).

- [ ] **Step 3: Implement — read.py**

Replace the path-resolution block in `harness/tools/read.py` `execute`:

```python
    def execute(self, args: dict, env) -> dict:
        p = args.get("__resolved_path")
        if p is None:
            p = Path(args["path"])
            if not p.is_absolute():
                p = Path(env.config.cwd) / p
        try:
            return {"output": p.read_text(), "returncode": 0, "exception_info": None}
        except Exception as e:
            return {"output": f"read failed: {e}", "returncode": 1, "exception_info": None}
```

- [ ] **Step 4: Implement — write.py**

Replace `WriteTool.execute` with:

```python
    def execute(self, args: dict, env) -> dict:
        from harness.permcheck import parent_escapes
        p = args.get("__resolved_path")
        if p is None:
            p = Path(args["path"])
            if not p.is_absolute():
                p = Path(env.config.cwd) / p
        roots = getattr(env, "_allowed_roots", None)
        if roots is not None and parent_escapes(p, roots):
            return {"output": f"write failed: path resolves outside allowed roots: {p}",
                    "returncode": 1, "exception_info": None}
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(args["content"])
            return {"output": f"wrote {p}", "returncode": 0, "exception_info": None}
        except Exception as e:
            return {"output": f"write failed: {e}", "returncode": 1, "exception_info": None}
```

- [ ] **Step 5: Implement — edit.py**

In `EditTool.execute`, replace the resolution block and add the parent re-check before the final `write_text`:

```python
    def execute(self, args: dict, env) -> dict:
        from harness.permcheck import parent_escapes
        p = args.get("__resolved_path")
        if p is None:
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
            return {"output": f"edit failed: old_string appears {count} times; add surrounding context to make it unique",
                    "returncode": 1, "exception_info": None}
        roots = getattr(env, "_allowed_roots", None)
        if roots is not None and parent_escapes(p, roots):
            return {"output": f"edit failed: path resolves outside allowed roots: {p}",
                    "returncode": 1, "exception_info": None}
        p.write_text(text.replace(args["old_string"], args["new_string"]))
        return {"output": f"edited {p}", "returncode": 0, "exception_info": None}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py -q`
Expected: PASS (existing + 4 new).

- [ ] **Step 7: Commit**

```bash
git add harness/tools/read.py harness/tools/write.py harness/tools/edit.py tests/test_tools_files.py
git commit -m "feat(perm): file tools honor resolved path + parent re-check (#106)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `AcpEnvironment` takes `check_permission` and wraps bash into a request

**Files:**
- Modify: `harness/acp_env.py:24-36` (constructor), `:51-53` (gate call)
- Test: `tests/test_acp_env.py:77-90` (migrate the 2 gate tests to req-shaped lambdas)

**Interfaces:**
- Consumes: `permcheck.PermissionRequest` (Task 1).
- Produces: `AcpEnvironment(check_permission=...)` where `check_permission: Callable[[PermissionRequest], bool] | None`. Internally, before running bash, it calls `check_permission(PermissionRequest(kind="bash", command=command, is_exec=True))`. The `request_permission` keyword is removed (single door).

- [ ] **Step 1: Update the two env gate tests to the new shape (write the failing test)**

Replace lines 77-90 of `tests/test_acp_env.py`:

```python
def test_permission_reject_skips_execution(tmp_path):
    calls = []
    env = _env(tmp_path,
               on_command=lambda phase, cmd, out: calls.append(phase),
               check_permission=lambda req: False)    # deny
    result = env.execute({"command": "printf 'denied'"})
    assert result["returncode"] == -1
    assert "denied" not in result.get("output", "")     # the command did NOT run
    assert "start" in calls and "rejected" in calls and "done" not in calls


def test_permission_allow_runs(tmp_path):
    env = _env(tmp_path, on_command=lambda *a: None, check_permission=lambda req: True)
    assert "ok" in env.execute({"command": "printf 'ok'"})["output"]


def test_permission_request_carries_bash_kind(tmp_path):
    seen = []
    env = _env(tmp_path, on_command=lambda *a: None,
               check_permission=lambda req: (seen.append(req) or True))
    env.execute({"command": "printf 'ok'"})
    assert seen and seen[0].kind == "bash"
    assert seen[0].command == "printf 'ok'" and seen[0].is_exec is True
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_acp_env.py -q -k permission`
Expected: FAIL — `AcpEnvironment.__init__` got unexpected kwarg `check_permission`.

- [ ] **Step 3: Implement the constructor + gate call**

In `harness/acp_env.py`, change the constructor signature and field:

```python
    def __init__(self, *,
                 on_command: Callable[[str, str, dict | None], None],
                 check_permission=None,                # Callable[[PermissionRequest], bool] | None
                 cancel_flag: threading.Event | None = None,
                 client_terminal: Callable[[str], dict] | None = None,
                 on_plan: Callable[[list[tuple[str, str]]], None] | None = None,
                 **kwargs: Any):
        super().__init__(**kwargs)
        self._on_command = on_command
        self._check_permission = check_permission
        self._cancel_flag = cancel_flag
        self._client_terminal = client_terminal
        self._on_plan = on_plan
```

Replace the gate call (was lines 51-53):

```python
        self._on_command("start", command, None)
        if self._check_permission is not None:
            from harness.permcheck import PermissionRequest
            req = PermissionRequest(kind="bash", command=command, is_exec=True)
            if not self._check_permission(req):
                self._on_command("rejected", command, None)
                return {"output": "", "returncode": -1, "exception_info": "permission denied"}
```

> Note: the `plan` sentinel interception (lines 45-49) stays ABOVE this block, so plan commands are never gated — unchanged.

- [ ] **Step 4: Run env tests**

Run: `.venv/bin/python -m pytest tests/test_acp_env.py -q`
Expected: PASS (all, including the 3 permission tests).

- [ ] **Step 5: Commit**

```bash
git add harness/acp_env.py tests/test_acp_env.py
git commit -m "refactor(perm): AcpEnvironment uses one check_permission(PermissionRequest) door

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `acp_agent` — single `check_permission` decision + allowed-roots wiring

**Files:**
- Modify: `harness/acp_agent.py:606-629` (replace `request_permission` with `check_permission`), `:667-675` (env construction + stamp `_allowed_roots`)
- Test: `tests/test_acp_perm_decision.py` (new — unit-test the decision in isolation)

**Interfaces:**
- Consumes: `permcheck.PermissionRequest` (Task 1); `self._auto_allow()` (`acp_agent.py:96`); `self._client_caps`.
- Produces: a `check_permission(req: PermissionRequest) -> bool` closure inside the prompt handler, passed as `AcpEnvironment(check_permission=...)` (Task 3 consumes it) AND used by the chokepoint (Task 5). The env is stamped `env._allowed_roots = [Path(state.cwd)] + ([state.workspace_dir] if state.workspace_dir else [])` (Task 2 + Task 5 consume it).

> The decision logic must be pure enough to unit-test. Extract it as a module-level helper `decide_permission(req, *, yolo, has_elicitation) -> bool` in `acp_agent.py`, and have the closure call it (the closure supplies `yolo=self._auto_allow()` and `has_elicitation=<caps check>`, then does the actual client prompt when `decide_permission` signals "ask"). To keep `decide_permission` a pure bool while still allowing a prompt, it returns one of three via a tiny enum-free convention: see Step 3.

- [ ] **Step 1: Write the failing decision unit tests**

```python
# tests/test_acp_perm_decision.py
from pathlib import Path

from harness.acp_agent import decide_permission
from harness.permcheck import PermissionRequest


def _file(write=False, outside=False):
    return PermissionRequest(kind="file", path=Path("/x"), is_write=write,
                             outside_roots=outside)


def test_yolo_allows_everything():
    assert decide_permission(_file(write=True, outside=True),
                             yolo=True, has_elicitation=False) == "allow"


def test_in_root_read_is_free():
    assert decide_permission(_file(write=False, outside=False),
                             yolo=False, has_elicitation=False) == "allow"


def test_in_root_write_is_free():
    assert decide_permission(_file(write=True, outside=False),
                             yolo=False, has_elicitation=False) == "allow"


def test_outside_root_write_no_channel_denies():
    assert decide_permission(_file(write=True, outside=True),
                             yolo=False, has_elicitation=False) == "deny"


def test_outside_root_write_with_channel_prompts():
    assert decide_permission(_file(write=True, outside=True),
                             yolo=False, has_elicitation=True) == "ask"


def test_bash_no_channel_denies():
    req = PermissionRequest(kind="bash", command="ls", is_exec=True)
    assert decide_permission(req, yolo=False, has_elicitation=False) == "deny"


def test_bash_with_channel_prompts():
    req = PermissionRequest(kind="bash", command="ls", is_exec=True)
    assert decide_permission(req, yolo=False, has_elicitation=True) == "ask"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_acp_perm_decision.py -q`
Expected: FAIL — `cannot import name 'decide_permission'`.

- [ ] **Step 3: Implement the pure decision helper (module-level in acp_agent.py)**

Add near the top of `harness/acp_agent.py` (after imports):

```python
def decide_permission(req, *, yolo: bool, has_elicitation: bool) -> str:
    """Pure policy: 'allow' (run, no prompt), 'deny' (block), or 'ask' (prompt the
    client). yolo overrides to allow. In-root file ops (read OR write) are free.
    Everything else is risky (bash, out-of-root, exec): ask if there is a prompt
    channel, otherwise fail CLOSED -> deny (#107)."""
    if yolo:
        return "allow"
    if req.kind == "file" and not req.outside_roots:
        return "allow"                       # in-root read & write are free
    return "ask" if has_elicitation else "deny"
```

- [ ] **Step 4: Replace the `request_permission` closure with `check_permission`**

Replace `harness/acp_agent.py:606-629` with:

```python
        def check_permission(req) -> bool:
            yolo = self._auto_allow()
            has_elicitation = not (
                self._client_caps is None
                or getattr(self._client_caps, "elicitation", None) is None
            )
            verdict = decide_permission(req, yolo=yolo, has_elicitation=has_elicitation)
            if verdict == "allow":
                return True
            if verdict == "deny":
                return False
            # verdict == "ask": prompt the client
            tc_id = tc["id"]
            options = [
                PermissionOption(kind="allow_once", name="Allow once", option_id="allow_once"),
                PermissionOption(kind="reject_once", name="Reject", option_id="reject_once"),
            ]
            title = f"$ {req.command}" if req.kind == "bash" else f"{'write' if req.is_write else 'read'} {req.path}"
            tool_call = ToolCallUpdate(tool_call_id=tc_id, title=title)
            coro = self._conn.request_permission(
                options=options, session_id=session_id, tool_call=tool_call
            )
            resp = asyncio.run_coroutine_threadsafe(coro, loop).result()
            return isinstance(resp.outcome, AllowedOutcome)
```

- [ ] **Step 5: Wire `check_permission` + allowed roots into the env**

Replace the env construction (`acp_agent.py:667-675`):

```python
        env = AcpEnvironment(cwd=state.cwd, on_command=on_command,
                             check_permission=check_permission,
                             cancel_flag=state.cancel_flag,
                             client_terminal=client_terminal,
                             on_plan=on_plan)
        env._active_persona = state.workspace_dir.name if state.workspace_dir else "default"
        # Allowed write/confine roots: the session cwd plus the persona workspace
        # (which lives OUTSIDE cwd — config_dir()/agents/<id> — so memory writes
        # must not be classified outside-root). Consumed by permcheck + file tools.
        from pathlib import Path as _Path
        env._allowed_roots = [_Path(state.cwd)] + (
            [state.workspace_dir] if state.workspace_dir else [])
```

- [ ] **Step 6: Run decision tests + full acp_agent-adjacent tests**

Run: `.venv/bin/python -m pytest tests/test_acp_perm_decision.py tests/test_acp_smoke.py tests/test_acp_tool_call_ids.py -q`
Expected: PASS. (The smoke/tool-call-id tests advertise elicitation, so bash still prompts → `ask` → prompt fires, unchanged.)

- [ ] **Step 7: Commit**

```bash
git add harness/acp_agent.py tests/test_acp_perm_decision.py
git commit -m "feat(perm): single check_permission decision + allowed roots; fail closed (#107)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Centralize the gate at the dispatch chokepoint + integration regression

**Files:**
- Modify: `harness/tracing_agent.py:221-249` (`execute_actions`)
- Test: `tests/test_tracing_agent_perm.py` (new — integration: file tool denied & not written; internal tools ungated; bash still via env)

**Interfaces:**
- Consumes: `permcheck.classify_path`, `permcheck.PermissionRequest` (Task 1); `env._check_permission` and `env._allowed_roots` (Tasks 3, 4); file tool `__resolved_path` override (Task 2).
- Produces: `execute_actions` that, for `read`/`write`/`edit`, classifies the path, builds a `PermissionRequest`, calls `env._check_permission(req)`, returns a permission-denied dict on False, and otherwise dispatches with `args["__resolved_path"]` set to the resolved path. `bash` is unchanged (goes through `env.execute`, which gates internally). `create_job`/`load_skill`/`load_memory`/`plan` are dispatched ungated.

- [ ] **Step 1: Write the failing integration tests**

Follow the existing pattern in `tests/test_tracing_agent_tools.py`: build a REAL
`TracingAgent` with `build_mock_model()` + `LocalEnvironment`, then stamp the
chokepoint contract (`_check_permission`, `_allowed_roots`) onto the env. Assert
on the returned tool messages (`out[0]["content"]`).

```python
# tests/test_tracing_agent_perm.py
from pathlib import Path  # noqa: E402

import yaml  # noqa: E402
from minisweagent.environments.local import LocalEnvironment  # noqa: E402

from harness.events import Emitter  # noqa: E402
from harness.models_mock import build_mock_model  # noqa: E402
from harness.tracing_agent import TracingAgent  # noqa: E402


def _agent(tmp_path, cwd, *, allow, roots):
    cfg = yaml.safe_load(Path("upstream/src/minisweagent/config/mini.yaml").read_text())["agent"]
    cfg["output_path"] = str(tmp_path / "traj.json")
    emitter = Emitter(tmp_path / "events.jsonl", clock=lambda: 0.0, console=False)
    env = LocalEnvironment(cwd=str(cwd))
    env._check_permission = allow          # Callable[[PermissionRequest], bool]
    env._allowed_roots = roots
    return TracingAgent(build_mock_model(), env, emitter=emitter, **cfg)


def test_outside_root_write_denied_and_not_written(tmp_path):
    root = tmp_path / "proj"; root.mkdir()
    target = tmp_path / "outside.txt"      # sibling of root, NOT inside it
    agent = _agent(tmp_path, root, allow=lambda req: False, roots=[root])
    msg = {"extra": {"actions": [
        {"tool_name": "write", "args": {"path": str(target), "content": "x"},
         "tool_call_id": "c0"}]}}
    out = agent.execute_actions(msg)
    assert not target.exists()             # #102+#106+#107: never written
    assert "denied" in out[0]["content"].lower()


def test_in_root_write_allowed_and_written(tmp_path):
    root = tmp_path / "proj"; root.mkdir()
    target = root / "ok.txt"
    agent = _agent(tmp_path, root, allow=lambda req: True, roots=[root])
    msg = {"extra": {"actions": [
        {"tool_name": "write", "args": {"path": "ok.txt", "content": "hi"},
         "tool_call_id": "c1"}]}}
    agent.execute_actions(msg)
    assert target.read_text() == "hi"


def test_gate_sees_file_kind_and_outside_flag(tmp_path):
    root = tmp_path / "proj"; root.mkdir()
    seen = []
    agent = _agent(tmp_path, root,
                   allow=lambda req: (seen.append(req) or True), roots=[root])
    msg = {"extra": {"actions": [
        {"tool_name": "write", "args": {"path": "../escape.txt", "content": "x"},
         "tool_call_id": "c2"}]}}
    agent.execute_actions(msg)
    assert seen and seen[0].kind == "file"
    assert seen[0].is_write is True and seen[0].outside_roots is True


def test_bash_still_routes_through_env(tmp_path):
    # bash is gated INSIDE env.execute (LocalEnvironment has no gate, so it just
    # runs) — the chokepoint must NOT add a second file-style gate for bash.
    agent = _agent(tmp_path, tmp_path, allow=lambda req: True, roots=[tmp_path])
    msg = {"extra": {"actions": [{"command": "echo hi", "tool_call_id": "c3"}]}}
    out = agent.execute_actions(msg)
    assert "hi" in out[0]["content"]       # bash path unchanged
```

> Note: `LocalEnvironment` (not `AcpEnvironment`) is used here so bash runs without
> a gate — this test isolates the *chokepoint's* behavior. The bash-gate itself is
> covered by `test_acp_env.py` (Task 3). The `_check_permission` stamped on the env
> is only consulted by the chokepoint for file tools.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_tracing_agent_perm.py -q`
Expected: FAIL — `write` currently runs ungated, so the outside-root file IS written / not denied.

- [ ] **Step 3: Implement the chokepoint gate**

In `harness/tracing_agent.py`, replace the `else` branch of the dispatch (currently `output = tool.execute(action.get("args", {}), self.env)` at line 242) with a gated branch. Add the imports at top of file if missing: `from pathlib import Path`. The full updated dispatch region:

```python
            if name == "bash":
                try:
                    output = self.env.execute(action)
                except Submitted:
                    self._emitter.emit("action.done", returncode=0, output_bytes=0)
                    raise
            else:
                output = self._dispatch_tool(name, tool, action.get("args", {}))
            outputs.append(output)
```

Add this helper method on `TracingAgent`:

```python
    # File tools (read/write/edit) are gated here at the ONE chokepoint; internal
    # tools (create_job/load_skill/load_memory) are not arbitrary-filesystem and
    # run ungated. Path is resolved ONCE and the resolved path is both gated and
    # handed to the tool, so approved-path == written-path (no TOCTOU divergence).
    _FILE_TOOLS = {"read", "write", "edit"}

    def _dispatch_tool(self, name: str, tool, args: dict) -> dict:
        check = getattr(self.env, "_check_permission", None)
        if name in self._FILE_TOOLS and check is not None:
            from harness.permcheck import PermissionRequest, classify_path
            roots = getattr(self.env, "_allowed_roots", None) or [Path(self.env.config.cwd)]
            resolved, outside = classify_path(args.get("path", ""), roots)
            req = PermissionRequest(kind="file", path=resolved,
                                    is_write=name in ("write", "edit"),
                                    outside_roots=outside)
            if not check(req):
                return {"output": "permission denied", "returncode": -1, "exception_info": ""}
            args = {**args, "__resolved_path": resolved}
        return tool.execute(args, self.env)
```

> `create_job`, `load_skill`, `load_memory` are not in `_FILE_TOOLS`, so they fall straight through to `tool.execute` ungated — intended. `plan` is a bash sentinel handled inside `env.execute`, never reaching this branch.

- [ ] **Step 4: Run integration tests**

Run: `.venv/bin/python -m pytest tests/test_tracing_agent_perm.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Run the FULL suite (regression gate)**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS — all green. Pay attention to `test_acp_env.py`, `test_tracing_agent*.py`, `test_tools_*.py`, `test_acp_smoke.py`, `test_acp_tool_call_ids.py`.

- [ ] **Step 6: Commit**

```bash
git add harness/tracing_agent.py tests/test_tracing_agent_perm.py
git commit -m "feat(perm): gate file tools at the dispatch chokepoint (#102)

Closes #102, #106, #107: file tools now pass through the same permission
decision as bash, paths are confined to allowed roots, and the gate fails
closed (not open) for risky ops when no elicitation channel exists.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec coverage:**
- #102 (file tools bypass gate) → Task 5 chokepoint gate. ✓
- #106 (no path confinement) → Task 1 `classify_path` + Task 2 parent re-check + Task 5 classify-at-dispatch. ✓
- #107 (fail open) → Task 4 `decide_permission` returns `deny` when `not has_elicitation`. ✓
- Allowed roots = cwd + workspace_dir → Task 4 Step 5. ✓
- Plan sentinel ungated → Task 3 note (stays above gate) + Task 5 (never reaches branch). ✓
- Internal tools ungated → Task 5 `_FILE_TOOLS` set excludes them. ✓
- One decision door → Task 3 + Task 4 (`check_permission`). ✓
- YOLO override → Task 4 `decide_permission(yolo=True)`. ✓
- TOCTOU re-check → Task 2 `parent_escapes`. ✓
- Bash not path-confined (non-goal) → no bash content parsing anywhere. ✓
- Resolved-path == written-path → Task 2 `__resolved_path` + Task 5 injection. ✓

**2. Placeholder scan:** No TBD/TODO; every code step shows complete code; every test step shows the assertion. ✓

**3. Type consistency:** `check_permission(req)` / `decide_permission(req, *, yolo, has_elicitation) -> str ("allow"|"deny"|"ask")` / `classify_path(raw, roots) -> (Path, bool)` / `parent_escapes(resolved, roots) -> bool` / `__resolved_path: Path` / `_allowed_roots: list[Path]` — names and shapes match across Tasks 1→5. ✓

## Known limitations (documented; follow-up issues to file)
- In-root writes are free — `.git/hooks`, shell rc, `~/.ssh` (when cwd is home) are not denied. → sensitive-subpath denylist issue.
- Same-process symlink-swap between parent re-check and write is not closed. → fd-based `O_NOFOLLOW` issue.
- Bash is gated as a whole command, not path-confined. → bash sandbox/parse issue.
