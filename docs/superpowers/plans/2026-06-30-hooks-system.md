# Internal Hook System + Session-End Auto-Regen Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a small internal Python hook registry (`session_start`/`session_end`) and a first consumer that refreshes stale, already-existing compressed siblings on session end — detached, gated on the existing `compress_aware` flag.

**Architecture:** A module-level pub/sub registry (`harness/hooks.py`) that the TUI fires at mount/unmount. Consumers self-register at import. The first consumer (`harness/compress/auto_regen.py`) finds stale existing siblings via a shared discovery unit (`harness/compress/targets.py`) and spawns a detached worker (`harness/compress/regen_worker.py`) that rebuilds them. `app.py` only learns two `dispatch(...)` lines and never imports compression.

**Tech Stack:** Python 3.11+, Textual (TUI), litellm (compression model, lazy), pytest.

## Global Constraints

- **Python ≥ 3.11** (project floor). Use `str | None` unions, `tomllib` if needed.
- **Test command (from worktree root):** `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/ -q` (target `tests/` only).
- **Known-green baseline (NOT regressions):** `tests/jobs/test_service_launchd.py::test_build_plist_has_runatload_keepalive_and_program` fails pre-existing; the Textual-Pilot cluster (`tests/test_tui_pilot.py`, `tests/test_tui_always_interactive.py`) can fail only under the broad run while passing in isolation. Anything else failing is yours.
- **Read path must NEVER raise:** any file read in the discovery/freshness path catches `OSError` + `UnicodeDecodeError` and degrades safely.
- **`dispatch` must NEVER raise:** each hook handler runs in `try/except Exception`, logged + skipped.
- **No new config knob:** auto-regen is gated on the existing `compress_aware` flag via `config.compress_aware_pinned(persona_id)`.
- **Never create a sibling that doesn't exist:** auto-regen only refreshes sources that already have a `.compressed.md` sibling.
- **`RULES_VERSION` unchanged** in this work (no compression-prompt edits).
- **Worktree discipline:** all work in `.worktrees/hooks-system` on branch `hooks-system`. Every implementer must `cd` to the worktree and confirm `git branch --show-current` == `hooks-system` before editing or committing.
- **Detach pattern (verbatim shape from `harness/jobs/supervisor.py:28`):** `subprocess.Popen([...], start_new_session=True, stdout=subprocess.DEVNULL, stderr=<log fd>, close_fds=True)`.
- **Conventional Commits**, each commit ending with `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

---

### Task 1: The hook registry (`harness/hooks.py`)

**Files:**
- Create: `harness/hooks.py`
- Test: `tests/test_hooks.py`

**Interfaces:**
- Consumes: nothing (leaf module; standard library only).
- Produces:
  - `register(event: str, handler, *, label: str | None = None) -> None`
  - `on(event: str, *, label: str | None = None)` — decorator returning the handler unchanged.
  - `dispatch(event: str, *, tracer=None, **payload) -> None` — calls handlers in registration order, isolates each in `try/except Exception`, logs via `tracer.emit("dn", "hook.error", event=…, label=…, error=…)` when `tracer` is truthy, never raises.
  - `clear(event: str | None = None) -> None` — remove handlers for one event, or all when `event is None` (test-only).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_hooks.py
from harness import hooks


def teardown_function():
    hooks.clear()


def test_dispatch_calls_handlers_in_registration_order():
    calls = []
    hooks.register("session_end", lambda **kw: calls.append("a"), label="a")
    hooks.register("session_end", lambda **kw: calls.append("b"), label="b")
    hooks.dispatch("session_end")
    assert calls == ["a", "b"]


def test_dispatch_passes_payload_to_handlers():
    seen = {}
    hooks.register("session_start", lambda **kw: seen.update(kw))
    hooks.dispatch("session_start", cwd="/x", persona_id="default")
    assert seen == {"cwd": "/x", "persona_id": "default"}


def test_unknown_event_is_noop():
    hooks.dispatch("nonexistent")   # must not raise


def test_raising_handler_is_isolated_and_others_still_run():
    calls = []
    hooks.register("session_end", lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")), label="bad")
    hooks.register("session_end", lambda **kw: calls.append("good"), label="good")
    hooks.dispatch("session_end")          # must not raise
    assert calls == ["good"]               # later handler still ran


def test_handler_error_is_logged_via_tracer():
    events = []

    class FakeTracer:
        def emit(self, source, name, **kw):
            events.append((source, name, kw))

    hooks.register("session_end", lambda **kw: (_ for _ in ()).throw(ValueError("x")), label="bad")
    hooks.dispatch("session_end", tracer=FakeTracer())
    assert events and events[0][0] == "dn" and events[0][1] == "hook.error"
    assert events[0][2]["event"] == "session_end"
    assert events[0][2]["label"] == "bad"
    assert "x" in events[0][2]["error"]


def test_on_decorator_registers_and_returns_handler():
    calls = []

    @hooks.on("session_start", label="deco")
    def handler(**kw):
        calls.append(1)

    hooks.dispatch("session_start")
    assert calls == [1]
    assert callable(handler)               # decorator returns the function


def test_clear_one_event_then_all():
    hooks.register("a", lambda **kw: None)
    hooks.register("b", lambda **kw: None)
    hooks.clear("a")
    hooks.dispatch("a")                     # no handlers, no raise
    hooks.clear()                           # clears everything
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_hooks.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'harness.hooks'`.

