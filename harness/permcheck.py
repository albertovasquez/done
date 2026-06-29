"""Leaf permission/path helpers shared by the dispatch chokepoint and file tools.
No harness imports — keeps the dispatch chain cycle-free (same rule as
textgate.py). Defines the structured PermissionRequest the single decision
function consumes, plus path normalization/confinement against allowed roots."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass
class PermissionRequest:
    kind: str                          # "bash" | "file"
    command: str | None = None
    path: Path | None = None
    is_write: bool = False
    is_exec: bool = False
    outside_roots: bool = False


def _real_roots(roots: Sequence[Path]) -> list[Path]:
    return [Path(os.path.realpath(str(r))) for r in roots]


def _inside(resolved: Path, real_roots: Sequence[Path]) -> bool:
    return any(resolved == r or r in resolved.parents for r in real_roots)


def classify_path(raw: str, roots: Sequence[Path]) -> tuple[Path, bool]:
    """Resolve `raw` (expanduser, anchor relative paths to the first root, collapse
    `..`/symlinks via realpath) and report whether it lands outside every root.
    For a non-existent leaf, realpath resolves the existing parent prefix and
    appends the rest literally — correct for fresh writes.

    With no roots, there is nothing to be inside: a relative path can't be anchored,
    so report it outside (the deny path) rather than IndexError on roots[0]."""
    p = Path(raw).expanduser()
    if not p.is_absolute():
        if not roots:
            return p, True               # no root to anchor to → outside-everything
        p = Path(roots[0]) / p
    resolved = Path(os.path.realpath(str(p)))
    return resolved, not _inside(resolved, _real_roots(roots))


def parent_escapes(resolved: Path, roots: Sequence[Path]) -> bool:
    """True if the parent directory of `resolved` resolves outside every root.
    Called immediately before write/edit touches disk — the TOCTOU re-check.
    Re-realpaths the parent so a parent symlinked out-of-root after approval is
    caught. Same boundary the gate enforced, re-validated at write time."""
    parent = Path(os.path.realpath(str(resolved.parent)))
    return not _inside(parent, _real_roots(roots))


def decide_permission(req: PermissionRequest, *, yolo: bool, has_elicitation: bool) -> str:
    """Pure policy: 'allow' (run, no prompt), 'deny' (block), or 'ask' (prompt the
    client). yolo overrides to allow. In-root file ops (read OR write) are free.
    Everything else is risky (bash, out-of-root, exec): ask if there is a prompt
    channel, otherwise fail CLOSED -> deny (#107).

    Lives in this import-light leaf (not acp_agent) so the headless cron-executor
    and dev-CLI paths can reuse the SAME policy without pulling acp_agent's heavy
    deps or creating an import cycle (#168)."""
    if yolo:
        return "allow"
    if req.kind == "file" and not req.outside_roots:
        return "allow"                       # in-root read & write are free
    return "ask" if has_elicitation else "deny"
