"""ctx_bar: a compact visual context-usage readout for the footer.
'ctx ██░░░░░░ 8% · 92k/1M', coloured accent → warning → error as it fills.
Pure function over (tokens, window); no Textual mount needed."""
from __future__ import annotations

from harness.tui.fmt import ctx_bar


def test_pre_start_shows_placeholder_when_no_tokens():
    # tokens <= 0 → dashes, no percentage (matches old 'ctx --/W' intent).
    out = ctx_bar(0, 200_000)
    assert "ctx" in out
    assert "--" in out
    assert "%" not in out


def test_low_usage_is_accent():
    out = ctx_bar(16_000, 200_000)  # 8%
    assert "8%" in out
    assert "$accent" in out
    assert "$warning" not in out and "$error" not in out


def test_mid_usage_is_warning():
    out = ctx_bar(150_000, 200_000)  # 75%
    assert "75%" in out
    assert "$warning" in out


def test_high_usage_is_error():
    out = ctx_bar(190_000, 200_000)  # 95%
    assert "95%" in out
    assert "$error" in out


def test_bar_has_fixed_width_cells():
    # 8 cells; at 8% roughly one filled. Assert total cell count is stable.
    out = ctx_bar(16_000, 200_000)
    filled = out.count("█")
    empty = out.count("░")
    assert filled + empty == 8


def test_readout_uses_upper_token_format():
    out = ctx_bar(92_000, 1_000_000)
    assert "92.0K" in out
    assert "1.0M" in out
