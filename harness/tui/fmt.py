"""Shared token/elapsed formatting for the TUI. Extracted so the footer, the
activity line, and the worker card format identically from one place.

Two token styles are preserved verbatim from the code they replace — they were
NOT identical before extraction, so both survive:
  - fmt_tokens_lower: '4.0k' (lowercase k, no M) — the activity/worker line.
  - fmt_tokens_upper: '4.2K' / '1.0M' — the footer ctx readout.
"""
from __future__ import annotations


def fmt_elapsed(s: float) -> str:
    s = int(s)
    return f"{s//60}m {s%60:02d}s" if s >= 60 else f"{s}s"


def fmt_tokens_lower(n: int) -> str:
    return f"{n/1000:.1f}k" if n >= 1000 else str(n)


def fmt_tokens_upper(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    return f"{n/1000:.1f}K" if n >= 1000 else str(n)


_CTX_CELLS = 8


def ctx_bar(tokens: int, window: int) -> str:
    """Compact context-usage readout for the footer:
    'ctx ██░░░░░░ 8% · 92.0K/1.0M', coloured accent (<50%) → warning (<90%) →
    error (>=90%) as it fills. tokens <= 0 → placeholder (no percentage).
    Returns Textual markup (theme colour tokens)."""
    if window <= 0 or tokens <= 0:
        empty = "░" * _CTX_CELLS
        return f"[$muted]ctx {empty} --/{fmt_tokens_upper(max(window, 0))}[/]"
    frac = min(tokens / window, 1.0)
    pct = int(frac * 100)
    token = "$accent" if pct < 50 else "$warning" if pct < 90 else "$error"
    filled = min(round(frac * _CTX_CELLS), _CTX_CELLS)
    bar = "█" * filled + "░" * (_CTX_CELLS - filled)
    return (f"[$muted]ctx[/] [{token}]{bar}[/] "
            f"[$muted]{pct}% · {fmt_tokens_upper(tokens)}/{fmt_tokens_upper(window)}[/]")
