"""dn compress CLI — rebuild siblings (default) and --status (report per-file delta + freshness).

Usage:
  dn compress [path ...]         # rebuild siblings for given paths (or default targets)
  dn compress --status [path ...]  # report freshness / delta for each target
"""
from __future__ import annotations

import argparse
import datetime
import os
from pathlib import Path

from harness.compress import engine, sibling


def rebuild_one(source: Path, *, call_model, today: str) -> str:
    """Compress *source* and write its sibling.

    Returns:
      "built"   — sibling written successfully.
      "skipped" — source does not exist.
      "failed"  — compress_text raised CompressionError (sibling NOT written).
    """
    source = Path(source)
    if not source.exists():
        return "skipped"
    original = source.read_text(errors="ignore")
    try:
        body = engine.compress_text(original, call_model=call_model)
    except engine.CompressionError:
        return "failed"
    sibling.write_sibling(source, body, today=today)
    return "built"


def status_line(source: Path) -> str:
    """Return a one-line freshness + delta report for *source*."""
    source = Path(source)
    sib = sibling.sibling_path(source)
    if not sib.exists():
        return f"{source.name}: no sibling"
    src_text = source.read_text(errors="ignore")
    sib_text = sib.read_text(errors="ignore")
    verdict = sibling.freshness(src_text, sib_text)
    src_n = len(src_text)
    _, body = sibling.split_header(sib_text)
    body_n = len(body)
    pct = 100 - round(100 * body_n / max(src_n, 1))
    return f"{source.name}: {verdict} ({src_n}->{body_n} chars, -{pct}%)"


def _default_targets() -> list[Path]:
    # DEFERRED (issue #186): per-persona SOUL/IDENTITY/USER/MEMORY walk.
    # Phase 1 default targets are cwd AGENTS.md/CLAUDE.md; pass explicit paths for others.
    candidates = [Path("AGENTS.md"), Path("CLAUDE.md")]
    return [p for p in candidates if p.exists()]


def _build_call_model():
    """Return a (prompt: str) -> str callable backed by litellm/vibeproxy.

    Returns None when VIBEPROXY_MODEL is not set — callers should treat that
    as "compression unavailable" and exit cleanly without crashing.
    """
    if not os.environ.get("VIBEPROXY_MODEL"):
        return None

    # Lazy import: vibeproxy intentionally does not import litellm at module level.
    import litellm
    from harness import vibeproxy

    model = vibeproxy.model_id(os.environ["VIBEPROXY_MODEL"])
    kwargs = vibeproxy.completion_kwargs()

    def call_model(prompt: str) -> str:
        resp = litellm.completion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            **kwargs,
        )
        return resp.choices[0].message.content or ""

    return call_model


def run(argv: list[str]) -> int:
    """Entrypoint for `dn compress [--status] [paths...]`."""
    ap = argparse.ArgumentParser(prog="dn compress")
    ap.add_argument("--status", action="store_true",
                    help="report freshness + size delta without rebuilding")
    ap.add_argument("paths", nargs="*", help="source files to process (default: cwd AGENTS.md/CLAUDE.md)")
    ns = ap.parse_args(argv)

    targets = [Path(p) for p in ns.paths] if ns.paths else _default_targets()

    if ns.status:
        for t in targets:
            print(status_line(t))
        return 0

    # Rebuild mode
    call_model = _build_call_model()
    if call_model is None:
        print("compression unavailable: set VIBEPROXY_MODEL")
        return 0

    today = datetime.date.today().isoformat()
    for t in targets:
        print(f"{t.name}: {rebuild_one(t, call_model=call_model, today=today)}")
    return 0
