import logging

import pytest

from harness.logging_setup import setup_file_logging


@pytest.fixture(autouse=True)
def _clean_harness_handlers():
    """Remove any file handlers this test added so cases don't leak into each
    other (the harness root logger is process-global)."""
    root = logging.getLogger("harness")
    before = list(root.handlers)
    yield
    for h in root.handlers[:]:
        if h not in before:
            root.removeHandler(h)
            h.close()


def test_setup_writes_harness_logs_to_file(tmp_path):
    log_path = tmp_path / "runs" / "x" / "harness.log"
    setup_file_logging(log_path)
    logging.getLogger("harness.config").warning("done.conf is unparseable")
    for h in logging.getLogger("harness").handlers:
        h.flush()
    assert log_path.exists()
    text = log_path.read_text()
    assert "done.conf is unparseable" in text
    assert "harness.config" in text


def test_setup_is_idempotent_per_path(tmp_path):
    log_path = tmp_path / "harness.log"
    setup_file_logging(log_path)
    setup_file_logging(log_path)   # second call must NOT stack a handler
    tagged = [h for h in logging.getLogger("harness").handlers
              if getattr(h, "_harness_debug_file", None) == str(log_path)]
    assert len(tagged) == 1, f"handler stacked {len(tagged)} times for one path"


def test_non_harness_logger_not_captured(tmp_path):
    log_path = tmp_path / "harness.log"
    setup_file_logging(log_path)
    logging.getLogger("some.other.lib").warning("unrelated noise")
    for h in logging.getLogger("harness").handlers:
        h.flush()
    assert "unrelated noise" not in (log_path.read_text() if log_path.exists() else "")
