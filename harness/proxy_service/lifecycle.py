"""Lifecycle orchestrator for CLIProxyAPI.

status()    — fully implemented: checks management liveness + auth status.
install()   — downloads binary, writes config, registers OS service, starts.
upgrade()   — re-downloads binary then stop + start.
uninstall() — stop + deregister OS service + remove data dir.
start()     — start via OS service manager.
stop()      — stop via OS service manager.
login()     — stop service, run foreground `cli-proxy-api -<provider>-login`, restart.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import time

from harness.proxy_service import binary, config_gen, download, management, paths


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
    for provider in management._AUTH_URL_PATHS:
        try:
            r = management._get("get-auth-status", pw)
            body = r.json()
            pstatus = body.get(provider, {}).get("status", "unknown") if isinstance(body, dict) else "unknown"
            lines.append(f"  {provider}: {pstatus}")
        except Exception as exc:
            lines.append(f"  {provider}: error ({exc})")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Install / upgrade / uninstall
# ---------------------------------------------------------------------------

def install() -> str:
    """Download the binary, write config, register OS service, and start.

    Returns a step log. ChecksumMismatch during download is caught and
    reported; no further steps execute if download fails.
    """
    pw = config_gen.ensure_management_password()

    # Step 1 — download binary.
    try:
        bin_path = download.download_and_install(binary.PINNED_VERSION)
    except download.ChecksumMismatch as exc:
        return f"CLIProxyAPI install: binary verification failed — {exc}"
    except Exception as exc:
        return f"CLIProxyAPI install: download error — {exc}"

    # Step 2 — write config.
    try:
        config_text = config_gen.generate()
        cfg_path = paths.config_path()
        cfg_path.write_text(config_text)
    except Exception as exc:
        return f"CLIProxyAPI install: config write failed — {exc}"

    # Step 3 — register OS service.
    try:
        _register_os_service(str(bin_path), str(cfg_path), pw)
    except Exception as exc:
        return f"CLIProxyAPI install: OS service registration failed — {exc}"

    # Step 4 — start.
    try:
        start()
    except Exception as exc:
        return f"CLIProxyAPI install: start failed — {exc}"

    # Step 5 — readiness poll (a few attempts, short sleep).
    _MAX_ATTEMPTS = 5
    _SLEEP_SECS = 0.5
    for _ in range(_MAX_ATTEMPTS):
        if management.is_ready(pw):
            return "CLIProxyAPI install: running"
        time.sleep(_SLEEP_SECS)

    return "CLIProxyAPI install: started (readiness check timed out — may still be starting)"


def upgrade() -> str:
    """Re-download the pinned binary, regenerate config, then stop + start.

    Regenerating config (like install() does) is what lets a newly-set
    NEURALWATT_API_KEY be picked up on restart. Without it, `dn proxy upgrade`
    only refreshed the binary and the documented "set key then upgrade" remedy
    silently did nothing.

    Returns a status string. Errors are caught and returned as strings.
    """
    try:
        download.download_and_install(binary.PINNED_VERSION)
    except download.ChecksumMismatch as exc:
        return f"CLIProxyAPI upgrade: binary verification failed — {exc}"
    except Exception as exc:
        return f"CLIProxyAPI upgrade: download error — {exc}"

    # Regenerate config AFTER download, BEFORE restart, so the restarted service
    # reads the fresh config. A write failure aborts before stop/start — never
    # restart against a half-written config.
    try:
        config_gen.ensure_management_password()
        cfg_path = paths.config_path()
        cfg_path.write_text(config_gen.generate())
    except Exception as exc:
        return f"CLIProxyAPI upgrade: config write failed — {exc}"

    stop_result = stop()
    start_result = start()
    return f"CLIProxyAPI upgrade: complete ({stop_result}; {start_result})"


def uninstall() -> str:
    """Stop the service, deregister it, and remove all proxy data.

    The data directory contains the binary, config, management password,
    and any downloaded auth tokens — all are removed.
    """
    try:
        stop()
    except Exception as exc:
        return f"CLIProxyAPI uninstall: stop error — {exc}"

    try:
        _deregister_os_service()
    except Exception as exc:
        return f"CLIProxyAPI uninstall: deregister error — {exc}"

    data = paths.data_dir()
    try:
        shutil.rmtree(data)
    except Exception as exc:
        return f"CLIProxyAPI uninstall: data dir removal failed — {exc}"

    return (
        f"CLIProxyAPI uninstall: removed proxy data dir {data} "
        "(includes binary, config, management password, and downloaded auth tokens)"
    )


# ---------------------------------------------------------------------------
# OS service helpers
# ---------------------------------------------------------------------------

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


def _deregister_os_service() -> str:
    """Remove the OS service unit file and unregister / disable the service.

    Mirrors _register_os_service: handles Darwin (launchd) and Linux (systemd).
    Returns a status string; failures are caught and reported, never raised.
    """
    sysname = platform.system()
    try:
        if sysname == "Darwin":
            return _deregister_launchd()
        elif sysname == "Linux":
            return _deregister_systemd()
        else:
            return f"unsupported platform: {sysname}"
    except Exception as exc:
        return f"error: {exc}"


def _deregister_launchd() -> str:
    from pathlib import Path
    from harness.proxy_service import service_launchd

    label = service_launchd.LABEL
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"

    try:
        subprocess.run(
            ["launchctl", "bootout", f"gui/{os.getuid()}/{label}"],
            check=True, capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        # Not fatal — unit may already be unloaded.
        pass  # noqa: S110

    if plist_path.exists():
        plist_path.unlink()
        return f"launchd: removed {label}"
    return f"launchd: plist not found at {plist_path} (already removed?)"


def _deregister_systemd() -> str:
    from pathlib import Path
    from harness.proxy_service import service_systemd

    label = service_systemd.LABEL
    unit_path = Path.home() / ".config" / "systemd" / "user" / f"{label}.service"

    try:
        subprocess.run(
            ["systemctl", "--user", "disable", "--now", f"{label}.service"],
            check=True, capture_output=True,
        )
    except subprocess.CalledProcessError:
        pass  # noqa: S110

    if unit_path.exists():
        unit_path.unlink()
        try:
            subprocess.run(
                ["systemctl", "--user", "daemon-reload"],
                check=True, capture_output=True,
            )
        except subprocess.CalledProcessError:
            pass  # noqa: S110
        return f"systemd: disabled + removed {label}.service"
    return f"systemd: unit file not found at {unit_path} (already removed?)"


# ---------------------------------------------------------------------------
# Service control via OS manager (launchctl/systemctl)
# ---------------------------------------------------------------------------

def _run(argv: list[str]) -> tuple[int, str]:
    """Shell-out seam for subprocess calls.

    Tests monkeypatch this to intercept and control OS service commands.
    Returns (returncode, stderr-or-stdout).
    """
    p = subprocess.run(argv, capture_output=True, text=True)
    return p.returncode, (p.stderr or p.stdout).strip()


def _run_interactive(argv: list[str]) -> tuple[int, str]:
    """Shell-out seam for an INTERACTIVE child (the OAuth login): inherits the
    terminal so the user sees "Opening browser…/Authentication successful!" and
    the browser opens. Separate from _run (which captures, for quiet service
    commands). Tests monkeypatch this to avoid launching a real browser.
    """
    p = subprocess.run(argv)                     # no capture: child uses our stdio
    return p.returncode, ""


def start() -> str:
    """Start the CLIProxyAPI service via OS manager."""
    sysname = platform.system()
    if sysname == "Darwin":
        from harness.proxy_service import service_launchd as s
        rc, err = _run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{s.LABEL}"])
    elif sysname == "Linux":
        from harness.proxy_service import service_systemd as s
        rc, err = _run(["systemctl", "--user", "start", f"{s.LABEL}.service"])
    else:
        return f"unsupported platform: {sysname}"
    return "CLIProxyAPI started" if rc == 0 else f"start failed: {err}"


def stop() -> str:
    """Stop the CLIProxyAPI service via OS manager."""
    sysname = platform.system()
    if sysname == "Darwin":
        from harness.proxy_service import service_launchd as s
        rc, err = _run(["launchctl", "bootout", f"gui/{os.getuid()}/{s.LABEL}"])
    elif sysname == "Linux":
        from harness.proxy_service import service_systemd as s
        rc, err = _run(["systemctl", "--user", "stop", f"{s.LABEL}.service"])
    else:
        return f"unsupported platform: {sysname}"
    return "CLIProxyAPI stopped" if rc == 0 else f"stop failed: {err}"


# ---------------------------------------------------------------------------
# Browser-auth login (browser-OAuth providers: anthropic, codex)
# ---------------------------------------------------------------------------

# Provider id → the binary's foreground login flag. "claude" is an alias for
# "anthropic". These run the OAuth flow IN the binary process (it opens the
# browser, owns its own callback listener, and saves the token), which is the
# only reliable mechanism: the management-API auth-url + poll flow fails because
# the running service and the callback collide.
_LOGIN_FLAGS = {"anthropic": "-claude-login", "codex": "-codex-login"}


def login(provider: str | None = None) -> str:
    """Authenticate a provider via CLIProxyAPI's foreground OAuth login.

    Stops the background service, runs `cli-proxy-api -<provider>-login` (which
    opens the browser and owns its own callback), then restarts the service so it
    picks up the new token. The service MUST be stopped during login — a running
    service collides with the foreground callback and the redirect is refused.

    Accepts "claude" as an alias for "anthropic".
    """
    # Friendly alias: "claude" → "anthropic".
    if provider == "claude":
        provider = "anthropic"
    if provider is None or provider not in _LOGIN_FLAGS:
        valid = ", ".join(sorted(_LOGIN_FLAGS))
        return f"dn proxy login: choose a provider from: {valid}"

    bin_path = binary.target_path()
    if not bin_path.exists():
        return ("dn proxy login: CLIProxyAPI binary not found. "
                "Run `dn proxy install` first, then re-run `dn proxy login`.")

    # Stop the service so the foreground login owns its callback. Restart it
    # afterward no matter what, so a failed login never leaves the proxy down.
    config_gen.ensure_management_password()      # ensure config/secret exist
    stop()
    try:
        flag = _LOGIN_FLAGS[provider]
        cfg = str(paths.config_path())
        try:
            rc, err = _run_interactive([str(bin_path), flag, "-config", cfg])
        except Exception as exc:                 # pragma: no cover - defensive
            rc, err = 1, str(exc)
    finally:
        start()                                  # bring the service back up

    if rc == 0:
        return (f"{provider}: authenticated. The proxy has been restarted and now "
                f"serves this provider's models.")
    return f"{provider}: sign-in did not complete ({err}). Re-run `dn proxy login {provider}`."
