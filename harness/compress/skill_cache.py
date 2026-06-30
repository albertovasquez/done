"""Done-owned side cache for compressed skill BODIES. Keyed by
sha256(source_body_sha + rules_version) so freshness IS the filename: a source
edit or a compression-rules bump yields a new key and an automatic clean miss.
Never writes into skill source dirs (bundled / ~/.claude / project) — only under
config_dir()/compress-cache/skills/. Pure file I/O + hashing; no LLM."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from harness import paths
from harness.compress import rules


def cache_dir() -> Path:
    return paths.config_dir() / "compress-cache" / "skills"


def cache_key(source_body: str) -> str:
    src_sha = hashlib.sha256(source_body.encode()).hexdigest()
    material = (src_sha + rules.rules_sha256()).encode()
    return hashlib.sha256(material).hexdigest()[:16]


def cache_path(source_body: str) -> Path:
    return cache_dir() / f"{cache_key(source_body)}.md"


def cached_body(source_body: str) -> str | None:
    """The compressed body for this source+rules, or None on miss/error."""
    try:
        return cache_path(source_body).read_text(encoding="utf-8")
    except OSError:
        return None


def store_body(source_body: str, compressed: str) -> Path:
    path = cache_path(source_body)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(compressed, encoding="utf-8")
    os.replace(tmp, path)
    return path
