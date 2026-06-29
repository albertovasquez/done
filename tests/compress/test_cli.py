"""Tests for compress_cli: rebuild_one and status_line with injected fake call_model."""
from pathlib import Path
from harness import compress_cli
from harness.compress import sibling


def test_rebuild_one_writes_fresh_sibling(tmp_path):
    src = tmp_path / "AGENTS.md"
    src.write_text("You should really read https://x.io now")
    out = compress_cli.rebuild_one(
        src, call_model=lambda p: "read https://x.io now", today="2026-06-29"
    )
    assert out == "built"
    sib = sibling.sibling_path(src)
    assert sibling.freshness(src.read_text(), sib.read_text()) == "fresh"


def test_status_line_reports_stale(tmp_path):
    src = tmp_path / "AGENTS.md"
    src.write_text("original")
    compress_cli.rebuild_one(src, call_model=lambda p: "orig", today="2026-06-29")
    src.write_text("changed now")
    line = compress_cli.status_line(src)
    assert "stale" in line.lower()
