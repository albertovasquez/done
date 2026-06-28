"""Authoritative test-import resolution for `done`.

`done` mandates worktree development (AGENTS.md #1) but documents a single repo-root
editable install. That editable install writes an ABSOLUTE-path finder pinned to the
checkout it was run in, so `import harness` from a worktree's tests would otherwise
resolve to the *root* checkout's source — silently testing the wrong code.

pytest auto-loads the conftest belonging to the tests being collected, so
`Path(__file__)` is always THIS worktree's path. Prepending that worktree's source
roots to sys.path (absolute, derived from this file's location — never cwd) shadows
the editable finder, making tests resolve to the worktree they live in.

This replaces the per-file `sys.path.insert(0, ".")` / `sys.path.insert(0,
"upstream/src")` lines that previously did this cwd-dependently (and therefore
fragilely) in each test module.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

# Order matters: insert(0, ...) puts the last-inserted path FIRST. We want the
# worktree root ahead of upstream/src is irrelevant (different packages), but both
# must precede the editable finder, which they do as plain sys.path entries.
for _p in (_ROOT / "upstream" / "src", _ROOT):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)
