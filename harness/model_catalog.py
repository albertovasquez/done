"""Static supported-models catalog. Source chain (fail-soft, never raises to the
UI): fresh disk cache -> stale cache -> bundled snapshot -> empty. models.dev is
the source; a bundled snapshot guarantees an offline list. Pure-ish: all I/O
(fetch/clock/cache path) is injectable for hermetic tests."""
from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

_SUPPORTED = {"neuralwatt", "anthropic", "openai", "google"}
_TTL_SECONDS = 24 * 60 * 60
_URL = "https://models.dev/api.json"
_SNAPSHOT = Path(__file__).resolve().parent / "data" / "models_snapshot.json"


@dataclass(frozen=True)
class Model:
    id: str
    name: str


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    env: list[str]
    models: list[Model]


def _default_fetch() -> str:
    req = urllib.request.Request(_URL, headers={"User-Agent": "dn-model-catalog/1"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.read().decode()


def _parse(raw: str) -> list[Provider]:
    data = json.loads(raw)
    out = []
    for pid, pdata in data.items():
        if pid not in _SUPPORTED:
            continue
        models = [Model(id=mid, name=(m or {}).get("name", mid))
                  for mid, m in (pdata.get("models") or {}).items()]
        out.append(Provider(id=pid, name=pdata.get("name", pid),
                            env=list(pdata.get("env") or []), models=models))
    return out


def providers(*, fetch=_default_fetch, cache_path: Path | None = None,
              now=time.time) -> list[Provider]:
    from harness import paths
    cache_path = cache_path or (paths.config_dir() / "models.json")
    # 1) fresh cache
    try:
        if cache_path.exists() and now() - cache_path.stat().st_mtime < _TTL_SECONDS:
            return _parse(cache_path.read_text())
    except Exception:
        pass
    # 2) fetch + write cache
    try:
        raw = fetch()
        tmp = cache_path.with_suffix(cache_path.suffix + ".tmp")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(raw)
        tmp.replace(cache_path)
        return _parse(raw)
    except Exception:
        pass
    # 3) stale cache
    try:
        if cache_path.exists():
            return _parse(cache_path.read_text())
    except Exception:
        pass
    # 4) bundled snapshot
    try:
        return _parse(_SNAPSHOT.read_text())
    except Exception:
        return []
