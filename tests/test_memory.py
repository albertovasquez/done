from datetime import date
from pathlib import Path
from harness.memory import MemoryLoad, resolve_memory, MAX_MEMORY_CHARS

TODAY = date(2026, 6, 26)        # yesterday = 2026-06-25


def _write(ws: Path, rel: str, body: str):
    p = ws / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def test_absent_workspace_is_empty(tmp_path):
    load = resolve_memory(tmp_path / "nope", today=TODAY)
    assert load == MemoryLoad()
    assert load.block == "" and load.injected == []


def test_present_but_empty_is_content_gated_noop(tmp_path):
    # workspace EXISTS but has no memory content -> still empty block (no protocol).
    tmp_path.mkdir(exist_ok=True)
    load = resolve_memory(tmp_path, today=TODAY)
    assert load.block == "" and load.injected == []


def test_comment_only_memory_is_skipped(tmp_path):
    _write(tmp_path, "MEMORY.md", "<!-- nothing yet -->")
    load = resolve_memory(tmp_path, today=TODAY)
    assert load.block == "" and load.injected == []


def test_durable_memory_injects_with_protocol(tmp_path):
    _write(tmp_path, "MEMORY.md", "Prefers terse answers.")
    load = resolve_memory(tmp_path, today=TODAY)
    assert load.injected == ["MEMORY.md"]
    assert "Prefers terse answers." in load.block
    assert "# Memory" in load.block
    # the protocol preamble is present (it teaches the write protocol)
    assert "read" in load.block.lower() and "append" in load.block.lower()
    # protocol uses the absolute, quoted workspace path
    assert str(tmp_path) in load.block


def test_daily_files_today_and_yesterday(tmp_path):
    _write(tmp_path, "memory/2026-06-26.md", "TODAY note")
    _write(tmp_path, "memory/2026-06-25.md", "YESTERDAY note")
    _write(tmp_path, "memory/2026-06-24.md", "OLD note")   # must NOT be read
    load = resolve_memory(tmp_path, today=TODAY)
    assert "TODAY note" in load.block
    assert "YESTERDAY note" in load.block
    assert "OLD note" not in load.block


def test_oversized_memory_trimmed(tmp_path):
    # § (not "x"/"y") is absent from ASCII tmp_path components AND the protocol
    # preamble, so count("§") == the body's char count exactly → proves _trim
    # capped the BODY at MAX_MEMORY_CHARS without counting protocol/path chars.
    _write(tmp_path, "MEMORY.md", "§" * (MAX_MEMORY_CHARS + 500))
    load = resolve_memory(tmp_path, today=TODAY)
    assert "…[truncated]…" in load.block
    assert load.block.count("§") == MAX_MEMORY_CHARS


def test_non_utf8_memory_skipped_not_raised(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "MEMORY.md").write_bytes(b"\xff\xfe\x00bad")
    load = resolve_memory(tmp_path, today=TODAY)
    assert load.injected == []
    assert load.skipped and load.skipped[0][0] == "MEMORY.md"


def test_two_workspaces_have_isolated_memory(tmp_path):
    a = tmp_path / "a"; a.mkdir(); (a / "MEMORY.md").write_text("A-fact", encoding="utf-8")
    b = tmp_path / "b"; b.mkdir(); (b / "MEMORY.md").write_text("B-fact", encoding="utf-8")
    la = resolve_memory(a, today=TODAY)
    lb = resolve_memory(b, today=TODAY)
    assert "A-fact" in la.block and "A-fact" not in lb.block
    assert "B-fact" in lb.block and "B-fact" not in la.block
