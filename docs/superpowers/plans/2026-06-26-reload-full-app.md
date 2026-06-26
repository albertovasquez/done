# Full-App `/reload` + Agent-Respawn `/clear` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `/reload` re-exec the whole `done` process (so TUI code edits like `header.py` are picked up, not just agent code), and make `/clear` reset the conversation **and** respawn the agent subprocess.

**Architecture:** `/reload` sets `app._reexec = True` and calls `app.exit()`; after `tui_main.main()`'s `app.run()` returns (terminal restored by Textual, agent killed by the existing `on_unmount`), `main()` re-exec's the original launcher via `os.execv`. `/clear` takes over the current agent-respawn body (`_cancel_inflight` → `_teardown` → `_connect`). The old lightweight `/clear` (`_new_session` only) is replaced.

**Tech Stack:** Python 3.11, Textual 8.2.7 (TUI), the `acp` SDK, pytest + Textual `run_test()` pilot harness, `os.execv`.

## Global Constraints

- **`os.execv` must run ONLY in `main()` after `app.run()` returns** — never from inside the running app. At that point Textual has restored the terminal and `on_unmount` has killed the agent (both verified to complete before `run()` returns in Textual 8.2.7). Re-exec'ing earlier inherits a raw-mode terminal or orphans the agent.
- **Re-exec the real launcher.** The launcher is the `dn` console script (`pyproject.toml` `[project.scripts] dn = "harness.tui_main:main"`), NOT `python -m`. Prefer `sys.argv[0]` (the `dn` executable) as `argv[0]` when it is an executable file; fall back to `[sys.executable, "-m", "harness.tui_main", ...]`.
- **Always pass `--cwd` explicitly** on re-exec. The app keys off `self.cwd`/`--cwd`, never process-cwd; omitting it could silently switch projects. Flags are reconstructed from the **parsed args**, not raw `sys.argv`.
- **`action_reload` does NOT call `_cancel_inflight`** (unlike `/clear`). The app is exiting; Textual cancels the in-flight worker (its `CancelledError` is a `BaseException`, bypassing the worker's `except Exception`) and `on_unmount` kills the agent. Verified safe.
- **`_busy` is intentionally never released in `action_reload`** (the process is replaced). The guard prevents interleaving; `app.exit()` is idempotent so a double call is harmless.
- **Test command (from the worktree root):** `.venv/bin/python -m pytest tests/ -q` (if no local `.venv`, use `../../.venv/bin/python`). Pilot tests cannot `os.execv` themselves — `/reload`'s full path is verified in pieces (helper + flag + `main()` branch with `os.execv` mocked).
- **Commit trailer:** end every commit message with `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

**Spec:** `docs/superpowers/specs/2026-06-26-reload-full-app-design.md`

---

## File Structure

- `harness/tui_main.py` — `_relaunch_args` + `_relaunch_command` helpers; `main()` re-exec branch after `app.run()`.
- `harness/tui/app.py` — `__init__` gains `self._reexec = False`; `action_reload` becomes the re-exec trigger; `action_clear` becomes the agent-respawn body (the old `action_reload`).
- `harness/tui/commands.py` — updated command descriptions.
- `tests/test_tui_main.py` — **new** — unit tests for the relaunch helpers + the `main()` re-exec branch (`os.execv` monkeypatched).
- `tests/test_tui_pilot.py` — `/reload` sets `_reexec` + exits; `/clear` respawns the agent (repurposed from the shipped reload tests).
- `tests/test_tui_commands.py` — updated description assertions if any test pins them.

Order: helpers first (Task 1, pure + testable in isolation), then the app command swap (Task 2), then wire `main()`'s re-exec branch (Task 3), then registry + pilot adjustments (Task 4).

---

## Task 1: Relaunch-command helpers in `tui_main`

**Files:**
- Modify: `harness/tui_main.py`
- Test: `tests/test_tui_main.py` (create)

**Interfaces:**
- Produces: `_relaunch_args(args, cwd) -> list[str]` (the flags); `_relaunch_command(args, cwd) -> list[str]` (`argv[0]` + flags, choosing the `dn` launcher or the `-m` fallback). `args` is the `argparse.Namespace` with `.model` and `.yolo`.

- [ ] **Step 1: Write the failing tests** — create `tests/test_tui_main.py`:

```python
import os
import sys
from types import SimpleNamespace as NS

sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

from harness.tui_main import _relaunch_args, _relaunch_command


def test_relaunch_args_vibeproxy_no_yolo():
    args = NS(model="vibeproxy", yolo=False)
    assert _relaunch_args(args, "/proj") == ["--model", "vibeproxy", "--cwd", "/proj"]


def test_relaunch_args_mock_with_yolo():
    args = NS(model="mock", yolo=True)
    assert _relaunch_args(args, "/p") == ["--model", "mock", "--cwd", "/p", "--yolo"]


def test_relaunch_command_prefers_executable_launcher(monkeypatch, tmp_path):
    # sys.argv[0] is an executable file (the `dn` console script) → used as argv[0]
    launcher = tmp_path / "dn"
    launcher.write_text("#!/bin/sh\n")
    launcher.chmod(0o755)
    monkeypatch.setattr(sys, "argv", [str(launcher)])
    args = NS(model="mock", yolo=False)
    cmd = _relaunch_command(args, "/p")
    assert cmd == [str(launcher), "--model", "mock", "--cwd", "/p"]


def test_relaunch_command_falls_back_to_dash_m(monkeypatch):
    # sys.argv[0] is not an executable file (e.g. "-c" / a module path) → fallback
    monkeypatch.setattr(sys, "argv", ["not-a-real-file"])
    args = NS(model="vibeproxy", yolo=True)
    cmd = _relaunch_command(args, "/p")
    assert cmd == [sys.executable, "-m", "harness.tui_main",
                   "--model", "vibeproxy", "--cwd", "/p", "--yolo"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `../../.venv/bin/python -m pytest tests/test_tui_main.py -q`
Expected: FAIL — `ImportError: cannot import name '_relaunch_args'`.

- [ ] **Step 3: Implement** — in `harness/tui_main.py`, add the helpers (after the imports, before `main`):

```python
def _relaunch_args(args, cwd) -> list[str]:
    """Flags to re-launch THIS TUI with, reconstructed from parsed args (not raw
    sys.argv) so they are correct however it was invoked. --cwd is always explicit."""
    flags = ["--model", args.model, "--cwd", cwd]
    if args.yolo:
        flags.append("--yolo")
    return flags


def _relaunch_command(args, cwd) -> list[str]:
    """argv for os.execv: the original launcher (the `dn` console script at
    sys.argv[0]) when it is an executable file, else `python -m harness.tui_main`."""
    launcher = sys.argv[0]
    flags = _relaunch_args(args, cwd)
    if launcher and os.path.isfile(launcher) and os.access(launcher, os.X_OK):
        return [launcher, *flags]
    return [sys.executable, "-m", "harness.tui_main", *flags]
```

(`os` and `sys` are already imported in `tui_main.py`.)

- [ ] **Step 4: Run to verify it passes**

Run: `../../.venv/bin/python -m pytest tests/test_tui_main.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add harness/tui_main.py tests/test_tui_main.py
git commit -m "feat(tui): relaunch-command helpers (prefer dn launcher, -m fallback)"
```

---

## Task 2: Swap the command bodies — `/reload` re-exec, `/clear` respawn

**Files:**
- Modify: `harness/tui/app.py` (`__init__` ~80-83; `action_clear` ~637-645; `action_reload` ~657-675)
- Test: `tests/test_tui_pilot.py`

**Interfaces:**
- Produces: `self._reexec: bool` (default False); `action_reload` sets `_reexec=True` + `_busy=True` and calls `self.exit()`; `action_clear` performs the agent respawn (cancel-inflight → reset → teardown → connect, with `_fatal` on failure).

- [ ] **Step 1: Write the failing tests** — append to `tests/test_tui_pilot.py`:

```python
def test_reload_sets_reexec_flag_and_exits():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app._reexec is False, "starts un-flagged"
            await app.action_reload()
            assert app._reexec is True, "reload must request a re-exec"
            # exit() was requested (Textual sets _exit); the app is on its way down
            assert app._exit is True, "reload must call app.exit()"
    asyncio.run(go())


def test_clear_respawns_agent_and_resets():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await _send_first_prompt(pilot, app, "hello")
            for _ in range(50):
                await pilot.pause()
                if "done" in _transcript_text(app):
                    break
            gen_before = app._gen
            await app.action_clear()
            await pilot.pause()
            assert app._gen == gen_before + 1, "clear must now RESPAWN the agent (gen bumps)"
            assert app._conn is not None, "reconnected after respawn"
            assert _transcript_text(app) == "", "conversation reset"
            assert app._busy is False, "busy released"
    asyncio.run(go())
```

> Note: the previously-shipped `test_clear_resets_session_without_respawn` (which asserted `_gen` UNCHANGED) is now WRONG under the new semantics — it must be removed/replaced by `test_clear_respawns_agent_and_resets` above. Delete that old test in this task.

- [ ] **Step 2: Run to verify it fails**

Run: `../../.venv/bin/python -m pytest tests/test_tui_pilot.py::test_reload_sets_reexec_flag_and_exits tests/test_tui_pilot.py::test_clear_respawns_agent_and_resets -q`
Expected: FAIL — `_reexec` attribute missing; `action_clear` doesn't bump `_gen` (still the old `_new_session`-only body).

- [ ] **Step 3: Implement** — in `harness/tui/app.py`:

Add to `__init__` (near `self._busy = False`):

```python
        self._reexec = False                  # /reload requests a full-process re-exec
```

Replace `action_clear` (currently the `_new_session`-only body) with the agent-respawn body (this is the OLD `action_reload` body, with "clearing" wording):

```python
    async def action_clear(self) -> None:
        if self._busy:
            return
        self._busy = True
        try:
            self._cancel_inflight()
            await self._reset_conversation()
            if self._started:                     # no transcript on the landing screen
                self._append_line(_c("muted", "— clearing… —"))
            await self._teardown()
            try:
                await self._connect()
                await self._reset_conversation()  # success → wipe the transient line
            except Exception as e:
                self._fatal(f"clear failed: {e}")
        finally:
            self._busy = False
```

Replace `action_reload` with the re-exec trigger:

```python
    async def action_reload(self) -> None:
        if self._busy:
            return
        self._busy = True                         # never released; the process is replaced
        self._reexec = True                       # main() re-execs after run() returns
        self.exit()                               # Textual restores the terminal; run() returns
```

- [ ] **Step 4: Run to verify it passes**

Run: `../../.venv/bin/python -m pytest tests/test_tui_pilot.py -q`
Expected: PASS (the two new tests + all existing, with the obsolete `test_clear_resets_session_without_respawn` removed).

- [ ] **Step 5: Commit**

```bash
git add harness/tui/app.py tests/test_tui_pilot.py
git commit -m "feat(tui): /reload triggers re-exec; /clear respawns the agent"
```

---

## Task 3: Wire the re-exec branch in `main()`

**Files:**
- Modify: `harness/tui_main.py` (`main`)
- Test: `tests/test_tui_main.py`

**Interfaces:**
- Consumes: `_relaunch_command` (T1); `app._reexec` (T2).
- Produces: `main()` calls `os.execv(cmd[0], cmd)` when `app._reexec` is set after `app.run()`; on `OSError` prints to stderr and `sys.exit(1)`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_tui_main.py`:

```python
import pytest
from harness import tui_main


class _FakeApp:
    def __init__(self, reexec):
        self._reexec = reexec
        self.ran = False
    def run(self):
        self.ran = True


def _patch_common(monkeypatch, reexec):
    """Patch HarnessTui to a fake whose _reexec is controllable, and stub the
    env/path side effects so main() can run headless."""
    app = _FakeApp(reexec)
    monkeypatch.setattr(tui_main, "HarnessTui", lambda **kw: app)
    monkeypatch.setattr(tui_main.paths, "load_env", lambda cwd: None)
    return app


def test_main_reexecs_when_flag_set(monkeypatch, tmp_path):
    app = _patch_common(monkeypatch, reexec=True)
    calls = {}
    def fake_execv(path, argv):
        calls["path"] = path
        calls["argv"] = argv
        raise SystemExit(0)        # execv never returns; simulate by bailing out
    monkeypatch.setattr(tui_main.os, "execv", fake_execv)
    with pytest.raises(SystemExit):
        tui_main.main(["--model", "mock", "--cwd", str(tmp_path)])
    assert app.ran is True
    assert calls["argv"][-4:] == ["--model", "mock", "--cwd", str(tmp_path)]
    assert calls["path"] == calls["argv"][0]


def test_main_no_reexec_when_flag_unset(monkeypatch, tmp_path):
    app = _patch_common(monkeypatch, reexec=False)
    called = {"execv": False}
    monkeypatch.setattr(tui_main.os, "execv",
                        lambda *a: called.__setitem__("execv", True))
    tui_main.main(["--model", "mock", "--cwd", str(tmp_path)])
    assert app.ran is True
    assert called["execv"] is False, "no re-exec when _reexec is False"


def test_main_reexec_oserror_exits_nonzero(monkeypatch, tmp_path, capsys):
    _patch_common(monkeypatch, reexec=True)
    def boom(path, argv):
        raise OSError("no such file")
    monkeypatch.setattr(tui_main.os, "execv", boom)
    with pytest.raises(SystemExit) as ei:
        tui_main.main(["--model", "mock", "--cwd", str(tmp_path)])
    assert ei.value.code == 1
    assert "reload failed to re-exec" in capsys.readouterr().err
```

- [ ] **Step 2: Run to verify it fails**

Run: `../../.venv/bin/python -m pytest tests/test_tui_main.py -q`
Expected: FAIL — `main()` does not call `os.execv` (no re-exec branch yet); the `cwd` may also differ until the branch reconstructs it.

- [ ] **Step 3: Implement** — in `harness/tui_main.py`, change the tail of `main()`. Replace the final line `HarnessTui(agent_cmd=agent_cmd, cwd=cwd, model=args.model).run()` with:

```python
    app = HarnessTui(agent_cmd=agent_cmd, cwd=cwd, model=args.model)
    app.run()
    if getattr(app, "_reexec", False):
        cmd = _relaunch_command(args, cwd)
        try:
            os.execv(cmd[0], cmd)          # replaces the process; never returns on success
        except OSError as e:
            print(f"reload failed to re-exec: {e}", file=sys.stderr)
            sys.exit(1)
```

- [ ] **Step 4: Run to verify it passes**

Run: `../../.venv/bin/python -m pytest tests/test_tui_main.py -q`
Expected: PASS (the 4 helper tests + the 3 main-branch tests).

- [ ] **Step 5: Commit**

```bash
git add harness/tui_main.py tests/test_tui_main.py
git commit -m "feat(tui): main() re-execs the launcher when /reload requested it"
```

---

## Task 4: Update command descriptions

**Files:**
- Modify: `harness/tui/commands.py`
- Test: `tests/test_tui_commands.py`

**Interfaces:**
- Consumes: `action_reload`/`action_clear` (T2).
- Produces: registry descriptions matching the new behaviors.

- [ ] **Step 1: Write the failing test** — append to `tests/test_tui_commands.py`:

```python
def test_reload_clear_descriptions_match_new_behavior():
    from harness.tui.commands import build_registry
    reg = {c.name: c for c in build_registry()}
    assert reg["reload"].description == "Reload everything (restart the app)"
    assert reg["clear"].description == "Fresh conversation (restart the agent)"
```

> If an existing test in `tests/test_tui_commands.py` pins the OLD descriptions, update that assertion in this task too.

- [ ] **Step 2: Run to verify it fails**

Run: `../../.venv/bin/python -m pytest tests/test_tui_commands.py::test_reload_clear_descriptions_match_new_behavior -q`
Expected: FAIL — descriptions still read the PR-#8 wording.

- [ ] **Step 3: Implement** — in `harness/tui/commands.py` `build_registry()`, update the two entries:

```python
        Command("reload", "Reload everything (restart the app)", _reload),
        Command("clear", "Fresh conversation (restart the agent)", _clear),
```

- [ ] **Step 4: Run to verify it passes**

Run: `../../.venv/bin/python -m pytest tests/test_tui_commands.py tests/test_tui_pilot.py tests/test_tui_main.py -q`
Expected: PASS.

- [ ] **Step 5: Run the FULL suite**

Run: `../../.venv/bin/python -m pytest tests/ -q`
Expected: PASS — entire suite green, no fixture left dirty (these tests use `FAKE_CMD`/`tmp_path`, never `examples/sample-repo`).

- [ ] **Step 6: Commit**

```bash
git add harness/tui/commands.py tests/test_tui_commands.py
git commit -m "feat(tui): update /reload and /clear descriptions for new behavior"
```

---

## Self-Review

**Spec coverage:**
- §1 command behaviors (table) → Task 2 (the two bodies) + Task 4 (descriptions). ✓
- §2 `/clear` = reset + respawn (old action_reload body, "clearing" wording) → Task 2. ✓
- §3 `/reload` = `_reexec` + `exit()`; `main()` re-exec; `_relaunch_args`/`_relaunch_command` (dn launcher + `-m` fallback, explicit `--cwd`) → Task 1 + Task 2 + Task 3. ✓
- §4 registry descriptions → Task 4. ✓
- §5 error handling: `/reload` `OSError` → stderr + exit 1 (Task 3); `/clear` respawn fail → `_fatal` (Task 2). ✓
- §6 testing: relaunch helpers (Task 1), `_reexec` flag + exit (Task 2), `main()` re-exec branch with `os.execv` mocked (Task 3), `/clear` respawns (Task 2). ✓
- Design-review notes: `action_reload` no `_cancel_inflight` (Task 2 body omits it, per constraint); `_busy` never released (Task 2); always explicit `--cwd` (Task 1). ✓

**Placeholder scan:** No TBD/TODO. The two "if an existing test pins the old behavior, update it" notes (Tasks 2, 4) are concrete instructions (remove `test_clear_resets_session_without_respawn`; update any pinned description), not vague placeholders.

**Type consistency:** `_relaunch_args(args, cwd) -> list[str]`, `_relaunch_command(args, cwd) -> list[str]`, `app._reexec: bool`, `action_reload`/`action_clear` (async, no args) — consistent across Tasks 1–4 and match the spec.

**Risk note:** Task 2 deletes/replaces the shipped `test_clear_resets_session_without_respawn` (its `_gen`-unchanged assertion is now false by design). The reviewer should confirm that removal is intentional (it is — `/clear` now respawns). Task 3's `main()` tests monkeypatch `os.execv`/`HarnessTui`/`paths.load_env` to run headless — the real re-exec is never executed in tests (correct; it can't be).
