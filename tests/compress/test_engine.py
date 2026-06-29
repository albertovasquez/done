import pytest
from harness.compress.engine import compress_text, CompressionError


def test_compress_text_returns_valid_compression():
    original = "You should really make sure to read https://x.io now."

    def fake_model(prompt: str) -> str:
        # mock model returns a terse version that preserves the URL
        return "read https://x.io now"

    out = compress_text(original, call_model=fake_model)
    assert "https://x.io" in out
    assert len(out) < len(original)


def test_compress_text_raises_after_retries_when_model_keeps_dropping_url():
    original = "Keep https://must-stay.example here."

    def bad_model(prompt: str) -> str:
        return "dropped everything"  # never preserves the URL

    with pytest.raises(CompressionError):
        compress_text(original, call_model=bad_model)
