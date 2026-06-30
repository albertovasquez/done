"""filter_output: pick the first registered filter whose matcher recognizes the
command, apply it, and FAIL OPEN — any error / None / empty / longer-than-input
result yields the ORIGINAL output unchanged. A filter must never lose signal.

Filters register via FILTERS (a list of (matcher, filter) pairs). Empty in Task 1
(identity); Task 2+ append real filters."""
from __future__ import annotations

from typing import Callable

# matcher: (command) -> bool ;  filt: (command, output, returncode) -> str | None
FILTERS: list[tuple[Callable[[str], bool], Callable[[str, str, int], str | None]]] = []


def filter_output(command: str, output: str, returncode: int) -> str:
    for matcher, filt in FILTERS:
        try:
            if not matcher(command):
                continue
            result = filt(command, output, returncode)
        except Exception:
            return output                      # fail-open: filter bug never loses output
        if not result or len(result) >= len(output):
            return output                      # declined / no shrink → original
        return result
    return output                              # no matcher → identity


from harness.output_filters import pytest_filter  # noqa: E402

FILTERS.append((pytest_filter.matches, pytest_filter.filter_pytest))
