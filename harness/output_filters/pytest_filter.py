"""pytest output filter: on a passing run, drop the per-file progress lines and
keep the session header + final summary. On ANY failure (returncode != 0 or a
FAILURES/ERRORS section present), return None → the FULL output passes through
unchanged. Never risk hiding a failure."""
from __future__ import annotations

import re

_SUMMARY = re.compile(r"^=+ .*(passed|failed|error|skipped).* in .*=+\s*$", re.M)
_HAS_FAILURE = re.compile(r"^(=+ (FAILURES|ERRORS) =+|=+ short test summary)", re.M)


def matches(command: str) -> bool:
    parts = command.strip().split()
    if not parts:
        return False
    first = parts[0].rsplit("/", 1)[-1]      # basename of argv[0]
    return first == "pytest" or "-m pytest" in command


def filter_pytest(command: str, output: str, returncode: int) -> str | None:
    if returncode != 0 or _HAS_FAILURE.search(output):
        return None                               # failures: decline → dispatcher passes through whole
    m = _SUMMARY.search(output)
    if not m:
        return None                               # unrecognized shape → decline
    header_end = output.find("\n\n")              # keep the session-start header
    header = output[:header_end] if header_end != -1 else ""
    return f"{header}\n\n{m.group(0).strip()}\n"  # header + summary only
