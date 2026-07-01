# Proxy Config Drift Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect when the CLIProxyAPI `config.yaml` is missing or stale relative to current env (`NEURALWATT_API_KEY` changed, or it was never installed), auto-install only when never-installed (safe, no running process to disturb), and warn-only (never auto-restart) when an already-running proxy has drifted.

**Architecture:** One new pure function `config_drift()` in `harness/proxy_service/config_gen.py` that diffs `generate()`'s current output against what's on disk. Two consumers: (1) a new `harness/proxy_service/auto_install.py` module, following the exact `harness/compress/auto_regen.py` pattern, registered on the `session_start` hook (not `session_end`) to spawn a detached `dn proxy install` only when config is missing; (2) `lifecycle.status()` and `tui/app.py::on_mount` both call `config_drift()` and print a one-line warning when drifted. No path ever auto-restarts an already-running proxy.

**Tech Stack:** Python stdlib (`subprocess.Popen` for detached spawn), existing `harness.hooks` pub/sub registry, pytest + `monkeypatch` for tests (no live proxy process required anywhere).

## Global Constraints

- No new marker/hash file — `config_drift()` diffs `generate()`'s output directly against `config_path()`'s contents (spec section 1).
- `config_drift()` must accept the same `env=` parameter `generate()` accepts, defaulting to `os.environ` (spec section 1).
- Auto-install fires unconditionally on `session_start` (not `session_end`) and ONLY when `config_drift() == "missing"` — never on `"drifted"` (spec section 2, confirmed in brainstorming).
- No code path anywhere auto-restarts an already-running proxy — hard constraint, not a preference (spec section 3).
- Detached spawn must follow the `auto_regen.py` precedent exactly: `subprocess.Popen(..., start_new_session=True, stdout=subprocess.DEVNULL, stderr=<log file>, close_fds=True)`, never raise past the hook handler.
- `config_drift()` calls must use the caller's own already-resolved `os.environ` (post that context's `load_env()`), never a fresh independent resolution — avoids the TUI-vs-`dn proxy` env asymmetry (spec section on env-source asymmetry).
- Every new test must run without a live CLIProxyAPI process — pure function + `monkeypatch`, matching `tests/test_proxy_config_gen.py` conventions.
- Test command: `.venv/bin/python -m pytest tests/ -q` (target `tests/` only, per `AGENTS.md`).

---

### Task 1: `config_drift()` in `config_gen.py`

**Files:**
- Modify: `harness/proxy_service/config_gen.py` (add `config_drift`, no changes to `generate()`)
- Test: `tests/test_proxy_config_gen.py` (append tests)

**Interfaces:**
- Consumes: `generate(port: int = 8317, *, env=None) -> str` (existing), `paths.config_path() -> Path` (existing, `harness/proxy_service/paths.py:12`)
- Produces: `config_drift(*, env=None) -> str`, returning one of the literal strings `"missing"`, `"drifted"`, `"ok"`. Tasks 2 and 3 both call this exact function with this exact contract.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_proxy_config_gen.py`:

```python
def test_config_drift_missing_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "config_path", lambda: tmp_path / "config.yaml")
    assert config_gen.config_drift(env={}) == "missing"


def test_config_drift_ok_when_file_matches_generate(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr(paths, "config_path", lambda: cfg_path)
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)
    env = {"NEURALWATT_API_KEY": "nw-123"}
    cfg_path.write_text(config_gen.generate(env=env))
    assert config_gen.config_drift(env=env) == "ok"


def test_config_drift_drifted_when_key_changed(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr(paths, "config_path", lambda: cfg_path)
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)
    cfg_path.write_text(config_gen.generate(env={"NEURALWATT_API_KEY": "old-key"}))
    assert config_gen.config_drift(env={"NEURALWATT_API_KEY": "new-key"}) == "drifted"


