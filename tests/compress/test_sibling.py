from pathlib import Path
from harness.compress import sibling, rules


def test_sibling_path_derivation():
    assert sibling.sibling_path(Path("/x/AGENTS.md")) == Path("/x/AGENTS.compressed.md")
    assert sibling.sibling_path(Path("/x/MEMORY.md")) == Path("/x/MEMORY.compressed.md")


def _fresh_sibling_text(source_text: str) -> str:
    body = "compressed body"
    header = sibling.build_header(
        source_sha=sibling.sha256_text(source_text),
        body_sha=sibling.sha256_text(body),
        date="2026-06-29",
    )
    return header + body


def test_freshness_all_match_is_fresh():
    src = "original source"
    assert sibling.freshness(src, _fresh_sibling_text(src)) == "fresh"


def test_freshness_stale_when_source_changed():
    sib = _fresh_sibling_text("old source")
    assert sibling.freshness("new source", sib) == "stale"


def test_freshness_stale_when_body_hand_edited():
    src = "original source"
    sib = _fresh_sibling_text(src).replace("compressed body", "TAMPERED body")
    assert sibling.freshness(src, sib) == "stale"


def test_freshness_corrupt_when_no_header():
    assert sibling.freshness("src", "just a body, no header") == "corrupt"


def test_write_sibling_is_atomic_and_roundtrips(tmp_path):
    source = tmp_path / "AGENTS.md"
    source.write_text("hello source")
    p = sibling.write_sibling(source, "compressed body", today="2026-06-29")
    assert p == tmp_path / "AGENTS.compressed.md"
    assert sibling.freshness(source.read_text(), p.read_text()) == "fresh"


def test_is_safe_sibling_rejects_symlink(tmp_path):
    source = tmp_path / "AGENTS.md"
    source.write_text("x")
    sib = tmp_path / "AGENTS.compressed.md"
    target = tmp_path / "evil.md"
    target.write_text("evil")
    sib.symlink_to(target)
    assert sibling.is_safe_sibling(source, sib) is False
