"""Detached child that rebuilds specific compressed siblings, then exits.

Spawned by session-end auto-regen as:
    python -m harness.compress.regen_worker <source-path> [<source-path> …]

Best-effort and unobserved: per-file failures are counted, never fatal; the
process always exits 0. Lives in its own module so the detached invocation does
NOT shell `dn compress` (which would relaunch the TUI argparse)."""
from __future__ import annotations

import datetime
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def regen(paths: list[str], *, call_model, today: str) -> dict:
    """Rebuild each path's sibling. Returns counts. Never raises."""
    from harness import compress_cli
    from harness.compress import sibling as _sibling
    built = failed = skipped = 0
    for p in paths:
        try:
            # Re-check that the sibling still exists (TOCTOU guard).  Discovery
            # only selected sources whose sibling existed at session-end; the
            # detached worker runs later.  If the user deleted the sibling in
            # between, skip rather than recreate it — auto-regen must never
            # create a sibling that doesn't exist.
            if not _sibling.sibling_path(Path(p)).is_file():
                skipped += 1
                continue
            result = compress_cli.rebuild_one(Path(p), call_model=call_model, today=today)
            if result == "built":
                built += 1
            elif result == "skipped":
                skipped += 1
            else:                            # "failed" (CompressionError)
                failed += 1
        except Exception:                    # litellm/network/anything — count + continue
            logger.exception("regen_worker: rebuild_one failed for %r", p)
            failed += 1
    return {"built": built, "failed": failed, "skipped": skipped}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        return 0
    from harness import compress_cli, paths
    paths.load_env(os.getcwd())              # so COMPRESS_MODEL / [harness] compress_model resolve
    call_model = compress_cli._build_call_model()
    if call_model is None:
        logger.info("regen_worker: no compression model configured; nothing to do")
        return 0
    today = datetime.date.today().isoformat()
    res = regen(argv, call_model=call_model, today=today)
    logger.info("regen_worker: built=%(built)d failed=%(failed)d skipped=%(skipped)d", res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
