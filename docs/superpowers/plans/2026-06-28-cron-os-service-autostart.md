# Cron OS-Service Autostart Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `harness-cron` daemon start automatically and stay running across reboots, closed windows, and crashes by registering it as an OS-managed service (launchd on macOS, systemd-user on Linux) instead of binding its lifecycle to the TUI.

**Architecture:** Borrow the OpenClaw/Hermes model directly: hand daemon lifecycle to the OS service manager, which guarantees autostart-at-boot, restart-on-crash, and single-instance for free. Add a platform-dispatch module (`harness/jobs/service.py`) that writes/loads/unloads a launchd plist or a systemd user unit. Expose it via a `dn cron …` CLI subcommand group and a one-time opt-in prompt on first run. The existing `harness-cron` binary, PID lock, and two-file heartbeat are unchanged in behavior — only **who starts the daemon** changes. The TUI's `ensure_daemon_running()` is demoted from primary mechanism to best-effort fallback for the not-yet-installed case.

**Tech Stack:** Python 3.11+, stdlib only (`subprocess`, `plistlib`, `platform`, `shutil`, `pathlib`), argparse subcommands, Textual (for the first-run prompt), pytest.

## Global Constraints

- **Worktree only** (AGENTS.md #1): all work in `.worktrees/cron-os-service` on branch `cron-os-service` off latest `main`. Never touch the primary checkout. Ship via PR against `main`; do not self-merge.
- **Test command** (AGENTS.md #3): `<repo-root>/.venv/bin/python -m pytest tests/ -q` (target `tests/` only). Run from the worktree root; `conftest.py` resolves imports to the worktree.
- **Python floor:** `>=3.11,<3.15` (already in `pyproject.toml`). `plistlib` and `subprocess` are stdlib — no new dependencies.
- **No new third-party dependencies.** Service management is pure stdlib + shelling out to `launchctl`/`systemctl`/`loginctl`.
- **Opt-in, never silent:** the OS service is registered only after explicit user consent (first-run prompt or `dn cron install`). Matches OpenClaw/Hermes (both gate install behind a prompt).
- **Platforms:** macOS (launchd) and Linux (systemd-user) are first-class. Windows and any other platform return a clear "not supported on <platform>" result — never crash.
- **Command name is `dn`** (not `done` — zsh reserved word). Console scripts: `dn`, `dn-agent`, `harness-cron`.
- **Best-effort, never fatal:** no service operation may crash the TUI or the daemon. Failures are logged and surfaced as status strings, never raised into boot.
- **Idempotent:** install/uninstall are safe to run repeatedly. Installing when already installed reloads; uninstalling when absent is a no-op.

---

## Best Practices Borrowed (traceability)

Every design choice below traces to a confirmed practice in OpenClaw or Hermes (see research memory `cron-daemon-os-service-research`):

| Practice | Source | Where in this plan |
|---|---|---|
| OS service owns lifecycle (not the app/UI) | both | Tasks 2–4 |
| launchd `RunAtLoad` + `KeepAlive` (macOS) | both | Task 3 |
| systemd user unit `Restart=always`, `WantedBy=default.target` | both | Task 4 |
| `loginctl enable-linger` so user service survives logout/reboot | both | Task 4 |
| Install is **prompted**, not silent | both | Task 7 |
| Idempotent install/uninstall | both | Tasks 3, 4 |
| Prove health after install (don't assume) | OpenClaw `restart-health.ts` | Task 6 (`status` reads heartbeat) |
| Keep in-process / lock guard as secondary single-instance defense | Hermes `.tick.lock` + OS guarantee | Task 1 (lock retained, documented as secondary) |
| Two-file heartbeat decoupled from lifecycle (display only) | Hermes `ticker_heartbeat`/`ticker_last_success` | unchanged; Task 6 reads it |
| Platform dispatch via a single registry | OpenClaw `service.ts`, Hermes `service_manager.py` | Task 2 |

---

## File Structure

**New files:**
- `harness/jobs/service.py` — platform-dispatch service manager. Public API: `install()`, `uninstall()`, `service_status()`, `current_backend()`. Internally delegates to launchd/systemd helpers.
- `harness/jobs/service_launchd.py` — macOS launchd backend: plist generation + `launchctl bootstrap`/`bootout`.
- `harness/jobs/service_systemd.py` — Linux systemd-user backend: unit generation + `systemctl --user` + `loginctl enable-linger`.
- `harness/jobs/cli.py` — the `dn cron` subcommand handlers (`install`, `uninstall`, `status`).
- `tests/jobs/test_service.py` — unit tests for dispatch + status (commands stubbed).
- `tests/jobs/test_service_launchd.py` — plist content + command construction.
- `tests/jobs/test_service_systemd.py` — unit content + command construction.
- `tests/jobs/test_cron_cli.py` — `dn cron …` argparse routing.
- `tests/test_first_run_service_prompt.py` — TUI first-run opt-in prompt (Pilot).

**Modified files:**
- `harness/tui_main.py` — add a `cron` subparser that dispatches to `harness/jobs/cli.py` BEFORE the TUI launches; bare `dn` still launches the TUI (back-compat).
- `harness/tui/app.py:336-362` (`on_mount`) — replace the unconditional `ensure_daemon_running()` with: (a) if the OS service is installed, do nothing (the OS owns it); (b) else, on first run, raise the install prompt; (c) else, fall back to `ensure_daemon_running()` (best-effort spawn for users who declined the service).
- `harness/jobs/supervisor.py:1-10` — docstring update: this is now the FALLBACK path, not the primary one.
- `harness/jobs/lock.py:1-8` — docstring update: lock is now a SECONDARY single-instance guard (OS service is primary).
- `README.md` — document `dn cron install` / `uninstall` / `status` and the autostart model.
- `docs/jobs.md` — same.

**Unchanged (intentionally):** `harness/jobs/cron_main.py`, `daemon.py`, `heartbeat.py`, `executor.py`, `store.py`, `ops.py`. The daemon binary and its liveness signals are correct; we only change who launches it.

---

### Task 1: Document the lock + supervisor as secondary/fallback (no behavior change)

Pure docstring edits that lock in the new mental model before any code depends on it. Reviewable on its own; ships the "why" so later tasks aren't surprising.

**Files:**
- Modify: `harness/jobs/lock.py:1-8`
- Modify: `harness/jobs/supervisor.py:1-10`
- Test: none (comment-only change; covered by existing `tests/jobs/test_lock.py` and `tests/jobs/test_supervisor.py` staying green)

**Interfaces:**
- Consumes: nothing.
- Produces: nothing (docstrings only).

- [ ] **Step 1: Update `lock.py` module docstring**

Replace the first docstring paragraph in `harness/jobs/lock.py` (lines 1–8) with:

```python
"""Single-instance lock for the harness-cron daemon — a SECONDARY guard.

Primary single-instance is now the OS service manager (launchd KeepAlive /
systemd Restart=always — see harness/jobs/service.py), which supervises exactly
one daemon. This lock remains as defense-in-depth for the paths the OS does NOT
cover: a user running `harness-cron` by hand, or the TUI fallback spawn
(supervisor.ensure_daemon_running) on a machine where the service is not
installed. The daemon claims cron/daemon.lock atomically (O_CREAT|O_EXCL) at
startup; a crash leaves a stale lock (dead pid) which the next daemon reclaims.
Paths computed at call time via cron_dir() so tests redirect via config_dir.
"""
```

- [ ] **Step 2: Update `supervisor.py` module docstring**

Replace the docstring in `harness/jobs/supervisor.py` (lines 1–10) with:

```python
"""Best-effort FALLBACK daemon spawn — used only when the OS service is absent.

PRIMARY autostart is the OS service manager (harness/jobs/service.py): launchd on
macOS, systemd-user on Linux, registered via `dn cron install`. When that service
is installed, the OS owns the daemon's lifecycle (autostart-at-boot, restart-on-
crash, single-instance) and this module is NOT used.

This fallback exists for users who declined the service (or are on an unsupported
platform): the TUI calls ensure_daemon_running() on boot to spawn a DETACHED
background daemon that outlives the window. It is single-instance via
harness/jobs/lock.py, so it can never produce two daemons. Unlike the OS service,
it does NOT survive a reboot or restart on crash — that is the gap `dn cron
install` closes.
"""
```

- [ ] **Step 3: Run existing tests to confirm no behavior change**

Run: `<repo-root>/.venv/bin/python -m pytest tests/jobs/test_lock.py tests/jobs/test_supervisor.py -q`
Expected: PASS (unchanged behavior; only docstrings edited).

- [ ] **Step 4: Commit**

```bash
git add harness/jobs/lock.py harness/jobs/supervisor.py
git commit -m "docs(jobs): mark lock + supervisor as secondary/fallback to OS service"
```

---

### Task 2: Platform-dispatch skeleton (`service.py`)

The registry that picks a backend per OS and exposes the stable public API the CLI and TUI call. Backends are stubbed here; Tasks 3–4 fill them in. This task locks the interface every later task consumes.

**Files:**
- Create: `harness/jobs/service.py`
- Test: `tests/jobs/test_service.py`

**Interfaces:**
- Consumes: `harness.jobs.paths.cron_dir`, `harness.paths.config_dir`.
- Produces (the stable public API — later tasks and the CLI depend on these exact signatures):
  - `current_backend() -> str` — returns `"launchd"`, `"systemd"`, or `"unsupported"`.
  - `install() -> ServiceResult` — register + start the service.
  - `uninstall() -> ServiceResult` — stop + deregister.
  - `service_status() -> ServiceResult` — is the OS service installed/loaded?
  - `ServiceResult` dataclass: `ok: bool`, `backend: str`, `state: str`, `detail: str`. `state` ∈ `{"installed", "not-installed", "unsupported", "error"}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/jobs/test_service.py
import platform
import pytest
from harness.jobs import service


def test_current_backend_matches_platform(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    assert service.current_backend() == "launchd"
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    assert service.current_backend() == "systemd"
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    assert service.current_backend() == "unsupported"


def test_install_on_unsupported_platform_is_clean(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    res = service.install()
    assert res.ok is False
    assert res.state == "unsupported"
    assert "Windows" in res.detail


def test_service_result_shape():
    res = service.ServiceResult(ok=True, backend="launchd", state="installed", detail="x")
    assert (res.ok, res.backend, res.state, res.detail) == (True, "launchd", "installed", "x")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `<repo-root>/.venv/bin/python -m pytest tests/jobs/test_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'harness.jobs.service'`.

- [ ] **Step 3: Write minimal implementation**

```python
# harness/jobs/service.py
"""OS-service manager for the harness-cron daemon — platform dispatch.

Borrowed from OpenClaw (src/daemon/service.ts) and Hermes
(hermes_cli/service_manager.py): one registry picks a backend per OS and exposes
a stable install/uninstall/status API. The OS service manager then owns the
daemon's lifecycle — autostart-at-boot, restart-on-crash, single-instance — which
is what `dn cron install` buys over the TUI fallback spawn.
"""
from __future__ import annotations

import platform
from dataclasses import dataclass


@dataclass(frozen=True)
class ServiceResult:
    ok: bool
    backend: str
    state: str          # "installed" | "not-installed" | "unsupported" | "error"
    detail: str


def current_backend() -> str:
    # Linux is assumed to use systemd-user. Non-systemd Linux (some containers,
    # Alpine/OpenRC) still resolves to "systemd" here, but install() then fails
    # cleanly: the `systemctl --user enable` shell-out returns non-zero and
    # service_systemd.install() returns a ServiceResult(ok=False, state="error")
    # — no crash, and the TUI falls back to the best-effort spawn.
    sysname = platform.system()
    if sysname == "Darwin":
        return "launchd"
    if sysname == "Linux":
        return "systemd"
    return "unsupported"


def _unsupported() -> ServiceResult:
    return ServiceResult(
        ok=False, backend="unsupported", state="unsupported",
        detail=f"OS-service autostart is not supported on {platform.system()}. "
               f"Jobs still fire while a `dn` window is open.",
    )


def install() -> ServiceResult:
    backend = current_backend()
    if backend == "launchd":
        from harness.jobs import service_launchd as b
        return b.install()
    if backend == "systemd":
        from harness.jobs import service_systemd as b
        return b.install()
    return _unsupported()


def uninstall() -> ServiceResult:
    backend = current_backend()
    if backend == "launchd":
        from harness.jobs import service_launchd as b
        return b.uninstall()
    if backend == "systemd":
        from harness.jobs import service_systemd as b
        return b.uninstall()
    return _unsupported()


def service_status() -> ServiceResult:
    backend = current_backend()
    if backend == "launchd":
        from harness.jobs import service_launchd as b
        return b.service_status()
    if backend == "systemd":
        from harness.jobs import service_systemd as b
        return b.service_status()
    return _unsupported()
```

> Note: the `service_launchd`/`service_systemd` imports are inside the branches, so this task's tests (which only hit `current_backend` and the Windows path) pass before Tasks 3–4 exist.

- [ ] **Step 4: Run test to verify it passes**

Run: `<repo-root>/.venv/bin/python -m pytest tests/jobs/test_service.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/jobs/service.py tests/jobs/test_service.py
git commit -m "feat(jobs): add OS-service platform-dispatch skeleton"
```

---

### Task 3: launchd backend (macOS)

Generates the LaunchAgent plist with `RunAtLoad` + `KeepAlive` (the OpenClaw/Hermes pattern) and loads it via `launchctl bootstrap`. Pure functions for plist content and command construction are unit-tested without touching the real `launchctl`.

**Files:**
- Create: `harness/jobs/service_launchd.py`
- Test: `tests/jobs/test_service_launchd.py`

**Interfaces:**
- Consumes: `service.ServiceResult`, `harness.jobs.paths` (not required), `sys.executable`.
- Produces: `install()`, `uninstall()`, `service_status()` each `-> ServiceResult`; plus pure helpers `plist_path() -> Path`, `build_plist(python: str, label: str) -> bytes`, `LABEL` constant `"com.quiubo.done.cron"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/jobs/test_service_launchd.py
import plistlib
from pathlib import Path
import pytest
from harness.jobs import service_launchd as L


def test_label_is_reverse_dns():
    assert L.LABEL == "com.quiubo.done.cron"


def test_plist_path_under_launchagents(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert L.plist_path() == tmp_path / "Library" / "LaunchAgents" / "com.quiubo.done.cron.plist"


def test_build_plist_has_runatload_keepalive_and_program():
    raw = L.build_plist(python="/usr/bin/python3", label="com.quiubo.done.cron")
    doc = plistlib.loads(raw)
    assert doc["Label"] == "com.quiubo.done.cron"
    assert doc["RunAtLoad"] is True
    assert doc["KeepAlive"] is True
    assert doc["ProgramArguments"] == ["/usr/bin/python3", "-m", "harness.jobs.cron_main"]


def test_build_plist_has_throttle_interval():
    # KeepAlive respawns instantly on exit; ThrottleInterval rate-limits a tight
    # respawn loop (e.g. a daemon that crashes immediately on startup). The standard
    # launchd guard — mirrors systemd's RestartSec=5 on the Linux backend.
    doc = plistlib.loads(L.build_plist(python="/usr/bin/python3", label="com.quiubo.done.cron"))
    assert doc["ThrottleInterval"] == 10


def test_install_writes_plist_and_bootstraps(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    calls = []
    monkeypatch.setattr(L, "_run", lambda argv: calls.append(argv) or (0, ""))
    res = L.install()
    assert res.ok is True and res.state == "installed"
    assert L.plist_path().is_file()                      # plist written
    assert any("bootstrap" in c for c in calls), calls   # launchctl bootstrap invoked


def test_uninstall_is_idempotent_when_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(L, "_run", lambda argv: (0, ""))
    res = L.uninstall()                                  # nothing installed
    assert res.ok is True and res.state == "not-installed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `<repo-root>/.venv/bin/python -m pytest tests/jobs/test_service_launchd.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'harness.jobs.service_launchd'`.

- [ ] **Step 3: Write minimal implementation**

```python
# harness/jobs/service_launchd.py
"""macOS launchd backend for the harness-cron daemon.

Writes a LaunchAgent plist with RunAtLoad + KeepAlive (so the daemon starts at
login and is restarted on crash — the OpenClaw/Hermes pattern) and loads it via
`launchctl bootstrap gui/<uid>`. All shell-outs go through _run so tests stub
them; plist content and paths are pure functions.
"""
from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

from harness.jobs.service import ServiceResult

LABEL = "com.quiubo.done.cron"


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def build_plist(python: str, label: str) -> bytes:
    doc = {
        "Label": label,
        "ProgramArguments": [python, "-m", "harness.jobs.cron_main"],
        "RunAtLoad": True,
        "KeepAlive": True,
        # Rate-limit respawns: KeepAlive restarts the daemon the instant it exits,
        # so a daemon that fails immediately would otherwise hot-loop launchd.
        # 10s mirrors the systemd backend's RestartSec=5 intent. The PID lock
        # (harness/jobs/lock.py) is orthogonal and safe here: on SIGKILL the old
        # process is dead before respawn, so the new daemon's dead-pid reclaim
        # takes the lock rather than exiting — no acquire/exit/respawn loop.
        "ThrottleInterval": 10,
        "ProcessType": "Background",
    }
    return plistlib.dumps(doc)


def _run(argv: list[str]) -> tuple[int, str]:
    p = subprocess.run(argv, capture_output=True, text=True)
    return p.returncode, (p.stderr or p.stdout).strip()


def _domain_target() -> str:
    return f"gui/{os.getuid()}"


def install() -> ServiceResult:
    path = plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(build_plist(sys.executable, LABEL))
    # Idempotent reload: bootout first (ignore failure if not loaded), then bootstrap.
    _run(["launchctl", "bootout", _domain_target(), str(path)])
    rc, err = _run(["launchctl", "bootstrap", _domain_target(), str(path)])
    if rc != 0:
        return ServiceResult(False, "launchd", "error",
                             f"launchctl bootstrap failed: {err}")
    return ServiceResult(True, "launchd", "installed",
                         f"launchd service loaded ({path})")


def uninstall() -> ServiceResult:
    path = plist_path()
    if not path.exists():
        return ServiceResult(True, "launchd", "not-installed", "no launchd service to remove")
    _run(["launchctl", "bootout", _domain_target(), str(path)])
    try:
        path.unlink()
    except OSError:
        pass
    return ServiceResult(True, "launchd", "not-installed", "launchd service removed")


def service_status() -> ServiceResult:
    if not plist_path().exists():
        return ServiceResult(True, "launchd", "not-installed", "launchd service not installed")
    rc, _ = _run(["launchctl", "print", f"{_domain_target()}/{LABEL}"])
    if rc == 0:
        return ServiceResult(True, "launchd", "installed", "launchd service loaded")
    return ServiceResult(True, "launchd", "installed", "plist present but not loaded")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `<repo-root>/.venv/bin/python -m pytest tests/jobs/test_service_launchd.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/jobs/service_launchd.py tests/jobs/test_service_launchd.py
git commit -m "feat(jobs): add launchd backend (RunAtLoad+KeepAlive autostart)"
```

---

### Task 4: systemd-user backend (Linux) with linger

Generates a systemd **user** unit with `Restart=always` + `WantedBy=default.target`, enables it, and calls `loginctl enable-linger` — the detail that makes the user service survive logout and reboot (both reference apps do this). Pure unit content + command construction are unit-tested.

**Files:**
- Create: `harness/jobs/service_systemd.py`
- Test: `tests/jobs/test_service_systemd.py`

**Interfaces:**
- Consumes: `service.ServiceResult`, `sys.executable`, `getpass.getuser`.
- Produces: `install()`, `uninstall()`, `service_status()` each `-> ServiceResult`; pure helpers `unit_path() -> Path`, `build_unit(python: str) -> str`, `UNIT` constant `"harness-cron.service"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/jobs/test_service_systemd.py
from pathlib import Path
import pytest
from harness.jobs import service_systemd as S


def test_unit_name():
    assert S.UNIT == "harness-cron.service"


def test_unit_path_under_user_systemd(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert S.unit_path() == tmp_path / ".config" / "systemd" / "user" / "harness-cron.service"


def test_build_unit_has_restart_always_and_wantedby():
    unit = S.build_unit(python="/usr/bin/python3")
    assert "Restart=always" in unit
    assert "WantedBy=default.target" in unit
    assert "ExecStart=/usr/bin/python3 -m harness.jobs.cron_main" in unit


def test_install_writes_unit_enables_and_lingers(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    calls = []
    monkeypatch.setattr(S, "_run", lambda argv: calls.append(argv) or (0, ""))
    res = S.install()
    assert res.ok is True and res.state == "installed"
    assert S.unit_path().is_file()
    flat = [" ".join(c) for c in calls]
    assert any("daemon-reload" in c for c in flat), flat
    assert any("enable" in c and "harness-cron" in c for c in flat), flat
    assert any("enable-linger" in c for c in flat), flat          # survives reboot


def test_uninstall_idempotent_when_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(S, "_run", lambda argv: (0, ""))
    res = S.uninstall()
    assert res.ok is True and res.state == "not-installed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `<repo-root>/.venv/bin/python -m pytest tests/jobs/test_service_systemd.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'harness.jobs.service_systemd'`.

- [ ] **Step 3: Write minimal implementation**

```python
# harness/jobs/service_systemd.py
"""Linux systemd-user backend for the harness-cron daemon.

Writes a user unit (~/.config/systemd/user/harness-cron.service) with
Restart=always + WantedBy=default.target, enables it, and calls
`loginctl enable-linger` so the user service survives logout and reboot (the
detail OpenClaw/Hermes both apply). Shell-outs go through _run for testability.
"""
from __future__ import annotations

import getpass
import subprocess
import sys
from pathlib import Path

from harness.jobs.service import ServiceResult

UNIT = "harness-cron.service"


def unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / UNIT


def build_unit(python: str) -> str:
    return (
        "[Unit]\n"
        "Description=DoneDone cron daemon (harness-cron)\n"
        "After=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={python} -m harness.jobs.cron_main\n"
        "Restart=always\n"
        "RestartSec=5\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def _run(argv: list[str]) -> tuple[int, str]:
    p = subprocess.run(argv, capture_output=True, text=True)
    return p.returncode, (p.stderr or p.stdout).strip()


def install() -> ServiceResult:
    path = unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_unit(sys.executable), encoding="utf-8")
    _run(["systemctl", "--user", "daemon-reload"])
    rc, err = _run(["systemctl", "--user", "enable", "--now", UNIT])
    if rc != 0:
        return ServiceResult(False, "systemd", "error",
                             f"systemctl --user enable failed: {err}")
    # Best-effort: lingering lets the user service run without an active login
    # session (survives logout/reboot). Failure is non-fatal — log via detail.
    rc_l, err_l = _run(["loginctl", "enable-linger", getpass.getuser()])
    linger = "linger on" if rc_l == 0 else f"linger unavailable ({err_l})"
    return ServiceResult(True, "systemd", "installed",
                         f"systemd user service enabled; {linger}")


def uninstall() -> ServiceResult:
    path = unit_path()
    if not path.exists():
        return ServiceResult(True, "systemd", "not-installed", "no systemd unit to remove")
    _run(["systemctl", "--user", "disable", "--now", UNIT])
    try:
        path.unlink()
    except OSError:
        pass
    _run(["systemctl", "--user", "daemon-reload"])
    return ServiceResult(True, "systemd", "not-installed", "systemd user service removed")


def service_status() -> ServiceResult:
    if not unit_path().exists():
        return ServiceResult(True, "systemd", "not-installed", "systemd unit not installed")
    rc, out = _run(["systemctl", "--user", "is-active", UNIT])
    return ServiceResult(True, "systemd", "installed", f"systemd unit active={out or 'unknown'}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `<repo-root>/.venv/bin/python -m pytest tests/jobs/test_service_systemd.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/jobs/service_systemd.py tests/jobs/test_service_systemd.py
git commit -m "feat(jobs): add systemd-user backend with enable-linger autostart"
```

---

### Task 5: `dn cron` CLI subcommand group

Adds `install` / `uninstall` / `status` handlers that call `service.py` and print human output. Keeps the CLI logic out of `tui_main.py` (which only routes to it).

**Files:**
- Create: `harness/jobs/cli.py`
- Test: `tests/jobs/test_cron_cli.py`

**Interfaces:**
- Consumes: `service.install/uninstall/service_status`, `service.ServiceResult`.
- Produces: `run(argv: list[str]) -> int` — dispatches `["install"|"uninstall"|"status", ...]`; prints a one-line human summary; returns process exit code (0 ok, 1 error). `print_result(res: ServiceResult) -> None` helper.

- [ ] **Step 1: Write the failing test**

```python
# tests/jobs/test_cron_cli.py
import pytest
from harness.jobs import cli
from harness.jobs.service import ServiceResult


def test_install_routes_to_service(monkeypatch, capsys):
    monkeypatch.setattr(cli.service, "install",
                        lambda: ServiceResult(True, "launchd", "installed", "loaded"))
    rc = cli.run(["install"])
    assert rc == 0
    assert "installed" in capsys.readouterr().out.lower()


def test_status_routes_to_service(monkeypatch, capsys):
    monkeypatch.setattr(cli.service, "service_status",
                        lambda: ServiceResult(True, "systemd", "not-installed", "not installed"))
    rc = cli.run(["status"])
    assert rc == 0
    assert "not" in capsys.readouterr().out.lower()


def test_error_result_returns_exit_1(monkeypatch):
    monkeypatch.setattr(cli.service, "install",
                        lambda: ServiceResult(False, "launchd", "error", "boom"))
    assert cli.run(["install"]) == 1


def test_unknown_subcommand_returns_2(capsys):
    assert cli.run(["frobnicate"]) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `<repo-root>/.venv/bin/python -m pytest tests/jobs/test_cron_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'harness.jobs.cli'`.

- [ ] **Step 3: Write minimal implementation**

```python
# harness/jobs/cli.py
"""`dn cron …` subcommands: install / uninstall / status of the OS service.

Thin layer over harness/jobs/service.py — argument routing + human output only.
Invoked from harness/tui_main.py when argv[1] == "cron".
"""
from __future__ import annotations

import argparse

from harness.jobs import service
from harness.jobs.service import ServiceResult


def print_result(res: ServiceResult) -> None:
    mark = "✓" if res.ok else "✗"
    print(f"{mark} [{res.backend}] {res.state}: {res.detail}")


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="dn cron",
                                     description="Manage the DoneDone cron autostart service.")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("install", help="Register + start the OS autostart service.")
    sub.add_parser("uninstall", help="Stop + deregister the OS autostart service.")
    sub.add_parser("status", help="Show whether the OS autostart service is installed.")
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 2

    if args.action == "install":
        res = service.install()
    elif args.action == "uninstall":
        res = service.uninstall()
    else:
        res = service.service_status()

    print_result(res)
    return 0 if res.ok else 1
```

- [ ] **Step 4: Run test to verify it passes**

Run: `<repo-root>/.venv/bin/python -m pytest tests/jobs/test_cron_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/jobs/cli.py tests/jobs/test_cron_cli.py
git commit -m "feat(jobs): add `dn cron install/uninstall/status` CLI"
```

---

### Task 6: Route `dn cron …` from the `dn` entrypoint

Makes `dn cron install` actually reachable. `dn` with no subcommand (or any non-`cron` first arg, e.g. `--cwd`) still launches the TUI — strict back-compat. Intercept happens before argparse so existing TUI flags are untouched.

**Files:**
- Modify: `harness/tui_main.py` — inside `main(argv=None)` (≈ line 84), before the existing `ArgumentParser` is built. Locate by content (`def main(argv=None)`), not by line number.
- Test: `tests/jobs/test_cron_cli.py` (add routing test) — or a focused `tests/test_dn_cron_routing.py`

**Interfaces:**
- Consumes: `harness.jobs.cli.run`.
- Produces: behavior — `main(["cron", "install"])` returns the CLI exit code without launching the TUI; `main([...other...])` proceeds to the TUI unchanged.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dn_cron_routing.py
import pytest
from harness import tui_main


def test_dn_cron_routes_to_cli_without_launching_tui(monkeypatch):
    seen = {}
    monkeypatch.setattr("harness.jobs.cli.run", lambda argv: seen.setdefault("argv", argv) or 0)
    # If routing works, the TUI app is never constructed:
    monkeypatch.setattr(tui_main, "HarnessTui",
                        lambda *a, **k: pytest.fail("TUI must not launch for `dn cron`"))
    rc = tui_main.main(["cron", "install"])
    assert rc == 0
    assert seen["argv"] == ["install"]


def test_bare_dn_still_reaches_tui_arg_parsing(monkeypatch):
    # A non-cron invocation must NOT be intercepted; it proceeds to arg parsing.
    # Stub the app so we don't spawn a real agent; assert we got past routing.
    launched = {}
    monkeypatch.setattr(tui_main, "HarnessTui",
                        lambda *a, **k: launched.setdefault("yes", True) or _FakeApp())
    monkeypatch.setattr("harness.paths.load_env", lambda *a, **k: None)
    class _FakeApp:
        def run(self): pass
    tui_main.main(["--model", "mock", "--cwd", "."])
    assert launched.get("yes") is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `<repo-root>/.venv/bin/python -m pytest tests/test_dn_cron_routing.py -v`
Expected: FAIL (first test: TUI launches / `cli.run` not called).

- [ ] **Step 3: Write minimal implementation**

At the very top of `main(argv=None)` in `harness/tui_main.py`, before the `ArgumentParser` is built, insert:

Change the signature annotation from `-> None` to `-> int | None` (the `cron`
branch now returns an int; the TUI path still falls through and returns `None`).
Insert the intercept as the first lines of the body:

```python
def main(argv=None) -> int | None:
    import sys
    raw = sys.argv[1:] if argv is None else argv
    # `dn cron …` is a service-management subcommand, not a TUI launch. Intercept
    # before argparse so the TUI's flags stay unchanged. Bare `dn` and `dn --cwd …`
    # are NOT intercepted and proceed to the TUI as before.
    if raw and raw[0] == "cron":
        from harness.jobs import cli
        return cli.run(raw[1:])
    # ... existing parser/launch code unchanged (still returns None on the TUI path) ...
```

> Why `return`, not `raise SystemExit`: `dn = harness.tui_main:main`, and
> console_scripts uses the function's return value as the process exit code. So
> `return cli.run(raw[1:])` makes `dn cron install` exit with the CLI's code, while
> the TUI path returning `None` exits 0 — and the unit test can assert on the
> returned int directly. No `SystemExit` needed.

- [ ] **Step 4: Run test to verify it passes**

Run: `<repo-root>/.venv/bin/python -m pytest tests/test_dn_cron_routing.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full jobs + tui_main suite to confirm no regression**

Run: `<repo-root>/.venv/bin/python -m pytest tests/jobs/ tests/test_dn_cron_routing.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add harness/tui_main.py tests/test_dn_cron_routing.py
git commit -m "feat(cli): route `dn cron …` to the service CLI before TUI launch"
```

---

### Task 7: First-run opt-in prompt + demote `ensure_daemon_running` to fallback

The TUI boot path becomes: if the OS service is installed → do nothing (OS owns it); else if this is the first run and the platform is supported → show a one-time opt-in prompt; else → fall back to the best-effort spawn. The prompt writes a "decision recorded" marker so it never nags again.

**Files:**
- Modify: `harness/tui/app.py` — the cron-autostart block inside `on_mount` (≈ lines 352–362; the `ensure_daemon_running()` call sits there). **Locate by content** (`from harness.jobs.supervisor import ensure_daemon_running` / the `# Auto-start the cron daemon` comment), not by line number — the surrounding lines drift between branches.
- Create: `harness/jobs/prompt_state.py` (tiny: has-the-user-been-asked marker)
- Test: `tests/test_first_run_service_prompt.py`, `tests/jobs/test_prompt_state.py`

**Interfaces:**
- Consumes: `service.current_backend`, `service.service_status`, `service.install`, `supervisor.ensure_daemon_running`.
- Produces:
  - `prompt_state.has_been_asked() -> bool`, `prompt_state.mark_asked() -> None` (marker file `cron_dir()/.service_prompt_done`).
  - `app._decide_cron_autostart()` — the boot decision, dispatched from `on_mount`. Pure-ish: returns one of `"os-service-present"`, `"prompted"`, `"fallback-spawn"`, `"skipped"` for testability.

- [ ] **Step 1: Write the failing test for the marker**

```python
# tests/jobs/test_prompt_state.py
import pytest
from harness.jobs import prompt_state


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr("harness.paths.config_dir", lambda: tmp_path)


def test_not_asked_then_asked():
    assert prompt_state.has_been_asked() is False
    prompt_state.mark_asked()
    assert prompt_state.has_been_asked() is True
```

- [ ] **Step 2: Run it; verify failure**

Run: `<repo-root>/.venv/bin/python -m pytest tests/jobs/test_prompt_state.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'harness.jobs.prompt_state'`.

- [ ] **Step 3: Implement the marker**

```python
# harness/jobs/prompt_state.py
"""One-time marker: has the user been asked about cron autostart yet?

Stored as an empty file in cron_dir so the first-run opt-in prompt fires exactly
once, regardless of how many `dn` windows open. Paths resolved at call time so
tests redirect via config_dir.
"""
from __future__ import annotations

from harness.jobs.paths import cron_dir


def _marker():
    return cron_dir() / ".service_prompt_done"


def has_been_asked() -> bool:
    return _marker().exists()


def mark_asked() -> None:
    cron_dir().mkdir(parents=True, exist_ok=True)
    _marker().touch()
```

- [ ] **Step 4: Run it; verify pass**

Run: `<repo-root>/.venv/bin/python -m pytest tests/jobs/test_prompt_state.py -v`
Expected: PASS.

- [ ] **Step 5: Write the failing test for the boot decision**

```python
# tests/test_first_run_service_prompt.py
import pytest
from harness.jobs.service import ServiceResult


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr("harness.paths.config_dir", lambda: tmp_path)


def _decide(monkeypatch, *, backend, installed, asked):
    """Drive HarnessTui._decide_cron_autostart with stubbed environment."""
    from harness.tui.app import HarnessTui
    import harness.jobs.service as svc
    import harness.jobs.prompt_state as ps
    import harness.jobs.supervisor as sup
    monkeypatch.setattr(svc, "current_backend", lambda: backend)
    monkeypatch.setattr(svc, "service_status",
                        lambda: ServiceResult(True, backend,
                                              "installed" if installed else "not-installed", ""))
    monkeypatch.setattr(ps, "has_been_asked", lambda: asked)
    monkeypatch.setattr(sup, "ensure_daemon_running", lambda **k: "spawned")
    # Build a bare instance without running the full app:
    app = HarnessTui.__new__(HarnessTui)
    return app._decide_cron_autostart(show_prompt=lambda: None)


def test_os_service_present_does_nothing(monkeypatch):
    assert _decide(monkeypatch, backend="launchd", installed=True, asked=True) == "os-service-present"


def test_first_run_supported_platform_prompts(monkeypatch):
    assert _decide(monkeypatch, backend="launchd", installed=False, asked=False) == "prompted"


def test_declined_before_falls_back_to_spawn(monkeypatch):
    assert _decide(monkeypatch, backend="launchd", installed=False, asked=True) == "fallback-spawn"


def test_unsupported_platform_falls_back_to_spawn(monkeypatch):
    assert _decide(monkeypatch, backend="unsupported", installed=False, asked=False) == "fallback-spawn"
```

- [ ] **Step 6: Run it; verify failure**

Run: `<repo-root>/.venv/bin/python -m pytest tests/test_first_run_service_prompt.py -v`
Expected: FAIL with `AttributeError: ... '_decide_cron_autostart'`.

- [ ] **Step 7: Implement the boot decision + wire into `on_mount`**

Add this method to `HarnessTui` (near `on_mount` in `harness/tui/app.py`):

```python
def _decide_cron_autostart(self, *, show_prompt) -> str:
    """Decide how to ensure cron runs. Returns the branch taken (testable).

    1. OS service already installed → do nothing; the OS owns the lifecycle.
    2. First run on a supported platform → show the opt-in prompt (once).
    3. Otherwise (declined before, or unsupported platform) → best-effort
       fallback spawn so jobs still fire while this window is open.
    """
    from harness.jobs import service, prompt_state
    from harness.jobs.supervisor import ensure_daemon_running

    if service.current_backend() != "unsupported":
        if service.service_status().state == "installed":
            return "os-service-present"
        if not prompt_state.has_been_asked():
            prompt_state.mark_asked()
            show_prompt()
            return "prompted"
    ensure_daemon_running()
    return "fallback-spawn"
```

Then replace the body of the existing cron block in `on_mount` (the `try:` that
imports and calls `ensure_daemon_running()` — find it by that content, ≈ lines
352–362) with:

```python
        # Ensure scheduled jobs can fire. PRIMARY path is the OS service (launchd/
        # systemd) installed via `dn cron install`; if present, the OS owns the
        # daemon and we do nothing. On first run we offer to install it; otherwise
        # we fall back to a best-effort detached spawn (survives window close but
        # not reboot). Never let any of this break boot.
        try:
            self._decide_cron_autostart(show_prompt=self._show_cron_install_prompt)
        except Exception as e:
            self.log(f"cron autostart skipped: {e!r}")
            if self._tracer is not None:
                self._tracer.emit("dn", "cron.autostart.failed", error=str(e))
```

Add a minimal prompt method (a Textual screen/modal consistent with the existing `NewPersonaModal` pattern — a yes/no that calls `service.install()` on yes and prints the `ServiceResult.detail` into the activity log):

```python
def _show_cron_install_prompt(self) -> None:
    """Offer to install the OS autostart service (once). Yes → service.install();
    the result detail is surfaced in the activity log. Mirrors NewPersonaModal's
    push_screen(..., callback=...) lifecycle."""
    from harness.tui.widgets.cron_install_modal import CronInstallModal
    from harness.jobs import service

    def _on_choice(accepted: bool) -> None:
        if not accepted:
            return
        res = service.install()
        self.log(f"cron autostart: {res.detail}")
    self.push_screen(CronInstallModal(), callback=_on_choice)
```

> The `CronInstallModal` is a small ModalScreen (copy the structure of the existing `NewPersonaModal`): a one-line explanation ("Start DoneDone's scheduler at login so scheduled jobs fire even when no window is open?") and Yes/No buttons that `dismiss(True/False)`. Create it as `harness/tui/widgets/cron_install_modal.py`. Its only logic is dismiss-with-bool; the install side-effect lives in `_on_choice` above, so the modal needs no service import and stays trivially testable.
>
> **No key-binding collision:** this modal is pushed *programmatically* from `_show_cron_install_prompt` (via `push_screen`), not bound to a key. `NewPersonaModal` is the one bound to `n` in `AgentRail`; `CronInstallModal` introduces no `BINDINGS` of its own beyond its Yes/No buttons, so it cannot clash with that or any other rail binding. (The `n` shortcut itself was removed in PR #162 — creation is agent-native now — but the programmatic-push pattern is unaffected either way.)

- [ ] **Step 8: Run the decision tests; verify pass**

Run: `<repo-root>/.venv/bin/python -m pytest tests/test_first_run_service_prompt.py tests/jobs/test_prompt_state.py -v`
Expected: PASS.

- [ ] **Step 9: Update the existing on_mount autostart test**

The existing `tests/jobs/test_cron_drawer_mount.py::test_on_mount_calls_ensure_daemon_running` asserts `ensure_daemon_running` is called unconditionally. Under the new logic it is called only in the fallback branch. Update that test so its environment forces the fallback branch (stub `service.service_status` → not-installed and `prompt_state.has_been_asked` → True), then assert the spawn happens. Keep the `_no_real_daemon_spawn` fixture.

```python
def test_on_mount_falls_back_to_spawn_when_service_absent(monkeypatch):
    """With no OS service installed and the prompt already answered, boot falls
    back to the best-effort detached spawn."""
    import harness.jobs.service as svc
    import harness.jobs.prompt_state as ps
    import harness.jobs.supervisor as sup
    from harness.jobs.service import ServiceResult
    monkeypatch.setattr(svc, "current_backend", lambda: "launchd")
    monkeypatch.setattr(svc, "service_status",
                        lambda: ServiceResult(True, "launchd", "not-installed", ""))
    monkeypatch.setattr(ps, "has_been_asked", lambda: True)
    called = []
    monkeypatch.setattr(sup, "ensure_daemon_running", lambda **k: called.append(True) or "spawned")
    # ... launch app via run_test as before, then:
    assert called == [True]
```

- [ ] **Step 10: Run the full jobs + drawer-mount suite**

Run: `<repo-root>/.venv/bin/python -m pytest tests/jobs/ tests/test_first_run_service_prompt.py tests/test_cron_drawer_mount.py -q`
Expected: PASS.

- [ ] **Step 11: Commit**

```bash
git add harness/jobs/prompt_state.py harness/tui/app.py harness/tui/widgets/cron_install_modal.py \
        tests/jobs/test_prompt_state.py tests/test_first_run_service_prompt.py tests/jobs/test_cron_drawer_mount.py
git commit -m "feat(tui): first-run opt-in for OS autostart; demote spawn to fallback"
```

---

### Task 8: Documentation

Document the autostart model and the `dn cron` commands so users (and the next agent) understand the lifecycle. README staleness is a known trap in this repo — keep it accurate.

**Files:**
- Modify: `README.md` (the jobs/cron section)
- Modify: `docs/jobs.md`
- Test: none (docs).

**Interfaces:** none.

- [ ] **Step 1: Add a "Scheduled jobs run automatically" subsection**

Add to `README.md` (under the existing cron/jobs coverage) and mirror in `docs/jobs.md`:

```markdown
### Keeping scheduled jobs running

DoneDone fires scheduled jobs from a small background daemon (`harness-cron`).
For jobs to fire even when no `dn` window is open — and after a reboot — register
the daemon as an OS service:

    dn cron install      # macOS: launchd LaunchAgent; Linux: systemd user service
    dn cron status       # show whether the service is installed/active
    dn cron uninstall    # remove it

On macOS this writes a LaunchAgent (`~/Library/LaunchAgents/com.quiubo.done.cron.plist`)
with RunAtLoad + KeepAlive. On Linux it writes a systemd **user** unit
(`~/.config/systemd/user/harness-cron.service`, `Restart=always`) and enables
lingering so it survives logout and reboot.

The first time you launch `dn`, it offers to install this for you. If you decline
(or you're on an unsupported platform), jobs still fire while a `dn` window is
open, via a best-effort background spawn — but they won't survive a reboot or
fire with all windows closed. Run `dn cron install` any time to make it
permanent. The Ctrl+J panel's daemon-status header shows whether ticks are firing.
```

- [ ] **Step 2: Commit**

```bash
git add README.md docs/jobs.md
git commit -m "docs: document `dn cron` autostart and the OS-service model"
```

---

### Task 9: Full suite + manual smoke verification

The whole-branch gate. Run everything, then prove the real launchd/systemd path works on this machine (the reference apps both *prove* health after install rather than assuming).

**Files:** none (verification only).

- [ ] **Step 1: Run the full test suite**

Run: `<repo-root>/.venv/bin/python -m pytest tests/ -q`
Expected: PASS, no regressions. Note the test count vs. `main` (additive).

- [ ] **Step 2: Manual smoke — install path (macOS, this machine)**

```bash
.venv/bin/dn cron status        # expect: not-installed
.venv/bin/dn cron install       # expect: ✓ installed, plist loaded
launchctl print gui/$(id -u)/com.quiubo.done.cron | head    # expect: the service listed
ls -l ~/.config/harness/cron/ticker_heartbeat   # expect: file appears within ~35s (one interval)
.venv/bin/dn cron uninstall     # expect: ✓ not-installed
```
Record the observed output in the PR description. If on Linux, do the `systemctl --user status harness-cron` equivalent.

- [ ] **Step 3: Manual smoke — fallback path**

With the service uninstalled and `.service_prompt_done` present (decline already recorded), launch `dn`, confirm via the Ctrl+J panel that the daemon header goes green within ~2 intervals (the fallback spawn still works).

- [ ] **Step 4: Open the PR**

```bash
git push -u origin cron-os-service
gh pr create --base main --title "feat(jobs): OS-service autostart for the cron daemon (launchd/systemd)" \
  --body "Fixes cron-daemon autostart flakiness by handing lifecycle to the OS service manager (launchd on macOS, systemd-user on Linux), mirroring OpenClaw and Hermes. Adds \`dn cron install/uninstall/status\`, a first-run opt-in prompt, and demotes the TUI spawn to a best-effort fallback. Heartbeat + PID lock retained as the liveness display and secondary single-instance guard. Manual smoke output in comments.

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

> Do NOT self-merge — maintainer's call (AGENTS.md #1).

---

## Self-Review

**Spec coverage** (against the confirmed scope: OS service macOS+Linux, opt-in prompt, demote TUI spawn, keep heartbeat/lock):
- OS service macOS → Task 3 ✓
- OS service Linux + linger → Task 4 ✓
- `dn cron install/uninstall/status` → Tasks 5–6 ✓
- First-run opt-in prompt (not silent) → Task 7 ✓
- Demote `ensure_daemon_running` to fallback → Tasks 1, 7 ✓
- Keep heartbeat for Ctrl+J panel → unchanged; read in fallback verify (Task 9) ✓
- Keep PID lock as secondary guard → Task 1 ✓
- Unsupported-platform clean degradation → Tasks 2, 7 ✓
- Docs → Task 8 ✓

**Placeholder scan:** every code step contains full code; no "TBD"/"add error handling"/"similar to Task N". One soft spot flagged inline: Task 6 Step 3 notes the `main()` return-type adjustment explicitly rather than hand-waving it.

**Type consistency:** `ServiceResult(ok, backend, state, detail)` used identically in Tasks 2–7. `state` vocabulary `{installed, not-installed, unsupported, error}` consistent. Backend names `launchd`/`systemd`/`unsupported` consistent across `current_backend`, results, and tests. `LABEL="com.quiubo.done.cron"` and `UNIT="harness-cron.service"` referenced consistently. `_decide_cron_autostart` return words `{os-service-present, prompted, fallback-spawn}` match between impl and tests (note: impl has 3 words; `skipped` mentioned in the interface block was dropped — the impl never returns it, so the interface line is corrected to the 3 actual returns).

**Open risk to flag to the reviewer:** Task 6's interception of `argv[0] == "cron"` assumes no project directory is ever literally named such that a user types `dn cron` meaning "open the TUI on ./cron". This matches how `dn` takes the project via `--cwd` (not a positional), so a bare positional `cron` is unambiguous. Confirmed safe against current `tui_main.py` argparse (no positional args).

## Review Revisions (2026-06-28)

Incorporated four findings from an independent plan review (verified against live code before applying):

1. **launchd respawn-loop guard (the substantive one):** added `ThrottleInterval: 10` to `build_plist` (Task 3) + a test assertion. `KeepAlive` respawns instantly on exit; the throttle rate-limits a tight crash-respawn loop, mirroring the systemd backend's `RestartSec=5`. Verified that the PID-lock interplay is already safe — on `SIGKILL` the old process is dead before respawn, so the new daemon's dead-pid reclaim (`lock.py:56-61`) takes the lock rather than exiting; documented inline.
2. **Task 6 return-type committed:** dropped the `raise SystemExit` alternative; the intercept is `return cli.run(...)` with `main(...) -> int | None`, since `dn = tui_main:main` and console_scripts uses the return as the exit code.
3. **Line-number drift:** the real `on_mount` cron block is ≈ `app.py:352-362` (not 336); Tasks 6 and 7 now say **locate by content**, not by line number. Added a note that `CronInstallModal` is pushed programmatically (no key binding), so it cannot collide with the rail's bindings.
4. **Linux=systemd assumption:** documented in `current_backend()` that non-systemd Linux still resolves to "systemd" but `install()` fails cleanly (non-zero `systemctl` → `ServiceResult(ok=False, state="error")` → TUI falls back to spawn). FYI-level; no code change beyond the comment.
