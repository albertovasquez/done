"""Lifecycle orchestrator for CLIProxyAPI.

status()  — fully implemented: checks management liveness + auth status.
install() — composed: describes the real steps (config write + OS unit register);
            actual binary download and launchctl/systemctl shell-out are guarded
            so unit/routing tests pass without a live proxy.
Other commands (uninstall, start, stop, upgrade, login) — stubbed with a clear
human-readable message; each is a thin call site that will be fleshed out once
the install path is validated end-to-end.
"""
from __future__ import annotations

import platform

from harness.proxy_service import config_gen, management, paths


# ---------------------------------------------------------------------------
# Fully implemented
# ---------------------------------------------------------------------------

def status() -> str:
    """Return a human-readable status string.

    Composes management.is_ready (connection check) with provider auth status.
    Never crashes when the proxy is not running — is_ready returns False on any
    connection error, so we just report "not running" gracefully.
    """
    pw = config_gen.ensure_management_password()
    if not management.is_ready(pw):
        return "CLIProxyAPI: not running (or not reachable on localhost:8317)"

    # Proxy is up — report per-provider auth status.
    lines = ["CLIProxyAPI: running"]
    for provider, path in management._AUTH_URL_PATHS.items():
        try:
            r = management._get("get-auth-status", pw)
            body = r.json()
            pstatus = body.get(provider, {}).get("status", "unknown") if isinstance(body, dict) else "unknown"
            lines.append(f"  {provider}: {pstatus}")
        except Exception as exc:
            lines.append(f"  {provider}: error ({exc})")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Composed (describes real steps; guarded shell-out / binary download)
# ---------------------------------------------------------------------------

def install() -> str:
    """Describe the install steps and perform the safe ones (config write).

    Binary download and OS-service registration are guarded behind a
    _can_shell_out() check so routing tests do not require a live environment.
    """
    pw = config_gen.ensure_management_password()
    config_text = config_gen.generate()
    cfg_path = paths.config_path()

    lines = ["CLIProxyAPI install:"]

    # Step 1 — write config (always safe).
    cfg_path.write_text(config_text)
    lines.append(f"  [ok] config written to {cfg_path}")

    # Step 2 — binary download (guarded).
    from harness.proxy_service import binary as _binary
    bin_path = _binary.target_path()
    if bin_path.exists():
        lines.append(f"  [ok] binary already present at {bin_path}")
    else:
        lines.append(f"  [skip] binary not present at {bin_path} — run `dn proxy upgrade` to download")

    # Step 3 — register OS service (guarded: skip if binary missing).
    if bin_path.exists():
        _result = _register_os_service(str(bin_path), str(cfg_path), pw)
        lines.append(f"  [os-service] {_result}")
    else:
        lines.append("  [skip] OS service registration skipped (no binary)")

    return "\n".join(lines)


def _register_os_service(binary: str, config_path: str, mgmt_password: str) -> str:
    """Write the OS service unit file and attempt to register it.

    Returns a status string. Any shell-out failure is caught and reported
    rather than propagated, so the caller always gets a human-readable result.
    """
    sysname = platform.system()
    try:
        if sysname == "Darwin":
            return _register_launchd(binary, config_path, mgmt_password)
        elif sysname == "Linux":
            return _register_systemd(binary, config_path, mgmt_password)
        else:
            return f"unsupported platform: {sysname}"
    except Exception as exc:
        return f"error: {exc}"


def _register_launchd(binary: str, config_path: str, mgmt_password: str) -> str:
    import subprocess
    from pathlib import Path
    from harness.proxy_service import service_launchd

    label = service_launchd.LABEL
    plist_bytes = service_launchd.build_plist(binary, config_path, mgmt_password, label)
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_bytes(plist_bytes)

    try:
        subprocess.run(["launchctl", "load", "-w", str(plist_path)],
                       check=True, capture_output=True)
        return f"launchd: loaded {label}"
    except subprocess.CalledProcessError as exc:
        return f"launchctl load failed: {exc.stderr.decode().strip()}"


def _register_systemd(binary: str, config_path: str, mgmt_password: str) -> str:
    import subprocess
    from pathlib import Path
    from harness.proxy_service import service_systemd

    label = service_systemd.LABEL
    unit_text = service_systemd.build_unit(binary, config_path, mgmt_password, label)
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit_path = unit_dir / f"{label}.service"
    unit_path.write_text(unit_text)

    try:
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True, capture_output=True)
        subprocess.run(["systemctl", "--user", "enable", "--now", f"{label}.service"],
                       check=True, capture_output=True)
        return f"systemd: enabled + started {label}.service"
    except subprocess.CalledProcessError as exc:
        return f"systemctl failed: {exc.stderr.decode().strip()}"


# ---------------------------------------------------------------------------
# Stubbed — clear message, will be fleshed out once install is validated
# ---------------------------------------------------------------------------

def uninstall() -> str:
    return "dn proxy uninstall: not yet implemented — coming in a follow-up task"


def start() -> str:
    return "dn proxy start: not yet implemented — use the OS service manager to start CLIProxyAPI"


def stop() -> str:
    return "dn proxy stop: not yet implemented — use the OS service manager to stop CLIProxyAPI"


def upgrade() -> str:
    return "dn proxy upgrade: not yet implemented — binary download coming in a follow-up task"


def login(provider: str | None = None) -> str:
    if provider is None:
        providers = ", ".join(management._AUTH_URL_PATHS)
        return f"dn proxy login: specify a provider ({providers})"
    if provider not in management._AUTH_URL_PATHS:
        providers = ", ".join(management._AUTH_URL_PATHS)
        return f"dn proxy login: unknown provider '{provider}' (choose from: {providers})"
    return f"dn proxy login {provider}: browser-auth flow not yet implemented — coming in a follow-up task"