def test_config_drift_drifted_when_key_removed(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr(paths, "config_path", lambda: cfg_path)
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)
    cfg_path.write_text(config_gen.generate(env={"NEURALWATT_API_KEY": "nw-123"}))
    assert config_gen.config_drift(env={}) == "drifted"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_proxy_config_gen.py -k config_drift -v`
Expected: FAIL with `AttributeError: module 'harness.proxy_service.config_gen' has no attribute 'config_drift'`

- [ ] **Step 3: Write minimal implementation**

Append to `harness/proxy_service/config_gen.py` (after `generate()`, before `ensure_management_password()`):

```python
def config_drift(*, env=None) -> str:
    """Compare config.yaml on disk against what generate() would produce now.

    Returns "missing" (no config.yaml yet — never installed), "drifted"
    (config.yaml exists but differs from current generate() output — e.g.
    NEURALWATT_API_KEY changed since the last install/upgrade), or "ok"
    (matches). Effectively pure — generate() calls paths.data_dir(), which
    mkdirs the data dir as a side effect (harmless, idempotent, same dir
    install() creates anyway), but this function never writes config.yaml
    itself and never raises on a missing file.
    """
    cfg_path = paths.config_path()
    if not cfg_path.exists():
        return "missing"
    current = generate(env=env)
    on_disk = cfg_path.read_text()
    return "ok" if current == on_disk else "drifted"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_proxy_config_gen.py -v`
Expected: all PASS, including the 4 new `config_drift` tests and all pre-existing tests in the file.

- [ ] **Step 5: Manually verify CLIProxyAPI does not rewrite config.yaml on boot**

This is the caveat flagged in the spec: `config_gen.py`'s existing comment
notes some secrets are bcrypt-hashed by CLIProxyAPI on boot. If the running
binary rewrites `config.yaml` in place after `install()` writes it, a
byte-compare in `config_drift()` would show permanent false `"drifted"` even
with zero actual env changes — a unit test cannot catch this since it
requires the real binary. Verify once, manually, in this worktree or any
machine with the proxy already installed:

```bash
dn proxy status                                        # confirm running
sha256sum ~/.local/share/harness/proxy/config.yaml      # note the hash
sleep 5
sha256sum ~/.local/share/harness/proxy/config.yaml      # re-check after boot has settled
```

If the hash is stable, `config_drift()`'s full-text compare is safe as
written — proceed. If the hash changes without any env/install action on your
part, `config_drift()` must be narrowed to compare only the `api-key:` and
`models:` lines instead of full-file equality — if so, stop here and adjust
Task 1's implementation before continuing to Task 2, since Tasks 2 and 3 both
depend on `config_drift()`'s return value being trustworthy.

- [ ] **Step 6: Commit**

```bash
git add harness/proxy_service/config_gen.py tests/test_proxy_config_gen.py
git commit -m "$(cat <<'EOF'
feat(proxy): add config_drift() to detect missing/stale config.yaml

Pure diff of generate()'s current output against what's on disk — no
new marker/hash file needed since generate() is already deterministic.
Feeds the session_start auto-install (Task 2) and the status()/TUI
warn-only surfaces (Task 3). Part of #279.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: auto-install-on-missing via `session_start` hook

