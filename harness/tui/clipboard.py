"""Native OS clipboard helper. Used by the TUI's copy affordance as the PRIMARY
path (a real success signal), with the app's OSC 52 escape sequence as the
fallback for terminals/SSH sessions where no clipboard binary is reachable.

Pure + injectable (platform/env/runner are parameters) so the tool-selection and
fallthrough logic are unit-testable without spawning real processes."""

from __future__ import annotations

import subprocess
import sys


def _native_copy_argv(platform: str, *, env: dict) -> list[list[str]]:
    """Ordered clipboard-tool argv candidates for a platform. Each inner list is a
    command to pipe the text into on stdin. Empty when the platform has no known
    tool. On Linux the session type (Wayland vs X11) decides the preferred tool."""
    if platform == "darwin":
        return [["pbcopy"]]
    if platform.startswith("win"):
        return [["clip"]]
    if platform == "linux":
        x11 = [["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]]
        if env.get("WAYLAND_DISPLAY"):
            return [["wl-copy"]] + x11          # Wayland session: prefer wl-copy
        return x11                              # X11 (or headless): xclip then xsel
    return []                                   # unknown platform → no native tool


def _run(argv: list[str], text: str) -> bool:
    """Pipe `text` into `argv` on stdin. Returns True on exit 0. Raises
    FileNotFoundError when the binary is absent (caller treats as 'try next')."""
    proc = subprocess.run(argv, input=text.encode("utf-8"),
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc.returncode == 0


def native_copy(text: str, *, platform: str | None = None,
                env: dict | None = None, runner=_run) -> bool:
    """Try each native clipboard tool for the platform in order; return True as
    soon as one succeeds. Returns False when no candidate exists or every one is
    missing/fails — the caller then falls back to OSC 52. A missing binary
    (FileNotFoundError) or any OSError just advances to the next candidate."""
    import os

    platform = sys.platform if platform is None else platform
    env = os.environ if env is None else env
    for argv in _native_copy_argv(platform, env=env):
        try:
            if runner(argv, text):
                return True
        except (FileNotFoundError, OSError):
            continue                            # tool absent / unrunnable → next
    return False
