"""Tests for compress_cli: rebuild_one and status_line with injected fake call_model."""
from pathlib import Path
from harness import compress_cli
from harness.compress import sibling


def test_compress_model_name_prefers_compress_model(monkeypatch):
    monkeypatch.setenv("VIBEPROXY_MODEL", "gpt-5.4")
    monkeypatch.setenv("COMPRESS_MODEL", "claude-haiku-4-5")
    assert compress_cli._compress_model_name() == "claude-haiku-4-5"


def test_compress_model_name_falls_back_to_vibeproxy(monkeypatch):
    monkeypatch.delenv("COMPRESS_MODEL", raising=False)
    monkeypatch.setenv("VIBEPROXY_MODEL", "gpt-5.4")
    assert compress_cli._compress_model_name() == "gpt-5.4"


def test_compress_model_name_none_when_neither_set(monkeypatch):
    monkeypatch.delenv("COMPRESS_MODEL", raising=False)
    monkeypatch.delenv("VIBEPROXY_MODEL", raising=False)
    assert compress_cli._compress_model_name() is None
    assert compress_cli._build_call_model() is None


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


def test_status_line_handles_missing_source(tmp_path):
    src = tmp_path / "AGENTS.md"
    # Create a sibling but not the source file
    sib = sibling.sibling_path(src)
    sib.parent.mkdir(parents=True, exist_ok=True)
    sib.write_text("compressed content")
    # Now call status_line with missing source (but existing sibling)
    line = compress_cli.status_line(src)
    assert "missing" in line.lower()   # returns a message, does not raise
