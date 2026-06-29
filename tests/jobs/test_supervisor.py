"""Unit tests for harness/jobs/supervisor.py — daemon auto-start (no real fork)."""
import sys
import pytest
from harness.jobs import supervisor, heartbeat


@pytest.fixture(autouse=True)
def _cron_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("harness.paths.config_dir", lambda: tmp_path)
    return tmp_path


def test_already_running_skips_spawn(_cron_dir):
    heartbeat.record_heartbeat(success=True)          # fresh → "running"
    calls = []
    assert supervisor.ensure_daemon_running(spawn=lambda: calls.append(1)) == "already-running"
    assert calls == []


def test_spawns_when_no_heartbeat(_cron_dir):
    calls = []
    assert supervisor.ensure_daemon_running(spawn=lambda: calls.append(1)) == "spawned"
    assert calls == [1]


def test_spawn_failure_is_swallowed(_cron_dir):
    def boom():
        raise OSError("no python")
    assert supervisor.ensure_daemon_running(spawn=boom) == "failed"   # no raise


def test_spawn_detached_argv_and_flags(_cron_dir, monkeypatch):
    seen = {}

    class FakePopen:
        def __init__(self, argv, **kw):
            seen["argv"] = argv
            seen["kw"] = kw

    monkeypatch.setattr(supervisor.subprocess, "Popen", FakePopen)
    supervisor._spawn_detached()
    assert seen["argv"] == [sys.executable, "-m", "harness.jobs.cron_main"]
    assert seen["kw"]["start_new_session"] is True
    assert seen["kw"]["stdout"] is supervisor.subprocess.DEVNULL