- [ ] **Step 3: Write minimal implementation**

```python
# harness/hooks.py
"""Internal lifecycle hook registry.

A tiny, single-process pub/sub seam. Built-in consumers self-register at import
time; the TUI fires events at lifecycle moments (session_start / session_end).

This is INTERNAL only — there is no user-configurable shell-hook layer yet (see
the follow-on issue). The event names + the `dispatch(**payload)` dict are the
forward-compat contract a future shell layer will serialize to a subprocess.

Hard rules:
- `dispatch` NEVER raises.
- Each handler is isolated: a raising handler is logged (when a tracer is
  passed) and skipped; the remaining handlers still run.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# event name -> list of (handler, label)
_handlers: dict[str, list[tuple]] = {}


def register(event: str, handler, *, label: str | None = None) -> None:
    """Register *handler* to run on *event*. Handlers run in registration order."""
    _handlers.setdefault(event, []).append((handler, label or getattr(handler, "__name__", "?")))


def on(event: str, *, label: str | None = None):
    """Decorator form of register; returns the handler unchanged."""
    def deco(handler):
        register(event, handler, label=label)
        return handler
    return deco


def dispatch(event: str, *, tracer=None, **payload) -> None:
    """Fire *event*: call every handler with **payload. Never raises.

    A handler that raises is logged (via tracer.emit('dn','hook.error',…) when a
    tracer is passed, and always to the module logger) and skipped."""
    for handler, label in list(_handlers.get(event, ())):
        try:
            handler(**payload)
        except Exception as e:                      # isolate: one bad hook never breaks others
            logger.exception("hook %r for event %r raised", label, event)
            if tracer is not None:
                try:
                    tracer.emit("dn", "hook.error", event=event, label=label, error=str(e))
                except Exception:                   # tracer failure must not break dispatch either
                    logger.exception("tracer.emit failed while logging hook error")


def clear(event: str | None = None) -> None:
    """Remove handlers for *event*, or all handlers when event is None. Test-only."""
    if event is None:
        _handlers.clear()
    else:
        _handlers.pop(event, None)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_hooks.py -q`
Expected: PASS (all 7 tests).

- [ ] **Step 5: Commit**

```bash
cd /Users/alberto/Work/Quiubo/harness/.worktrees/hooks-system
git add harness/hooks.py tests/test_hooks.py
git commit -m "feat(hooks): internal lifecycle hook registry

register/on/dispatch/clear. dispatch isolates each handler and never raises;
handler errors logged via tracer.emit('dn','hook.error',…). Single-process,
internal-only (user-config shell hooks deferred to a follow-on).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Stale-sibling discovery (`harness/compress/targets.py`)

**Files:**
- Create: `harness/compress/targets.py`
- Test: `tests/compress/test_targets.py`

**Interfaces:**
- Consumes: `harness.compress.sibling.sibling_path(source) -> Path`, `sibling.freshness(src_text, sib_text) -> str` (returns `"fresh"` / `"stale"` / `"corrupt"`); `harness.persona_select.list_personas() -> list[str]`, `resolve_workspace(persona_id) -> Path`; `harness.paths.default_workspace_dir() -> Path`; `harness.config.compress_aware_pinned(persona_id="default") -> bool`.
- Produces:
  - `candidate_sources(cwd: Path | None = None) -> list[Path]` — every potential source path across persona workspaces (SOUL/IDENTITY/USER/MEMORY per persona, gated on that persona's `compress_aware`) plus cwd `AGENTS.md`/`CLAUDE.md` when `cwd` is given. Only returns paths whose file exists. Never raises.
  - `stale_existing_siblings(cwd: Path | None = None) -> list[Path]` — the subset of `candidate_sources` that (a) already have a sibling and (b) are NOT `fresh`. Pure file I/O, no model. Never raises.

**Notes for implementer:**
- `PERSONA_FILES = ["SOUL.md", "IDENTITY.md", "USER.md", "MEMORY.md"]` — include MEMORY.md (it's a source too), even though `persona.PERSONA_FILES` omits it; define the list locally in this module.
- Per-persona `compress_aware` gate: for the default workspace use persona id `"default"`; for named personas use the id from `list_personas()`. Skip a whole workspace's files when `config.compress_aware_pinned(pid)` is False.
- "needs rebuild" = `freshness(...) != "fresh"` (so `"stale"` and `"corrupt"` both qualify).
- Reading a source or sibling that is corrupt/binary must not crash: wrap reads in `try/except (OSError, UnicodeDecodeError)`; on a read error of the *sibling*, treat the source as needing rebuild (return it); on a read error of the *source*, skip it (can't compress what you can't read).

- [ ] **Step 1: Write the failing test**

```python
# tests/compress/test_targets.py
from pathlib import Path
import pytest
from harness.compress import targets, sibling


def _write_fresh_sibling(src: Path, body: str = "compressed"):
    """Build a genuinely-fresh sibling for src using the real writer."""
    from harness.compress_cli import rebuild_one
    src.write_text("verbose original text", encoding="utf-8")
    rebuild_one(src, call_model=lambda p: body, today="2026-06-30")


