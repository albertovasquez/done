"""Pin the extracted tui/fmt.py helpers to byte-identical output vs. the two
pre-existing formatters they replace: activity_status token/elapsed style
('4.0k', '1m 24s') and the footer token style ('4.2K', '1.0M')."""
from __future__ import annotations

from harness.tui.fmt import fmt_elapsed, fmt_tokens_lower, fmt_tokens_upper


def test_fmt_elapsed_matches_activity_status_style():
    # was activity_status._fmt_elapsed
    assert fmt_elapsed(0) == "0s"
    assert fmt_elapsed(45) == "45s"
    assert fmt_elapsed(59) == "59s"
    assert fmt_elapsed(60) == "1m 00s"
    assert fmt_elapsed(84) == "1m 24s"
    assert fmt_elapsed(3661) == "61m 01s"


def test_fmt_tokens_lower_matches_activity_status_style():
    # was activity_status._fmt_tokens: '4.0k', no M suffix, lowercase k
    assert fmt_tokens_lower(0) == "0"
    assert fmt_tokens_lower(999) == "999"
    assert fmt_tokens_lower(1000) == "1.0k"
    assert fmt_tokens_lower(4000) == "4.0k"
    assert fmt_tokens_lower(52900) == "52.9k"


def test_fmt_tokens_upper_matches_footer_style():
    # was app._fmt_tokens: '4.2K' uppercase, '1.0M' at >= 1e6
    assert fmt_tokens_upper(0) == "0"
    assert fmt_tokens_upper(999) == "999"
    assert fmt_tokens_upper(1000) == "1.0K"
    assert fmt_tokens_upper(4200) == "4.2K"
    assert fmt_tokens_upper(1_000_000) == "1.0M"
    assert fmt_tokens_upper(1_500_000) == "1.5M"
