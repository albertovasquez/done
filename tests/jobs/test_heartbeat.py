"""Unit tests for harness/jobs/heartbeat.py — write/read/classify liveness."""
import time
import pytest
from harness.jobs import heartbeat as hb


I = 30.0  # interval; STALE_AFTER = 30*3+20 = 110


@pytest.mark.parametrize("hb_age, ok_age, expected", [
    (None, None, "stopped"),       # never ran
    (200.0, 5.0, "stalled"),       # heartbeat too old
    (5.0, None, "failing"),        # alive, never a successful tick
    (5.0, 200.0, "failing"),       # alive, success stale
    (5.0, 5.0, "running"),         # both fresh
    (110.0, 5.0, "running"),       # hb_age == STALE_AFTER boundary (not >)
    (110.01, 5.0, "stalled"),      # just over
])
def test_daemon_status(hb_age, ok_age, expected):
    assert hb.daemon_status(hb_age, ok_age, interval=I) == expected


def test_status_line_wording():
    assert hb.status_line("running", 5.0).startswith("✓")
    assert "jobs will fire" in hb.status_line("running", 5.0)
    assert hb.status_line("failing", 5.0).startswith("⚠")
    assert hb.status_line("stalled", 200.0) == "⚠ daemon stalled — no heartbeat for 200s"
    assert hb.status_line("stopped", None).startswith("✗")
    assert "won't fire" in hb.status_line("stopped", None)


# ── I/O round-trip ────────────────────────────────────────────────────────────
# Paths are computed at call time via cron_dir(), so patching config_dir to
# tmp_path (same pattern as tests/jobs/test_daemon.py's _cron_dir fixture)
# redirects every heartbeat file — no module-attribute rebinding needed.

@pytest.fixture(autouse=True)
def _cron_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("harness.paths.config_dir", lambda: tmp_path)
    return tmp_path


def test_record_and_read_roundtrip(_cron_dir):
    hb.record_heartbeat(success=True)
    assert hb.heartbeat_age() is not None and hb.heartbeat_age() < 5
    assert hb.success_age() is not None and hb.success_age() < 5


def test_fresh_install_creates_cron_dir(_cron_dir):
    assert not (_cron_dir / "cron").exists()
    hb.record_heartbeat()                       # must create the dir
    assert (_cron_dir / "cron" / "ticker_heartbeat").is_file()


def test_no_files_reads_none(_cron_dir):
    assert hb.heartbeat_age() is None           # nothing written → stopped
    assert hb.success_age() is None


def test_partial_file_reads_none(_cron_dir):
    p = _cron_dir / "cron"; p.mkdir()
    (p / "ticker_heartbeat").write_text("not-a-number")
    assert hb.heartbeat_age() is None           # parse failure → None, no raise


def test_record_never_raises_on_unwritable(_cron_dir, monkeypatch):
    def boom(*a, **k):
        raise OSError("nope")
    monkeypatch.setattr(hb, "_atomic_write_epoch", boom)
    hb.record_heartbeat(success=True)           # must not raise


def test_stale_heartbeat(_cron_dir):
    p = _cron_dir / "cron"; p.mkdir()
    (p / "ticker_heartbeat").write_text(f"{time.time() - 500}\n")
    age = hb.heartbeat_age()
    assert age is not None and age > 400
