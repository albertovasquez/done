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
