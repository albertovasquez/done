"""Unit tests for harness/jobs/lock.py — single-instance daemon lock."""
import pytest
from harness.jobs import lock


@pytest.fixture(autouse=True)
def _cron_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("harness.paths.config_dir", lambda: tmp_path)
    return tmp_path


def test_acquire_on_free_path_writes_pid(_cron_dir):
    assert lock.acquire(pid=4242) is True
    assert lock.lock_file().read_text().strip() == "4242"


def test_second_acquire_with_live_owner_fails(_cron_dir):
    assert lock.acquire(pid=4242, pid_alive=lambda p: True) is True
    assert lock.acquire(pid=9999, pid_alive=lambda p: True) is False   # live owner holds it


def test_stale_lock_is_reclaimed(_cron_dir):
    assert lock.acquire(pid=4242, pid_alive=lambda p: False) is True   # writes 4242
    # owner 4242 is "dead" → a new claimant reclaims and overwrites
    assert lock.acquire(pid=5555, pid_alive=lambda p: False) is True
    assert lock.lock_file().read_text().strip() == "5555"


def test_release_removes_file(_cron_dir):
    lock.acquire(pid=4242)
    lock.release()
    assert not lock.lock_file().exists()


def test_release_when_absent_does_not_raise(_cron_dir):
    lock.release()   # no file → no error


def test_garbled_lock_treated_as_reclaimable(_cron_dir):
    _cron_dir.joinpath("cron").mkdir()
    lock.lock_file().write_text("not-a-pid")
    assert lock.acquire(pid=7777, pid_alive=lambda p: True) is True    # unparseable → reclaim
    assert lock.lock_file().read_text().strip() == "7777"
