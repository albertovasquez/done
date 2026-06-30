from harness.compress import loader, sibling


def _write_fresh(source, body):
    sibling.write_sibling(source, body, today="2026-06-29")


def test_loads_original_when_mode_off(tmp_path):
    src = tmp_path / "AGENTS.md"
    src.write_text("ORIGINAL")
    _write_fresh(src, "compressed")
    assert loader.load_context_file(src, mode_on=False) == "ORIGINAL"


def test_loads_compressed_body_when_fresh_and_on(tmp_path):
    src = tmp_path / "AGENTS.md"
    src.write_text("ORIGINAL")
    _write_fresh(src, "compressed body")
    out = loader.load_context_file(src, mode_on=True)
    assert out == "compressed body"
    assert "compress-aware" not in out  # header stripped


def test_loads_original_when_sibling_missing(tmp_path):
    src = tmp_path / "AGENTS.md"
    src.write_text("ORIGINAL")
    assert loader.load_context_file(src, mode_on=True) == "ORIGINAL"


def test_loads_original_when_stale(tmp_path):
    src = tmp_path / "AGENTS.md"
    src.write_text("ORIGINAL")
    _write_fresh(src, "compressed body")
    src.write_text("CHANGED")  # now stale
    assert loader.load_context_file(src, mode_on=True) == "CHANGED"


def test_loads_original_when_sibling_unsafe_symlink(tmp_path):
    src = tmp_path / "AGENTS.md"
    src.write_text("ORIGINAL")
    # write a real fresh sibling
    _write_fresh(src, "compressed body")
    # move the generated sibling aside and replace it with a symlink to simulate an unsafe sibling
    gen = sibling.sibling_path(src)
    real = tmp_path / "real_body.md"
    gen.rename(real)
    gen.symlink_to(real)  # gen is now a symlink -> is_safe_sibling must reject it
    assert loader.load_context_file(src, mode_on=True) == "ORIGINAL"
