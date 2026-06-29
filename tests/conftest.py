"""Authoritative test-import resolution for `done`.

`done` mandates worktree development (AGENTS.md #1) but documents a single repo-root
editable install. That editable install writes an ABSOLUTE-path finder pinned to the
checkout it was run in, so `import harness` from a worktree's tests would otherwise
resolve to the *root* checkout's source — silently testing the wrong code.

pytest auto-loads the conftest belonging to the tests being collected, so
`Path(__file__)` is always THIS worktree's path. Prepending that worktree's source
roots to sys.path (absolute, derived from this file's location — never cwd) shadows
the editable finder, making tests resolve to the worktree they live in.

This replaces the per-file `sys.path.insert(0, ".")` / `sys.path.insert(0,
"upstream/src")` lines that previously did this cwd-dependently (and therefore
fragilely) in each test module.
"""
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent

# Order matters: insert(0, ...) puts the last-inserted path FIRST. We want the
# worktree root ahead of upstream/src is irrelevant (different packages), but both
# must precede the editable finder, which they do as plain sys.path entries.
for _p in (_ROOT / "upstream" / "src", _ROOT):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)


@pytest.fixture(autouse=True)
def _neutralize_cron_autostart(request, monkeypatch):
    """Make TUI boot deterministic: never let on_mount's cron autostart push a modal
    or fork a real daemon during a `run_test()` Pilot.

    HarnessTui.on_mount calls _decide_cron_autostart, which probes the OS service
    (launchd/systemd). The tmp XDG_CONFIG_HOME used by TUI tests does NOT redirect
    ~/Library/LaunchAgents, so on a developer's macOS the branch taken depended on
    whether a real LaunchAgent plist happened to exist — and the first-run branch
    pushed CronInstallModal into the Pilot, intermittently derailing unrelated TUI
    tests under full-suite load.

    Neutralize the two SIDE EFFECTS the boot path can trigger: the modal-push method
    becomes a no-op, and the detached daemon spawn is stubbed (it really does
    subprocess.Popen a cron_main child).

    SCOPE: skipped for tests under tests/jobs/, because those are the unit tests that
    EXERCISE these very functions (test_supervisor.py drives the real
    _spawn_detached; the cron-drawer test sets its own narrower stubs). Patching
    globally would mask the function under test. Everything else — the TUI Pilot
    suite — gets the neutralization. Tests that drive the decision logic directly
    (tests/test_first_run_service_prompt.py) pass their own show_prompt and assert
    the returned branch word, which these no-ops do not change.
    """
    if "/tests/jobs/" in request.node.nodeid or request.node.nodeid.startswith("tests/jobs/"):
        return
    try:
        from harness.tui.app import HarnessTui
        monkeypatch.setattr(HarnessTui, "_show_cron_install_prompt", lambda self: None)
    except Exception:
        pass
    try:
        monkeypatch.setattr("harness.jobs.supervisor._spawn_detached", lambda: None)
    except Exception:
        pass