**Files:**
- Create: `harness/proxy_service/auto_install.py`
- Modify: `harness/tui/app.py` (one import line, for side-effect registration — mirrors line 61's `_auto_regen` import)
- Test: `tests/proxy_service/test_auto_install.py` (new file — check whether `tests/proxy_service/` exists as a directory first; if not, create it, it's already listed as an existing test location alongside the flat `tests/test_proxy_*.py` files)

**Interfaces:**
- Consumes: `config_gen.config_drift(*, env=None) -> str` (Task 1), `harness.hooks.register(event: str, handler, *, label: str | None = None) -> None` (existing, `harness/hooks.py:25`)
- Produces: `auto_install.on_session_start(*, tracer=None, cwd=None, persona_id=None, **_) -> None` — registered as the `session_start` handler. `auto_install._spawn_install() -> None` — the detached spawn, monkeypatched in tests exactly like `auto_regen._spawn_worker`.

- [ ] **Step 1: Write the failing tests**

Create `tests/proxy_service/test_auto_install.py` (create the `tests/proxy_service/__init__.py` if the directory isn't already a package — check first with `ls tests/proxy_service/`):

```python
from harness.proxy_service import auto_install
from harness import hooks


def test_module_registers_for_session_start():
    # Mirrors tests/compress/test_auto_regen.py's registration test: assert the
    # registration MECHANISM rather than relying on import order (other test
    # files' hooks.clear() teardown can wipe the import-time registration).
    hooks.register("session_start", auto_install.on_session_start, label="proxy.auto_install")
    assert any(lbl == "proxy.auto_install" and h is auto_install.on_session_start
               for h, lbl in hooks._handlers.get("session_start", []))


def test_ok_drift_means_no_spawn(monkeypatch):
    monkeypatch.setattr(auto_install.config_gen, "config_drift", lambda env=None: "ok")
    spawned = []
    monkeypatch.setattr(auto_install, "_spawn_install", lambda: spawned.append(True))
    auto_install.on_session_start(cwd="/x")
    assert spawned == []


def test_drifted_means_no_spawn(monkeypatch):
    # Drifted (already installed, just stale) must NEVER auto-restart — warn-only,
    # handled by Task 3. This handler only acts on "missing".
    monkeypatch.setattr(auto_install.config_gen, "config_drift", lambda env=None: "drifted")
    spawned = []
    monkeypatch.setattr(auto_install, "_spawn_install", lambda: spawned.append(True))
    auto_install.on_session_start(cwd="/x")
    assert spawned == []


def test_missing_spawns_install(monkeypatch):
    monkeypatch.setattr(auto_install.config_gen, "config_drift", lambda env=None: "missing")
    spawned = []
    monkeypatch.setattr(auto_install, "_spawn_install", lambda: spawned.append(True))
    auto_install.on_session_start(cwd="/x")
    assert spawned == [True]


def test_two_concurrent_sessions_both_missing_neither_raises(monkeypatch):
    # Multi-session race (flagged in caveman review): two sessions launched close
    # together can both observe "missing" and both call the handler. This
    # handler itself does no locking — it relies on install()'s own steps being
    # idempotent-safe (download.download_and_install checks an existing stamp;
    # OS-service registration checks .exists()). This test only proves the
    # HANDLER's contract holds under a double-fire: neither call raises, and
    # both attempt a spawn (real idempotency is install()'s existing behavior,
    # exercised separately in tests/test_proxy_lifecycle.py, not re-tested here).
    monkeypatch.setattr(auto_install.config_gen, "config_drift", lambda env=None: "missing")
    spawned = []
    monkeypatch.setattr(auto_install, "_spawn_install", lambda: spawned.append(True))
    auto_install.on_session_start(cwd="/session-a")
    auto_install.on_session_start(cwd="/session-b")
    assert spawned == [True, True]          # both fired; no exception from either


def test_handler_never_raises_on_spawn_failure(monkeypatch):
    monkeypatch.setattr(auto_install.config_gen, "config_drift", lambda env=None: "missing")

    def boom():
        raise OSError("cannot fork")

    monkeypatch.setattr(auto_install, "_spawn_install", boom)
    auto_install.on_session_start(cwd="/x")          # must not raise


def test_spawn_emits_tracer_breadcrumb(monkeypatch):
    monkeypatch.setattr(auto_install.config_gen, "config_drift", lambda env=None: "missing")
    monkeypatch.setattr(auto_install, "_spawn_install", lambda: None)
    events = []

    class FakeTracer:
        def emit(self, source, name, **kw):
            events.append((source, name, kw))

    auto_install.on_session_start(cwd="/x", tracer=FakeTracer())
    assert any(n == "proxy.auto_install.spawn" for _, n, _ in events)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/proxy_service/test_auto_install.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'harness.proxy_service.auto_install'`

- [ ] **Step 3: Write minimal implementation**

Create `harness/proxy_service/auto_install.py`:

```python
"""Session-start consumer: auto-install the proxy when config.yaml is missing.

Registered for the `session_start` hook at import. Fires unconditionally on
every session start; only acts when config_gen.config_drift() reports
"missing" (proxy never installed — safe, no running process to disturb).
Never acts on "drifted" (already installed, just stale) — that case is
warn-only (see lifecycle.status() and tui/app.py::on_mount), because an
already-running proxy is a machine-global service other sessions/cron may
depend on; auto-restarting it here would be unsafe. Spawns a detached
`dn proxy install`, mirroring harness/compress/auto_regen.py: never blocks
session startup, never raises past this handler, self-heals next session on
failure."""
from __future__ import annotations

import logging
import subprocess
import sys

from harness import hooks
from harness.proxy_service import config_gen, paths

logger = logging.getLogger(__name__)


def _spawn_install() -> None:
    """Spawn `dn proxy install` detached. Mirrors auto_regen._spawn_worker."""
    log_dir = paths.data_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_fd = open(log_dir / "auto-install.log", "a")
    try:
        subprocess.Popen(
            [sys.executable, "-m", "harness.tui_main", "proxy", "install"],
            start_new_session=True,             # survives parent (TUI) exit
            stdout=subprocess.DEVNULL,
            stderr=log_fd,
            close_fds=True,
        )
    finally:
        log_fd.close()


def on_session_start(*, tracer=None, cwd=None, persona_id=None, **_) -> None:
    """Hook handler. Spawns a detached install only when config is missing."""
    try:
        drift = config_gen.config_drift()
    except Exception:
        logger.exception("auto_install: drift check failed")
        return
    if drift != "missing":
        return
    try:
        _spawn_install()
    except Exception as e:
        logger.exception("auto_install: spawn failed")
        if tracer is not None:
            try:
                tracer.emit("dn", "proxy.auto_install.spawn_failed", error=str(e))
            except Exception:
                logger.exception("tracer.emit failed")
        return
    if tracer is not None:
        try:
            tracer.emit("dn", "proxy.auto_install.spawn")
        except Exception:
            logger.exception("tracer.emit failed")


hooks.register("session_start", on_session_start, label="proxy.auto_install")
```

The invocation `[sys.executable, "-m", "harness.tui_main", "proxy", "install"]` is
confirmed correct: `pyproject.toml`'s `[project.scripts]` maps `dn` to
`harness.tui_main:main`, and `tui_main.py::main` reads `sys.argv[1:]`,
checking `raw[0] == "proxy"` to dispatch to `proxy_service.cli.run(raw[1:])`
— so `-m harness.tui_main proxy install` reaches the exact same
`proxy_cli.run(["install"])` path that `dn proxy install` does.

Then wire the import-time registration into the TUI, mirroring line 61 exactly. Read `harness/tui/app.py` lines 55-65 first to place it correctly:

```python
from harness.proxy_service import auto_install as _proxy_auto_install  # noqa: F401 — import-time hook registration
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/proxy_service/test_auto_install.py -v`
Expected: all 7 tests PASS.

Also run the full suite once to confirm the new `app.py` import doesn't break anything:
Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/proxy_service/auto_install.py harness/tui/app.py tests/proxy_service/test_auto_install.py
git commit -m "$(cat <<'EOF'
feat(proxy): auto-install on session_start when config.yaml is missing

New session_start hook, following the harness/compress/auto_regen.py
detached-spawn precedent. Only acts on config_drift()=="missing" — a
"drifted" (already-installed) config is never auto-remediated here,
since that would mean restarting a machine-global proxy other
sessions/cron may depend on (handled instead by warn-only surfaces in
the next task). Part of #279.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: warn-only drift surfaces in `status()` and TUI `on_mount`

**Files:**
- Modify: `harness/proxy_service/lifecycle.py:26-47` (`status()`)
- Modify: `harness/tui/app.py` (new method `HarnessTui._check_proxy_config_drift`, near `_decide_cron_autostart`; one-line call added to `on_mount` near lines ~416-429)
- Test: `tests/test_proxy_lifecycle.py` (append), `tests/test_tui_widgets.py` (append — or `tests/test_tui_hooks.py` if its conventions fit better on inspection)

**Interfaces:**
- Consumes: `config_gen.config_drift(*, env=None) -> str` (Task 1)
- Produces: `status()`'s return string now includes a drift warning line when applicable; new method `HarnessTui._check_proxy_config_drift(self) -> None` logs a one-line drift warning, called from `on_mount`. No new public interface beyond this — leaf task, nothing downstream depends on its outputs.

- [ ] **Step 1: Write the failing test for `status()`**

Append to `tests/test_proxy_lifecycle.py`:

```python
def test_status_warns_when_config_drifted(monkeypatch):
    monkeypatch.setattr(lifecycle.config_gen, "ensure_management_password", lambda: "pw")
    monkeypatch.setattr(lifecycle.management, "is_ready", lambda pw: False)
    monkeypatch.setattr(lifecycle.config_gen, "config_drift", lambda: "drifted")
    out = lifecycle.status()
    assert "proxy config stale" in out.lower()
    assert "dn proxy upgrade" in out


def test_status_no_warning_when_config_ok(monkeypatch):
    monkeypatch.setattr(lifecycle.config_gen, "ensure_management_password", lambda: "pw")
    monkeypatch.setattr(lifecycle.management, "is_ready", lambda pw: False)
    monkeypatch.setattr(lifecycle.config_gen, "config_drift", lambda: "ok")
    out = lifecycle.status()
    assert "stale" not in out.lower()


def test_status_no_warning_when_config_missing(monkeypatch):
    # "missing" means never-installed — Task 2's auto_install handles this on
    # session_start. status() should not also nag about it as "stale".
    monkeypatch.setattr(lifecycle.config_gen, "ensure_management_password", lambda: "pw")
    monkeypatch.setattr(lifecycle.management, "is_ready", lambda pw: False)
    monkeypatch.setattr(lifecycle.config_gen, "config_drift", lambda: "missing")
    out = lifecycle.status()
    assert "stale" not in out.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_proxy_lifecycle.py -k drift -v`
Expected: FAIL — `"proxy config stale"` not found in `status()`'s current output (the drift check doesn't exist there yet).

- [ ] **Step 3: Write minimal implementation for `status()`**

In `harness/proxy_service/lifecycle.py`, modify `status()` (currently lines 26-47):

```python
def status() -> str:
    """Return a human-readable status string.

    Composes management.is_ready (connection check) with provider auth status,
    plus a config-drift warning (never an auto-restart — see auto_install.py
    for the only safe automatic path, which handles "missing" only).
    Never crashes when the proxy is not running — is_ready returns False on any
    connection error, so we just report "not running" gracefully.
    """
    pw = config_gen.ensure_management_password()
    drift = config_gen.config_drift()
    drift_line = (
        "  proxy config stale — run `dn proxy upgrade` to pick up changes.\n"
        if drift == "drifted" else ""
    )
    if not management.is_ready(pw):
        return drift_line + "CLIProxyAPI: not running (or not reachable on localhost:8317)"

    # Proxy is up — report per-provider auth status.
    lines = [drift_line + "CLIProxyAPI: running"]
    for provider in management._AUTH_URL_PATHS:
        try:
            r = management._get("get-auth-status", pw)
            body = r.json()
            pstatus = body.get(provider, {}).get("status", "unknown") if isinstance(body, dict) else "unknown"
            lines.append(f"  {provider}: {pstatus}")
        except Exception as exc:
            lines.append(f"  {provider}: error ({exc})")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_proxy_lifecycle.py -v`
Expected: all PASS, including 3 new drift tests and all pre-existing tests.

- [ ] **Step 5: Add a small testable method + wire it into `on_mount`**

`on_mount` itself is a long coroutine with many side effects (`_connect`,
cron autostart, etc.) — not a good direct unit-test target. The codebase's
own convention for this (see `_decide_cron_autostart`, same file, called
from `on_mount` and separately unit-tested) is to factor the logic into a
small synchronous method and unit-test that directly, then wire a one-line
call into `on_mount`. Follow that pattern here.

Write the failing test first. Check `tests/test_tui_widgets.py` (one of the
files that already covers `HarnessTui` methods) for the exact construction
pattern used to instantiate/call a bound method on `HarnessTui` without a
full Pilot mount, and mirror it. If no lighter-weight pattern exists there,
use the same unbound-call-with-Stub approach as
`tests/test_tui_hooks.py::test_session_end_dispatched_before_tracer_close`
(call the plain function with a minimal stub object exposing only `.log`):

```python
def test_check_proxy_config_drift_logs_when_drifted(monkeypatch):
    from harness.tui import app as app_mod

    monkeypatch.setattr(
        "harness.proxy_service.config_gen.config_drift", lambda: "drifted"
    )
    logged = []

    class Stub:
        def log(self, msg):
            logged.append(msg)

    app_mod.HarnessTui._check_proxy_config_drift(Stub())
    assert any("proxy config stale" in m.lower() for m in logged)


def test_check_proxy_config_drift_silent_when_ok(monkeypatch):
    from harness.tui import app as app_mod

    monkeypatch.setattr(
        "harness.proxy_service.config_gen.config_drift", lambda: "ok"
    )
    logged = []

    class Stub:
        def log(self, msg):
            logged.append(msg)

    app_mod.HarnessTui._check_proxy_config_drift(Stub())
    assert logged == []


def test_check_proxy_config_drift_never_raises(monkeypatch):
    from harness.tui import app as app_mod

    def boom():
        raise RuntimeError("drift check exploded")

    monkeypatch.setattr("harness.proxy_service.config_gen.config_drift", boom)
    logged = []

    class Stub:
        def log(self, msg):
            logged.append(msg)

    app_mod.HarnessTui._check_proxy_config_drift(Stub())  # must not raise
```

Add these to `tests/test_tui_widgets.py` (or `tests/test_tui_hooks.py` if
that file's existing conventions fit better on inspection — check both
before choosing).

Run: `.venv/bin/python -m pytest tests/test_tui_widgets.py -k proxy_config_drift -v`
Expected: FAIL — `AttributeError: type object 'HarnessTui' has no attribute
'_check_proxy_config_drift'`.

Now implement. In `harness/tui/app.py`, add a new method near
`_decide_cron_autostart` (same class, `HarnessTui`):

```python
    def _check_proxy_config_drift(self) -> None:
        """Warn (never auto-restart) when config.yaml has drifted from current
        env — e.g. NEURALWATT_API_KEY changed since the last `dn proxy
        install`/`upgrade`. "missing" is handled separately by the
        auto_install session_start hook (harness/proxy_service/auto_install.py);
        this only covers "drifted". Never raises past this method."""
        try:
            from harness.proxy_service import config_gen as _proxy_config_gen
            if _proxy_config_gen.config_drift() == "drifted":
                self.log("proxy config stale — run `dn proxy upgrade` to pick up NEURALWATT_API_KEY changes")
        except Exception as e:
            self.log(f"proxy config drift check skipped: {e!r}")
```

Then wire a one-line call into `on_mount`, immediately after the existing
cron-autostart try/except block (before the final
`_hooks.dispatch("session_start", ...)` line):

```python
        self._check_proxy_config_drift()
```

- [ ] **Step 6: Run the new tests and the full suite**

Run: `.venv/bin/python -m pytest tests/test_proxy_lifecycle.py tests/proxy_service/test_auto_install.py tests/test_proxy_config_gen.py tests/test_tui_widgets.py -v`
Expected: all PASS.

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all PASS (full suite, confirms the `app.py` edit didn't regress anything else).

- [ ] **Step 7: Commit**

```bash
git add harness/proxy_service/lifecycle.py harness/tui/app.py tests/test_proxy_lifecycle.py tests/test_tui_widgets.py
git commit -m "$(cat <<'EOF'
feat(proxy): warn-only surfaces for drifted config in status() + TUI

lifecycle.status() and tui/app.py::on_mount both call config_drift()
and print a one-line "run dn proxy upgrade" warning when drifted.
Never auto-restarts an already-running proxy — that's a hard
constraint from the #279 review (machine-global service other
sessions/cron may depend on). Completes #279.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Final step: close out the issue

After Task 3 lands and full tests are green, this plan's execution should end with opening a PR against `main` (per the `ship` skill/workflow) whose description references and closes issue #279 (e.g. PR body includes `Closes #279`).