def _isolate(monkeypatch, tmp_path):
    """Point config + persona dirs at tmp so we read no real workspaces."""
    from harness import paths, persona_select, config
    cfg = tmp_path / "cfg"; (cfg / "agents").mkdir(parents=True)
    monkeypatch.setattr(paths, "config_dir", lambda: cfg)
    monkeypatch.setattr(paths, "default_workspace_dir", lambda: cfg / "agents" / "default")
    (cfg / "agents" / "default").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "compress_aware_pinned", lambda pid="default": True)
    return cfg


def test_stale_existing_sibling_is_selected(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    cwd = tmp_path / "proj"; cwd.mkdir()
    src = cwd / "AGENTS.md"
    _write_fresh_sibling(src)
    src.write_text("EDITED — now stale", encoding="utf-8")   # source changed → sibling stale
    result = targets.stale_existing_siblings(cwd=cwd)
    assert src in result


def test_source_without_sibling_is_ignored(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    cwd = tmp_path / "proj"; cwd.mkdir()
    (cwd / "AGENTS.md").write_text("no sibling here", encoding="utf-8")
    assert targets.stale_existing_siblings(cwd=cwd) == []


def test_fresh_sibling_is_skipped(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    cwd = tmp_path / "proj"; cwd.mkdir()
    src = cwd / "AGENTS.md"
    _write_fresh_sibling(src)                                # left fresh
    assert targets.stale_existing_siblings(cwd=cwd) == []


def test_corrupt_sibling_is_selected_and_does_not_crash(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    cwd = tmp_path / "proj"; cwd.mkdir()
    src = cwd / "AGENTS.md"; src.write_text("orig", encoding="utf-8")
    sibling.sibling_path(src).write_text("no valid header — corrupt", encoding="utf-8")
    result = targets.stale_existing_siblings(cwd=cwd)        # must not raise
    assert src in result


def test_compress_aware_off_skips_persona_files(monkeypatch, tmp_path):
    cfg = _isolate(monkeypatch, tmp_path)
    from harness import config
    monkeypatch.setattr(config, "compress_aware_pinned", lambda pid="default": False)
    soul = cfg / "agents" / "default" / "SOUL.md"
    from harness.compress_cli import rebuild_one
    soul.write_text("orig", encoding="utf-8")
    rebuild_one(soul, call_model=lambda p: "x", today="2026-06-30")
    soul.write_text("EDITED", encoding="utf-8")              # stale, but persona compress is OFF
    assert soul not in targets.stale_existing_siblings()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/compress/test_targets.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'harness.compress.targets'`.

- [ ] **Step 3: Write minimal implementation**

```python
# harness/compress/targets.py
"""Discover compressed-sibling sources and select the stale ones.

Pure file I/O — no model, never raises. Two surfaces:
  candidate_sources(cwd)        — every source we might compress (existing files).
  stale_existing_siblings(cwd)  — sources that have a sibling AND it's not fresh.

The second is what session-end auto-regen feeds to the detached worker; it never
includes a source that lacks a sibling (presence = opt-in)."""
from __future__ import annotations

from pathlib import Path

from harness import config, paths, persona_select
from harness.compress import sibling

PERSONA_FILES = ["SOUL.md", "IDENTITY.md", "USER.md", "MEMORY.md"]
CWD_FILES = ["AGENTS.md", "CLAUDE.md"]


def _persona_workspaces() -> list[tuple[str, Path]]:
    """(persona_id, workspace_dir) for the default + every named persona. Never raises."""
    out: list[tuple[str, Path]] = []
    try:
        out.append(("default", paths.default_workspace_dir()))
    except Exception:
        pass
    try:
        for pid in persona_select.list_personas():
            if pid == "default":
                continue
            try:
                out.append((pid, persona_select.resolve_workspace(pid)))
            except Exception:
                continue
    except Exception:
        pass
    return out


def candidate_sources(cwd: Path | None = None) -> list[Path]:
    """Existing source files we could compress, honoring per-persona compress_aware."""
    sources: list[Path] = []
    for pid, ws in _persona_workspaces():
        try:
            if not config.compress_aware_pinned(pid):
                continue
        except Exception:
            continue
        for name in PERSONA_FILES:
            p = ws / name
            if p.is_file():
                sources.append(p)
    if cwd is not None:
        for name in CWD_FILES:
            p = Path(cwd) / name
            if p.is_file():
                sources.append(p)
    return sources


def _needs_rebuild(src: Path) -> bool:
    """True when src has a sibling that is not fresh. Never raises."""
    sib = sibling.sibling_path(src)
    if not sib.is_file():
        return False                       # no sibling → opt-out → never touched
    try:
        src_text = src.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False                       # can't read source → can't compress it
    try:
        sib_text = sib.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return True                        # unreadable sibling → rebuild it
    return sibling.freshness(src_text, sib_text) != "fresh"


def stale_existing_siblings(cwd: Path | None = None) -> list[Path]:
    """Sources with an existing-but-not-fresh sibling. Pure file I/O, never raises."""
    return [s for s in candidate_sources(cwd) if _needs_rebuild(s)]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/compress/test_targets.py -q`
Expected: PASS (all 5 tests).

- [ ] **Step 5: Commit**

```bash
cd /Users/alberto/Work/Quiubo/harness/.worktrees/hooks-system
git add harness/compress/targets.py tests/compress/test_targets.py
git commit -m "feat(compress): stale-existing-sibling discovery (targets.py)

candidate_sources + stale_existing_siblings: per-persona/cwd walk that selects
only sources whose sibling exists AND is not fresh. Pure file I/O, never raises,
honors per-persona compress_aware. Groundwork for #188 item B too.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: The detached regen worker (`harness/compress/regen_worker.py`)

**Files:**
- Create: `harness/compress/regen_worker.py`
- Test: `tests/compress/test_regen_worker.py`

**Interfaces:**
- Consumes: `harness.compress_cli._build_call_model() -> callable | None`, `compress_cli.rebuild_one(source, *, call_model, today) -> str`; `harness.paths.load_env(cwd)`.
- Produces:
  - `regen(paths: list[str], *, call_model, today: str) -> dict` — rebuild each path; return `{"built": n, "failed": n, "skipped": n}`. A per-path failure increments `failed` and continues (never raises).
  - `main(argv: list[str] | None = None) -> int` — entrypoint: load env, resolve model (None → log + return 0), call `regen`, return 0 always.
  - `if __name__ == "__main__": raise SystemExit(main())`

- [ ] **Step 1: Write the failing test**

```python
# tests/compress/test_regen_worker.py
from harness.compress import regen_worker


def test_regen_calls_rebuild_one_per_path(tmp_path):
    a = tmp_path / "A.md"; a.write_text("a", encoding="utf-8")
    b = tmp_path / "B.md"; b.write_text("b", encoding="utf-8")
    seen = []

    def fake_call_model(prompt):
        return "compressed"

    # rebuild_one is real here; it writes siblings. Just assert it ran for both.
    res = regen_worker.regen([str(a), str(b)], call_model=fake_call_model, today="2026-06-30")
    assert res["built"] == 2
    assert (tmp_path / "A.compressed.md").is_file()
    assert (tmp_path / "B.compressed.md").is_file()


def test_regen_one_failure_does_not_stop_others(tmp_path, monkeypatch):
    a = tmp_path / "A.md"; a.write_text("a", encoding="utf-8")
    b = tmp_path / "B.md"; b.write_text("b", encoding="utf-8")
    from harness import compress_cli

    calls = []

    def flaky_rebuild(source, *, call_model, today):
        calls.append(source)
        if source.name == "A.md":
            raise RuntimeError("boom")
        return "built"

    monkeypatch.setattr(compress_cli, "rebuild_one", flaky_rebuild)
    res = regen_worker.regen([str(a), str(b)], call_model=lambda p: "x", today="2026-06-30")
    assert res["failed"] == 1 and res["built"] == 1
    assert len(calls) == 2                       # both attempted


def test_main_returns_zero_when_model_unavailable(monkeypatch, tmp_path):
    from harness import compress_cli, paths
    monkeypatch.setattr(paths, "load_env", lambda cwd: None)
    monkeypatch.setattr(compress_cli, "_build_call_model", lambda: None)
    assert regen_worker.main([str(tmp_path / "X.md")]) == 0    # no model → clean exit
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/compress/test_regen_worker.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'harness.compress.regen_worker'`.

- [ ] **Step 3: Write minimal implementation**

```python
# harness/compress/regen_worker.py
"""Detached child that rebuilds specific compressed siblings, then exits.

Spawned by session-end auto-regen as:
    python -m harness.compress.regen_worker <source-path> [<source-path> …]

Best-effort and unobserved: per-file failures are counted, never fatal; the
process always exits 0. Lives in its own module so the detached invocation does
NOT shell `dn compress` (which would relaunch the TUI argparse)."""
from __future__ import annotations

import datetime
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def regen(paths: list[str], *, call_model, today: str) -> dict:
    """Rebuild each path's sibling. Returns counts. Never raises."""
    from harness import compress_cli
    built = failed = skipped = 0
    for p in paths:
        try:
            result = compress_cli.rebuild_one(Path(p), call_model=call_model, today=today)
            if result == "built":
                built += 1
            elif result == "skipped":
                skipped += 1
            else:                            # "failed" (CompressionError)
                failed += 1
        except Exception:                    # litellm/network/anything — count + continue
            logger.exception("regen_worker: rebuild_one failed for %r", p)
            failed += 1
    return {"built": built, "failed": failed, "skipped": skipped}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        return 0
    from harness import compress_cli, paths
    paths.load_env(os.getcwd())              # so COMPRESS_MODEL / [harness] compress_model resolve
    call_model = compress_cli._build_call_model()
    if call_model is None:
        logger.info("regen_worker: no compression model configured; nothing to do")
        return 0
    today = datetime.date.today().isoformat()
    res = regen(argv, call_model=call_model, today=today)
    logger.info("regen_worker: built=%(built)d failed=%(failed)d skipped=%(skipped)d", res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/compress/test_regen_worker.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
cd /Users/alberto/Work/Quiubo/harness/.worktrees/hooks-system
git add harness/compress/regen_worker.py tests/compress/test_regen_worker.py
git commit -m "feat(compress): detached regen worker entrypoint

python -m harness.compress.regen_worker <paths…>: loads env, resolves the
compress model (none → exit 0), rebuilds each given sibling best-effort, always
exits 0. Own module so it doesn't shell dn (which relaunches the TUI argparse).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: The auto-regen consumer (`harness/compress/auto_regen.py`)

**Files:**
- Create: `harness/compress/auto_regen.py`
- Test: `tests/compress/test_auto_regen.py`

**Interfaces:**
- Consumes: `harness.hooks.register`; `harness.compress.targets.stale_existing_siblings(cwd) -> list[Path]`; `subprocess.Popen`; `sys.executable`; `harness.paths.config_dir() -> Path` (for the log fd dir).
- Produces:
  - `on_session_end(*, tracer=None, cwd=None, persona_id=None, **_) -> None` — the handler. Gate is implicit in `targets` (per-persona `compress_aware`); finds stale existing siblings; if any, spawns the detached worker with exactly those paths; logs spawn via `tracer.emit("dn","compress.regen.spawn",count=…)`. Never raises, never blocks.
  - `_spawn_worker(paths: list[str]) -> None` — the detached Popen (mirrors `jobs/supervisor.py:28`). Overridable in tests.
  - Module import side effect: `hooks.register("session_end", on_session_end, label="compress.auto_regen")`.

**Notes for implementer:**
- The handler accepts `**_` so future payload fields never break it.
- `_spawn_worker` opens a log fd under `paths.config_dir()/compress-cache/regen.log`, hands it to the child, closes it in the parent (exact pattern from `supervisor.py`). Create the parent dir with `mkdir(parents=True, exist_ok=True)`.
- Spawn must be wrapped so a Popen failure is logged, not raised (the handler is already isolated by dispatch, but defense-in-depth keeps a spawn failure from looking like a hook bug).

- [ ] **Step 1: Write the failing test**

```python
# tests/compress/test_auto_regen.py
from pathlib import Path
from harness.compress import auto_regen
from harness import hooks


def test_module_registers_for_session_end():
    # importing the module registered the handler
    assert any(lbl == "compress.auto_regen"
               for _, lbl in hooks._handlers.get("session_end", []))


def test_no_stale_means_no_spawn(monkeypatch):
    monkeypatch.setattr(auto_regen.targets, "stale_existing_siblings", lambda cwd=None: [])
    spawned = []
    monkeypatch.setattr(auto_regen, "_spawn_worker", lambda paths: spawned.append(paths))
    auto_regen.on_session_end(cwd="/x")
    assert spawned == []                         # nothing stale → no detached process


def test_stale_spawns_worker_with_exact_paths(monkeypatch):
    stale = [Path("/ws/SOUL.md"), Path("/proj/AGENTS.md")]
    monkeypatch.setattr(auto_regen.targets, "stale_existing_siblings", lambda cwd=None: stale)
    spawned = []
    monkeypatch.setattr(auto_regen, "_spawn_worker", lambda paths: spawned.append(paths))
    auto_regen.on_session_end(cwd="/proj")
    assert spawned == [["/ws/SOUL.md", "/proj/AGENTS.md"]]


def test_handler_never_raises_on_spawn_failure(monkeypatch):
    monkeypatch.setattr(auto_regen.targets, "stale_existing_siblings",
                        lambda cwd=None: [Path("/ws/SOUL.md")])

    def boom(paths):
        raise OSError("cannot fork")

    monkeypatch.setattr(auto_regen, "_spawn_worker", boom)
    auto_regen.on_session_end(cwd="/x")          # must not raise


def test_spawn_emits_tracer_breadcrumb(monkeypatch):
    monkeypatch.setattr(auto_regen.targets, "stale_existing_siblings",
                        lambda cwd=None: [Path("/ws/SOUL.md")])
    monkeypatch.setattr(auto_regen, "_spawn_worker", lambda paths: None)
    events = []

    class FakeTracer:
        def emit(self, source, name, **kw):
            events.append((source, name, kw))

    auto_regen.on_session_end(cwd="/x", tracer=FakeTracer())
    assert any(n == "compress.regen.spawn" for _, n, _ in events)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/compress/test_auto_regen.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'harness.compress.auto_regen'`.

- [ ] **Step 3: Write minimal implementation**

```python
# harness/compress/auto_regen.py
"""Session-end consumer: refresh stale EXISTING compressed siblings, detached.

Registered for the `session_end` hook at import. On fire it finds stale existing
siblings (per-persona compress_aware gating + presence=opt-in are enforced by
targets), and if any exist spawns a detached worker to rebuild exactly those —
so quitting the TUI is never blocked by an LLM call. Never raises, never
surfaces an error to the user; a failed/interrupted regen just leaves the
sibling stale (self-heals next session)."""
from __future__ import annotations

import logging
import subprocess
import sys

from harness import hooks, paths
from harness.compress import targets

logger = logging.getLogger(__name__)


def _spawn_worker(paths_list: list[str]) -> None:
    """Spawn the detached regen worker for *paths_list*. Mirrors jobs/supervisor.py."""
    log_dir = paths.config_dir() / "compress-cache"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_fd = open(log_dir / "regen.log", "a")
    try:
        subprocess.Popen(
            [sys.executable, "-m", "harness.compress.regen_worker", *paths_list],
            start_new_session=True,             # survives parent (TUI) exit
            stdout=subprocess.DEVNULL,
            stderr=log_fd,
            close_fds=True,
        )
    finally:
        log_fd.close()


def on_session_end(*, tracer=None, cwd=None, persona_id=None, **_) -> None:
    """Hook handler. Finds stale existing siblings; spawns the detached worker."""
    try:
        stale = targets.stale_existing_siblings(cwd=cwd)
    except Exception:
        logger.exception("auto_regen: discovery failed")
        return
    if not stale:
        return
    paths_list = [str(p) for p in stale]
    try:
        _spawn_worker(paths_list)
    except Exception as e:
        logger.exception("auto_regen: spawn failed")
        if tracer is not None:
            try:
                tracer.emit("dn", "compress.regen.spawn_failed", error=str(e))
            except Exception:
                logger.exception("tracer.emit failed")
        return
    if tracer is not None:
        try:
            tracer.emit("dn", "compress.regen.spawn", count=len(paths_list))
        except Exception:
            logger.exception("tracer.emit failed")


hooks.register("session_end", on_session_end, label="compress.auto_regen")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/compress/test_auto_regen.py -q`
Expected: PASS (5 tests).

> **Test-isolation note:** `test_module_registers_for_session_end` inspects the
> real registry. If other hook tests call `hooks.clear()` in teardown, run this
> file alone or rely on import order; it does not depend on `clear()`.

- [ ] **Step 5: Commit**

```bash
cd /Users/alberto/Work/Quiubo/harness/.worktrees/hooks-system
git add harness/compress/auto_regen.py tests/compress/test_auto_regen.py
git commit -m "feat(compress): session-end auto-regen consumer

Registers for the session_end hook; on fire, finds stale EXISTING siblings and
spawns the detached regen worker for exactly those paths. Never creates new
siblings, never blocks quit, never raises. Gated (via targets) on the existing
per-persona compress_aware flag.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Fire the hooks from the TUI (`harness/tui/app.py`)

**Files:**
- Modify: `harness/tui/app.py` (import section ~line 56; `on_mount` ~line 341–367; `on_unmount` ~line 1680–1694)
- Test: `tests/test_tui_hooks.py`

**Interfaces:**
- Consumes: `harness.hooks.dispatch`; `harness.compress.auto_regen` (imported once for its self-registration side effect); `self.cwd: str`; `self._current_persona() -> str | None` (existing TUI method).
- Produces: `session_start` dispatched in `on_mount` (after `_connect`), `session_end` dispatched in `on_unmount` (BEFORE tracer close). No new public API.

**Notes for implementer:**
- Add to the import block near `app.py:56` (`from harness import config as _config`):
  ```python
  from harness import hooks as _hooks
  from harness.compress import auto_regen as _auto_regen  # noqa: F401 — import-time hook registration
  ```
  The `noqa: F401` is required — the import exists for its registration side effect; do not remove it.
- `_current_persona()` already exists (used at `app.py:193`, `1590`). Use it for `persona_id`; if it can return None that's fine (payload allows None).
- In `on_mount`, dispatch `session_start` AFTER the `_connect()` try/except and the cron-autostart block (end of the method), wrapped so a hook never breaks mount — but since `dispatch` already never raises, a bare call is acceptable; still pass `tracer=self._tracer`.
- In `on_unmount`, dispatch `session_end` as the FIRST action (before the pending-perm/teardown/tracer-close logic) so the tracer is still open to record breadcrumbs. Pass `tracer=self._tracer, cwd=self.cwd, persona_id=self._current_persona()`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tui_hooks.py
"""The TUI fires session_start on mount and session_end on unmount.

These are unit tests on the dispatch wiring, not full Pilot runs: we call a thin
seam. Implementer: if app.py exposes on_mount/on_unmount as coroutines that need
a mounted app, instead assert the dispatch via monkeypatching hooks.dispatch and
driving the smallest path. The REQUIRED behavior to lock:
  - on_unmount dispatches 'session_end' with cwd + persona_id, BEFORE tracer.close().
"""
from harness import hooks


def test_session_end_dispatched_before_tracer_close(monkeypatch):
    from harness.tui import app as app_mod

    order = []
    monkeypatch.setattr(hooks, "dispatch",
                        lambda event, **kw: order.append(("dispatch", event)))

    class FakeTracer:
        def close(self):
            order.append(("tracer", "close"))
        def emit(self, *a, **k):
            pass

    # Build a minimal object exposing just what on_unmount touches.
    class Stub:
        _pending_perm = None
        _cm = None
        _tracer = FakeTracer()
        cwd = "/proj"
        def _current_persona(self):
            return "default"
        def log(self, *a, **k):
            pass

    stub = Stub()
    import asyncio
    asyncio.run(app_mod.HarnessTui.on_unmount(stub))     # call unbound with stub

    assert ("dispatch", "session_end") in order
    assert order.index(("dispatch", "session_end")) < order.index(("tracer", "close"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_tui_hooks.py -q`
Expected: FAIL — `session_end` not dispatched (assertion error / not in order).

- [ ] **Step 3: Write minimal implementation**

In the import block (after `from harness import config as _config`):

```python
from harness import hooks as _hooks
from harness.compress import auto_regen as _auto_regen  # noqa: F401 — import-time hook registration
```

At the END of `on_mount` (after the cron-autostart try/except):

```python
        _hooks.dispatch("session_start", tracer=self._tracer,
                        cwd=self.cwd, persona_id=self._current_persona())
```

At the START of `on_unmount` (first lines of the method body):

```python
    async def on_unmount(self) -> None:
        # Fire session_end while the tracer is still open so hooks can log.
        _hooks.dispatch("session_end", tracer=self._tracer,
                        cwd=self.cwd, persona_id=self._current_persona())
        if self._pending_perm is not None and not self._pending_perm.done():
            ...  # (existing body unchanged)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_tui_hooks.py -q`
Expected: PASS.

- [ ] **Step 5: Run the broader TUI + hooks suites to confirm no regressions**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_hooks.py tests/compress/ tests/test_tui_hooks.py -q`
Expected: PASS (Pilot flakiness not in scope here).

- [ ] **Step 6: Commit**

```bash
cd /Users/alberto/Work/Quiubo/harness/.worktrees/hooks-system
git add harness/tui/app.py tests/test_tui_hooks.py
git commit -m "feat(tui): fire session_start/session_end hooks

on_mount dispatches session_start; on_unmount dispatches session_end BEFORE the
tracer closes (so hooks can log). Imports auto_regen for its registration side
effect. app.py stays ignorant of compression — it only dispatches events.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Wire `targets` into `dn compress` default targets (#188 item B groundwork)

**Files:**
- Modify: `harness/compress_cli.py` (`_default_targets`, ~line 55–59)
- Test: `tests/compress/test_cli.py` (add a case)

**Interfaces:**
- Consumes: `harness.compress.targets.candidate_sources(cwd) -> list[Path]`.
- Produces: `_default_targets()` now returns cwd `AGENTS.md`/`CLAUDE.md` PLUS every existing persona voice/memory file (via `candidate_sources`), de-duplicated, instead of cwd-only.

**Notes for implementer:**
- Keep behavior additive: with no personas configured and only cwd files, the result is the same set as before. `candidate_sources(cwd=Path.cwd())` already returns cwd files + persona files; just dedupe and return existing paths.
- Do NOT change the `--skills` or `--status` paths.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/compress/test_cli.py
def test_default_targets_includes_persona_voice_files(monkeypatch, tmp_path):
    from harness import compress_cli, paths, config, persona_select
    cfg = tmp_path / "cfg"; (cfg / "agents" / "default").mkdir(parents=True)
    monkeypatch.setattr(paths, "config_dir", lambda: cfg)
    monkeypatch.setattr(paths, "default_workspace_dir", lambda: cfg / "agents" / "default")
    monkeypatch.setattr(config, "compress_aware_pinned", lambda pid="default": True)
    soul = cfg / "agents" / "default" / "SOUL.md"; soul.write_text("s", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    targets = compress_cli._default_targets()
    assert soul in [p.resolve() for p in targets] or soul in targets
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/compress/test_cli.py::test_default_targets_includes_persona_voice_files -q`
Expected: FAIL — SOUL.md not in the cwd-only target list.

- [ ] **Step 3: Write minimal implementation**

```python
# harness/compress_cli.py — replace _default_targets
def _default_targets() -> list[Path]:
    """Default rebuild targets: cwd AGENTS.md/CLAUDE.md + existing persona
    voice/memory files (SOUL/IDENTITY/USER/MEMORY) across workspaces. Closes
    #188 item B — `dn compress` with no args now covers the voice files. Pass
    explicit paths to override."""
    from harness.compress import targets
    seen: list[Path] = []
    for p in targets.candidate_sources(cwd=Path.cwd()):
        if p not in seen:
            seen.append(p)
    return seen
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/compress/test_cli.py -q`
Expected: PASS (new test + existing CLI tests).

- [ ] **Step 5: Commit**

```bash
cd /Users/alberto/Work/Quiubo/harness/.worktrees/hooks-system
git add harness/compress_cli.py tests/compress/test_cli.py
git commit -m "feat(compress): dn compress default targets cover persona files (#188 item B)

_default_targets now returns cwd AGENTS/CLAUDE + every existing persona
SOUL/IDENTITY/USER/MEMORY via targets.candidate_sources, deduped. Additive:
cwd-only setups are unchanged.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Documentation (`docs/hooks.md` + `docs/compress-aware.md`)

**Files:**
- Create: `docs/hooks.md`
- Modify: `docs/compress-aware.md` (add a short auto-refresh note in the "Regenerating siblings" section, ~line 81–89)

**Interfaces:** none (docs).

**Acceptance bar (the task is NOT done until all four are present in `docs/hooks.md`):**
1. What the hook system is (internal Python registry, single TUI process) and what it is NOT yet (no user-config shell hooks — link the follow-on issue placeholder).
2. Event catalog table (`session_start`, `session_end`) with fire-point + payload fields, matching the code.
3. How to add a consumer (the `hooks.register("session_end", fn)` pattern + the never-raise/isolation contract + self-register-at-import convention).
4. The `auto_regen` worked example.

- [ ] **Step 1: Write `docs/hooks.md`**

```markdown
# Hooks — internal lifecycle events

Done has a small **internal hook system**: built-in code can run at lifecycle
moments (session start / end). It is Python-only and lives in the TUI process.

> **Internal only (for now).** There is no user-configurable shell-hook layer
> yet — you cannot declare `run ./script.sh on session_end` in config. That is
> planned as a follow-on (the event names and payloads below are the contract
> that layer will build on). Today, hooks are registered in Python by the
> harness itself.

## Events

| Event | Fired | Payload (keyword args) |
| --- | --- | --- |
| `session_start` | TUI mount, after the agent connects | `tracer`, `cwd: str`, `persona_id: str \| None` |
| `session_end` | TUI unmount, before the trace file is closed | `tracer`, `cwd: str`, `persona_id: str \| None` |

`session_end` fires before the tracer closes so handlers can record
breadcrumbs. `persona_id` may be `None`; consumers that touch files should not
require it (walk all persona workspaces instead).

## Adding a consumer

```python
from harness import hooks

def on_session_end(*, tracer=None, cwd=None, persona_id=None, **_):
    ...  # do your thing

hooks.register("session_end", on_session_end, label="my.consumer")
```

Rules a handler MUST honor:

- **Never block.** `session_end` runs during app teardown. Do slow work in a
  detached subprocess (see `auto_regen` below), not inline.
- **Never assume it won't be skipped.** A raising handler is caught, logged via
  `tracer.emit("dn", "hook.error", …)`, and skipped — it never breaks the
  session or other handlers. `dispatch` itself never raises.
- **Accept `**_`.** Take the payload as keyword args plus `**_` so new payload
  fields never break you.
- **Self-register at import.** Put `hooks.register(...)` at module top level and
  import the module once at TUI startup (see `harness/tui/app.py`), so
  registration is deterministic and one-time.

## Worked example: `auto_regen`

`harness/compress/auto_regen.py` keeps compressed siblings fresh. On
`session_end` it finds stale **existing** siblings (it never creates new ones)
via `harness/compress/targets.py`, and if any are stale it spawns a detached
worker (`python -m harness.compress.regen_worker <paths…>`) to rebuild exactly
those. Quitting the TUI is never blocked; a failed regen just leaves the sibling
stale (it heals next session). It is gated on the existing per-persona
`compress_aware` flag — no separate setting.

See `docs/compress-aware.md` for the compression feature itself.
```

- [ ] **Step 2: Add the auto-refresh note to `docs/compress-aware.md`**

In the "Regenerating siblings: `dn compress`" section, add after the intro paragraph:

```markdown
Siblings also **auto-refresh on session end**: when you quit the TUI, Done
detects any *existing* sibling that has gone stale and rebuilds it in the
background (detached, never blocking quit). It never creates a sibling you
didn't ask for — `dn compress <path>` is still how you opt a file in the first
time. See `docs/hooks.md` for the mechanism.
```

- [ ] **Step 3: Verify the doc covers the acceptance bar**

Re-read `docs/hooks.md`: confirm all four required points (what-it-is + not-yet, event table matching code, how-to-add-a-consumer with the contract, auto_regen example) are present.

- [ ] **Step 4: Commit**

```bash
cd /Users/alberto/Work/Quiubo/harness/.worktrees/hooks-system
git add docs/hooks.md docs/compress-aware.md
git commit -m "docs(hooks): document the internal hook system + auto-refresh

docs/hooks.md: events (session_start/end), payloads, how to add a consumer,
the never-raise contract, and auto_regen as the worked example. compress-aware
doc notes siblings auto-refresh on session end.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Full-suite green gate + follow-on issue

**Files:** none (verification + issue filing).

- [ ] **Step 1: Run the full suite**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/ -q`
Expected: PASS except the known-green baseline (the `test_service_launchd` failure and any Pilot-cluster flake). If a NEW test fails, it's a regression — fix before proceeding.

- [ ] **Step 2: File the follow-on issue**

```bash
gh issue create --title "User-configurable shell hooks (on top of the internal hook registry)" \
  --body "The internal hook registry (harness/hooks.py, events session_start/session_end) is internal-Python only. Follow-on: let \`dn\` users declare shell commands against these lifecycle events — config schema, shell exec, matchers, timeouts, and a security model for arbitrary command execution. The registry's event names + the dispatch(**payload) dict (tracer/cwd/persona_id) are designed to be exactly what that layer serializes to JSON. See docs/hooks.md and the spec docs/superpowers/specs/2026-06-30-hooks-system-design.md."
```

- [ ] **Step 3: No commit** (verification task). Proceed to the final whole-branch review.

---

## Self-Review

**1. Spec coverage:**
- Registry (register/on/dispatch/clear, isolation, never-raise) → Task 1 ✓
- Events `session_start`/`session_end` + payloads → Tasks 1 (contract), 5 (fired) ✓
- Existing-siblings-only + stale selection + per-persona gate + never-raise → Task 2 ✓
- Detached worker entrypoint → Task 3 ✓
- auto_regen consumer (gate, spawn exact paths, never block/raise, breadcrumb) → Task 4 ✓
- TUI dispatch (session_end before tracer close; app ignorant of compression) → Task 5 ✓
- Shared targets / #188 item B groundwork → Task 2 (built) + Task 6 (wired) ✓
- Docs (`docs/hooks.md` four points + compress-aware note) → Task 7 ✓
- Full-suite gate + follow-on issue → Task 8 ✓

**2. Placeholder scan:** No TBD/TODO; every code step has complete code; every test has assertions. ✓

**3. Type consistency:** `stale_existing_siblings(cwd) -> list[Path]` consumed identically in Tasks 4 & (via candidate_sources) 6; `rebuild_one(source, *, call_model, today) -> str` used identically in Task 3; `_build_call_model() -> callable|None` consistent; `freshness(...) != "fresh"` used in Task 2 only; `hooks.register(event, handler, *, label)` consistent across Tasks 1/4/7. ✓

**4. Known wrinkle flagged:** Task 5's test calls the unbound coroutine with a stub — implementer may adapt if `on_unmount` touches more state; the locked requirement (session_end before tracer close) is explicit. Task 4's registry-inspection test documented re: `clear()` isolation.
