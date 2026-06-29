from pathlib import Path
from harness import memory


def test_compress_on_write_persists_compressed_when_on(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "_compress_on", lambda *_a, **_k: True, raising=False)
    p = tmp_path / "MEMORY.md"
    memory.compress_on_write(p, "verbose https://x.io fact", call_model=lambda _p: "terse https://x.io")
    assert p.read_text() == "terse https://x.io"


def test_compress_on_write_falls_back_to_verbose_on_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "_compress_on", lambda *_a, **_k: True, raising=False)
    p = tmp_path / "MEMORY.md"
    # model drops the URL -> CompressionError -> fallback to verbose
    memory.compress_on_write(p, "keep https://x.io", call_model=lambda _p: "dropped")
    assert "https://x.io" in p.read_text()


def test_compress_on_write_verbatim_when_off(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "_compress_on", lambda *_a, **_k: False, raising=False)
    p = tmp_path / "MEMORY.md"
    memory.compress_on_write(p, "verbose", call_model=lambda _p: "terse")
    assert p.read_text() == "verbose"
