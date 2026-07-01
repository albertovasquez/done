# Robust Model Catalog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `dn`'s live-only, silently-degrading model picker with a static supported-models catalog (models.dev + bundled snapshot) reconciled against the live proxy and available keys, grouped by provider, with `available`/`login_needed`/`stale_config` status per model — never a silent swap.

**Architecture:** Six focused units built bottom-up: a shared alias↔upstream map (leaf), pure id normalization, the models.dev catalog with snapshot fallback, a pure reconciler, a two-source key adapter, then the UI + resolve-path wiring. Pure logic (normalization, reconcile) is isolated from I/O (catalog fetch, proxy read, key reads) so the brains are trivially testable.

**Tech Stack:** Python 3.11+, pytest, Textual (TUI), stdlib `urllib` (no new deps).

## Global Constraints

- **Worktree only.** Work in `/Users/alberto/Work/Quiubo/harness/.worktrees/model-catalog` (branch `feat/model-catalog`). Never edit the primary checkout. Verify `pwd` before any edit.
- **Test runner (from worktree root):** `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/ -q`
- **Hermetic tests only.** NO live network (models.dev) and NO live proxy (`localhost:8317`) in any test — use fixtures/snapshots. (Honors the #229 lesson: tests coupled to a live proxy flake.)
- **THREE-ID DISCIPLINE (core rule).** Every model carries three distinct ids, never conflated:
  - **bind id** = the proxy's actual served id (`glm`, `claude-opus-4-8`) — the ONLY thing ever sent to the proxy.
  - **display name** = catalog pretty name from models.dev.
  - **match key** = canonical normalization — availability lookup ONLY; never displayed, never bound.
- **`keys_present` is a TWO-SOURCE union:** OAuth/browser providers via proxy `get-auth-status`; `api_key` providers (neuralwatt, gemini) via env-var presence.
- **`available` = "served by proxy," NOT "callable this instant"** (a served model may be cooling down). Copy must not over-promise.
- **Never silently swap** a persona's configured model. `resolve_or_warn` warns (via the ACP relay, at session start), never substitutes.
- Leaf modules stay stdlib-only where noted (no import cycles).

---

### Task 1: Shared alias↔upstream map (`model_map.py`)

**Files:**
- Create: `harness/proxy_service/model_map.py`
- Modify: `harness/proxy_service/config_gen.py:12-23` (import the pairs from the leaf instead of defining them)
- Test: `tests/proxy_service/test_model_map.py`

**Interfaces:**
- Produces: `NEURALWATT_MODELS: list[tuple[str, str]]` (upstream_id, alias); `alias_to_upstream() -> dict[str,str]`; `upstream_to_alias() -> dict[str,str]`.

- [ ] **Step 1: Write the failing test**

Create `tests/proxy_service/test_model_map.py`:
```python
from harness.proxy_service import model_map


def test_pairs_and_maps_are_consistent():
    pairs = model_map.NEURALWATT_MODELS
    assert ("glm-5.2", "glm") in pairs
    assert ("qwen3.5-397b-fast", "qwen") in pairs
    assert ("glm-5.2-short-fast", "glm-fast") in pairs
    assert model_map.alias_to_upstream()["glm"] == "glm-5.2"
    assert model_map.upstream_to_alias()["glm-5.2"] == "glm"


def test_config_gen_reexports_same_pairs():
    # config_gen must consume the leaf, not define its own copy
    from harness.proxy_service import config_gen
    assert config_gen._NEURALWATT_MODELS == model_map.NEURALWATT_MODELS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/proxy_service/test_model_map.py -q`
Expected: FAIL — `ModuleNotFoundError: harness.proxy_service.model_map`.

- [ ] **Step 3: Create the leaf module**

Create `harness/proxy_service/model_map.py`:
```python
"""Shared NeuralWatt alias<->upstream id map. LEAF: stdlib-only, no harness
imports (config_gen and model_ids both consume it; keep it cycle-free). The
alias is what the proxy serves and what gets bound; the upstream id is what
models.dev lists. IDs confirmed against live NeuralWatt /v1/models 2026-06-30."""
from __future__ import annotations

# (upstream model id, proxy alias)
NEURALWATT_MODELS: list[tuple[str, str]] = [
    ("glm-5.2", "glm"),
    ("qwen3.5-397b-fast", "qwen"),
    ("glm-5.2-short-fast", "glm-fast"),
]


def alias_to_upstream() -> dict[str, str]:
    return {alias: up for up, alias in NEURALWATT_MODELS}


def upstream_to_alias() -> dict[str, str]:
    return {up: alias for up, alias in NEURALWATT_MODELS}
```

- [ ] **Step 4: Point config_gen at the leaf**

In `harness/proxy_service/config_gen.py`, replace the `_NEURALWATT_MODELS = [...]` block (lines 12-16) and the `alias_to_upstream` body (lines 19-23) with imports from the leaf:
```python
from harness.proxy_service.model_map import NEURALWATT_MODELS as _NEURALWATT_MODELS, alias_to_upstream  # noqa: F401
```
Keep `_NEURALWATT_MODELS` as the name used later in `generate()` (the re-export preserves it). Remove the now-duplicated local `alias_to_upstream` def (it comes from the import).

- [ ] **Step 5: Run tests to verify they pass**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/proxy_service/test_model_map.py -q`
Expected: PASS (2 tests).

- [ ] **Step 6: Run config_gen's existing tests (no regression)**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/ -q -k "config_gen or proxy"`
Expected: PASS (existing proxy/config_gen tests still green).

- [ ] **Step 7: Commit**

```bash
git add harness/proxy_service/model_map.py harness/proxy_service/config_gen.py tests/proxy_service/test_model_map.py
git commit -m "refactor(proxy): extract alias<->upstream map to shared leaf model_map.py

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Canonical id normalization (`model_ids.py`)

**Files:**
- Create: `harness/model_ids.py`
- Test: `tests/test_model_ids.py`

**Interfaces:**
- Consumes: `model_map.alias_to_upstream()` (Task 1).
- Produces: `canonical(model_id: str) -> str`; `matches(a: str, b: str) -> bool`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_model_ids.py`:
```python
from harness import model_ids


def test_alias_normalizes_to_upstream():
    # neuralwatt alias <-> models.dev upstream id
    assert model_ids.canonical("glm") == model_ids.canonical("glm-5.2")
    assert model_ids.canonical("qwen") == model_ids.canonical("qwen3.5-397b-fast")
    assert model_ids.canonical("glm-fast") == model_ids.canonical("glm-5.2-short-fast")


def test_strips_only_strict_date_suffix():
    assert model_ids.canonical("claude-haiku-4-5-20251001") == model_ids.canonical("claude-haiku-4-5")
    assert model_ids.canonical("claude-opus-4-20250514") == "claude-opus-4"


def test_no_overstrip_on_versioned_ids():
    # these must NOT be altered (no 8-digit date tail)
    for mid in ["claude-opus-4-6", "gpt-5.4", "gpt-5.4-mini", "claude-sonnet-5", "gpt-image-1.5"]:
        assert model_ids.canonical(mid) == mid


def test_matches_uses_canonical():
    assert model_ids.matches("glm", "glm-5.2")
    assert model_ids.matches("claude-haiku-4-5-20251001", "claude-haiku-4-5")
    assert not model_ids.matches("glm", "qwen")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_model_ids.py -q`
Expected: FAIL — `ModuleNotFoundError: harness.model_ids`.

- [ ] **Step 3: Implement normalization**

Create `harness/model_ids.py`:
```python
"""Canonical model-id normalization — a MATCH KEY ONLY. Never display or bind a
canonical id; it exists solely to reconcile catalog ids (models.dev upstream
names) against proxy-served ids (short aliases, dated Claude ids). Two rules:
(1) map a neuralwatt alias to its upstream id; (2) strip a strict trailing
-YYYYMMDD date suffix. Conservative by design — see #229-era caveman review."""
from __future__ import annotations

import re

from harness.proxy_service.model_map import alias_to_upstream

_DATE_SUFFIX = re.compile(r"-\d{8}$")


def canonical(model_id: str) -> str:
    up = alias_to_upstream().get(model_id, model_id)  # alias -> upstream if known
    return _DATE_SUFFIX.sub("", up)                    # strip strict -YYYYMMDD tail


def matches(a: str, b: str) -> bool:
    return canonical(a) == canonical(b)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_model_ids.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add harness/model_ids.py tests/test_model_ids.py
git commit -m "feat(models): add canonical id normalization (alias+date, match-only)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Static catalog with snapshot fallback (`catalog.py`)

**Files:**
- Create: `harness/model_catalog.py`
- Create: `harness/data/models_snapshot.json` (bundled offline fallback)
- Test: `tests/test_model_catalog.py`

**Interfaces:**
- Produces: dataclasses `Model{id: str, name: str}`, `Provider{id: str, name: str, env: list[str], models: list[Model]}`; `providers(*, fetch=..., cache_path=..., now=...) -> list[Provider]`. The `fetch`/`cache_path`/`now` params are injectable so tests never touch the network or real clock.

- [ ] **Step 1: Write the failing test**

Create `tests/test_model_catalog.py`:
```python
import json
from harness import model_catalog


_FAKE_API = {
    "neuralwatt": {"name": "NeuralWatt", "env": ["NEURALWATT_API_KEY"],
                   "models": {"glm-5.2": {"name": "GLM 5.2"}, "qwen3.5-397b-fast": {"name": "Qwen3.5"}}},
    "anthropic": {"name": "Anthropic", "env": ["ANTHROPIC_API_KEY"],
                  "models": {"claude-opus-4-8": {"name": "Claude Opus 4.8"}}},
    "some-unsupported": {"name": "X", "env": [], "models": {"z": {"name": "Z"}}},
}


def test_parses_supported_providers_from_fetch(tmp_path):
    provs = model_catalog.providers(
        fetch=lambda: json.dumps(_FAKE_API),
        cache_path=tmp_path / "models.json",
        now=lambda: 1000.0,
    )
    by_id = {p.id: p for p in provs}
    assert "neuralwatt" in by_id and "anthropic" in by_id
    assert by_id["neuralwatt"].env == ["NEURALWATT_API_KEY"]
    assert {m.id for m in by_id["neuralwatt"].models} == {"glm-5.2", "qwen3.5-397b-fast"}
    # unsupported providers filtered out
    assert "some-unsupported" not in by_id


def test_falls_back_to_bundled_snapshot_when_fetch_fails(tmp_path):
    def boom():
        raise OSError("network down")
    provs = model_catalog.providers(
        fetch=boom, cache_path=tmp_path / "nonexistent.json", now=lambda: 1000.0)
    # snapshot ships with at least neuralwatt + anthropic
    ids = {p.id for p in provs}
    assert "neuralwatt" in ids and "anthropic" in ids


def test_uses_fresh_cache_without_fetching(tmp_path):
    cache = tmp_path / "models.json"
    cache.write_text(json.dumps(_FAKE_API))
    called = {"n": 0}
    def counting_fetch():
        called["n"] += 1
        return json.dumps({})
    # cache mtime is "now"; TTL not exceeded -> no fetch
    provs = model_catalog.providers(fetch=counting_fetch, cache_path=cache, now=lambda: cache.stat().st_mtime + 10)
    assert called["n"] == 0
    assert {p.id for p in provs} >= {"neuralwatt", "anthropic"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_model_catalog.py -q`
Expected: FAIL — `ModuleNotFoundError: harness.model_catalog`.

- [ ] **Step 3: Create the bundled snapshot**

Create `harness/data/models_snapshot.json` — a trimmed real models.dev payload for the supported providers. Minimum content (extend with real ids from `https://models.dev/api.json`):
```json
{
  "neuralwatt": {"name": "NeuralWatt", "env": ["NEURALWATT_API_KEY"],
    "models": {"glm-5.2": {"name": "GLM 5.2"}, "qwen3.5-397b-fast": {"name": "Qwen3.5 397B"}, "glm-5.2-short-fast": {"name": "GLM 5.2 Short Fast"}}},
  "anthropic": {"name": "Anthropic", "env": ["ANTHROPIC_API_KEY"],
    "models": {"claude-opus-4-8": {"name": "Claude Opus 4.8"}, "claude-sonnet-5": {"name": "Claude Sonnet 5"}, "claude-haiku-4-5": {"name": "Claude Haiku 4.5"}}},
  "openai": {"name": "OpenAI", "env": ["OPENAI_API_KEY"],
    "models": {"gpt-5.4": {"name": "GPT-5.4"}, "gpt-5.4-mini": {"name": "GPT-5.4 mini"}}}
}
```

- [ ] **Step 4: Implement the catalog**

Create `harness/model_catalog.py`:
```python
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
    from harness.proxy_service import paths
    cache_path = cache_path or (paths.data_dir() / "models.json")
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_model_catalog.py -q`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add harness/model_catalog.py harness/data/models_snapshot.json tests/test_model_catalog.py
git commit -m "feat(models): static model catalog from models.dev with snapshot fallback

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Two-source key adapter + neuralwatt provider registry

**Files:**
- Modify: `harness/proxy_service/providers.py` (add neuralwatt + env-var names)
- Create: `harness/model_keys.py` (the two-source union adapter)
- Test: `tests/test_model_keys.py`

**Interfaces:**
- Consumes: `providers.PROVIDERS` (with new `env` field); proxy `get-auth-status` (mockable).
- Produces: `keys_present(*, auth_status: dict, environ: dict) -> dict[str, bool]` mapping provider_id -> has-key.

- [ ] **Step 1: Write the failing test**

Create `tests/test_model_keys.py`:
```python
from harness import model_keys


def test_api_key_provider_present_from_env():
    ks = model_keys.keys_present(
        auth_status={}, environ={"NEURALWATT_API_KEY": "x"})
    assert ks["neuralwatt"] is True


def test_api_key_provider_absent_when_env_missing():
    ks = model_keys.keys_present(auth_status={}, environ={})
    assert ks["neuralwatt"] is False


def test_oauth_provider_present_from_auth_status():
    ks = model_keys.keys_present(
        auth_status={"anthropic": {"status": "authenticated"}}, environ={})
    assert ks["anthropic"] is True
    # a provider absent from auth_status is False, not missing
    assert ks.get("codex", False) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_model_keys.py -q`
Expected: FAIL — `ModuleNotFoundError: harness.model_keys`.

- [ ] **Step 3: Add neuralwatt + env names to the provider registry**

In `harness/proxy_service/providers.py`, add an `env: tuple[str, ...] = ()` field to the `Provider` dataclass and register neuralwatt. Result:
```python
@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    mechanism: str            # "browser_poll" | "cli_flag" | "api_key"
    login_flag: str | None = None
    env: tuple[str, ...] = ()  # env-var name(s) for api_key providers


PROVIDERS = [
    Provider("anthropic", "Claude", "browser_poll"),
    Provider("codex", "OpenAI / Codex", "browser_poll"),
    Provider("antigravity", "Antigravity", "browser_poll"),
    Provider("xai", "Grok (xAI)", "cli_flag", "--xai-login"),
    Provider("kimi", "Kimi", "cli_flag", "--kimi-login"),
    Provider("gemini", "Gemini / AI Studio", "api_key", env=("GEMINI_API_KEY",)),
    Provider("neuralwatt", "NeuralWatt (GLM/Qwen)", "api_key", env=("NEURALWATT_API_KEY",)),
]
```

- [ ] **Step 4: Implement the two-source adapter**

Create `harness/model_keys.py`:
```python
"""Two-source key/auth presence: OAuth/browser providers come from the proxy's
get-auth-status; api_key providers (neuralwatt, gemini) come from env-var
presence (the same NEURALWATT_API_KEY config_gen reads). Pure: both sources are
passed in, so tests need neither the proxy nor real env."""
from __future__ import annotations

from harness.proxy_service.providers import PROVIDERS


def keys_present(*, auth_status: dict, environ: dict) -> dict[str, bool]:
    out: dict[str, bool] = {}
    for p in PROVIDERS:
        if p.mechanism == "api_key":
            out[p.id] = any(environ.get(name) for name in p.env)
        else:
            st = auth_status.get(p.id, {})
            out[p.id] = bool(st) and st.get("status") not in (None, "", "unauthenticated")
    return out
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_model_keys.py -q`
Expected: PASS (3 tests).

- [ ] **Step 6: Run existing provider/login tests (no regression)**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/ -q -k "provider or login or proxy"`
Expected: PASS (adding a field with a default and one list entry must not break existing tests).

- [ ] **Step 7: Commit**

```bash
git add harness/proxy_service/providers.py harness/model_keys.py tests/test_model_keys.py
git commit -m "feat(models): two-source key presence + register neuralwatt provider

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: The reconciler (`availability.py`)

**Files:**
- Create: `harness/model_availability.py`
- Test: `tests/test_model_availability.py`

**Interfaces:**
- Consumes: `Provider`/`Model` (Task 3), `model_ids.matches` (Task 2), `keys_present` dict (Task 4).
- Produces: dataclass `ModelStatus{provider: str, display_name: str, bind_id: str | None, status: str}` (status ∈ `"available"|"login_needed"|"stale_config"`); `reconcile(providers, proxy_ids, keys_present) -> list[ModelStatus]`; `resolve_or_warn(configured_model, statuses) -> tuple[str, str | None]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_model_availability.py`:
```python
from harness import model_availability as av
from harness.model_catalog import Provider, Model


_CATALOG = [
    Provider("neuralwatt", "NeuralWatt", ["NEURALWATT_API_KEY"],
             [Model("glm-5.2", "GLM 5.2")]),
    Provider("anthropic", "Anthropic", ["ANTHROPIC_API_KEY"],
             [Model("claude-opus-4-8", "Claude Opus 4.8")]),
]


def test_available_when_proxy_serves_matching_id():
    # proxy serves the ALIAS 'glm'; catalog has upstream 'glm-5.2' -> canonical match
    out = av.reconcile(_CATALOG, proxy_ids=["glm", "claude-opus-4-8"],
                       keys_present={"neuralwatt": True, "anthropic": True})
    by = {(s.provider, s.display_name): s for s in out}
    glm = by[("neuralwatt", "GLM 5.2")]
    assert glm.status == "available"
    assert glm.bind_id == "glm"            # BIND the proxy id, not 'glm-5.2'


def test_login_needed_when_key_absent_and_not_served():
    out = av.reconcile(_CATALOG, proxy_ids=[], keys_present={"neuralwatt": False, "anthropic": True})
    glm = next(s for s in out if s.display_name == "GLM 5.2")
    assert glm.status == "login_needed"
    assert glm.bind_id is None


def test_stale_config_when_key_present_but_not_served():
    out = av.reconcile(_CATALOG, proxy_ids=[], keys_present={"neuralwatt": True, "anthropic": True})
    glm = next(s for s in out if s.display_name == "GLM 5.2")
    assert glm.status == "stale_config"
    assert glm.bind_id is None


def test_resolve_or_warn_passes_available_no_warning():
    out = av.reconcile(_CATALOG, proxy_ids=["glm"], keys_present={"neuralwatt": True, "anthropic": False})
    model, warning = av.resolve_or_warn("glm", out)
    assert model == "glm" and warning is None


def test_resolve_or_warn_warns_never_swaps():
    out = av.reconcile(_CATALOG, proxy_ids=[], keys_present={"neuralwatt": False, "anthropic": False})
    model, warning = av.resolve_or_warn("glm", out)
    assert model == "glm"              # NEVER substituted
    assert warning and "glm" in warning
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_model_availability.py -q`
Expected: FAIL — `ModuleNotFoundError: harness.model_availability`.

- [ ] **Step 3: Implement the reconciler**

Create `harness/model_availability.py`:
```python
"""Reconcile the static catalog against what the proxy serves and which keys are
present. Never silently swaps a configured model. See spec: three-id discipline
(bind=proxy id, display=catalog name, match=canonical)."""
from __future__ import annotations

from dataclasses import dataclass

from harness import model_ids


@dataclass(frozen=True)
class ModelStatus:
    provider: str
    display_name: str
    bind_id: str | None      # proxy id to send; None until available
    status: str              # "available" | "login_needed" | "stale_config"


def reconcile(providers, proxy_ids, keys_present) -> list[ModelStatus]:
    out: list[ModelStatus] = []
    for prov in providers:
        has_key = bool(keys_present.get(prov.id, False))
        for m in prov.models:
            served = next((pid for pid in proxy_ids if model_ids.matches(pid, m.id)), None)
            if served is not None:
                status, bind = "available", served
            elif has_key:
                status, bind = "stale_config", None
            else:
                status, bind = "login_needed", None
            out.append(ModelStatus(prov.id, m.name, bind, status))
    return out


def resolve_or_warn(configured_model, statuses):
    """Return (model, warning|None). Never substitutes: returns the configured
    model verbatim; if it isn't an available bind_id, returns a warning string."""
    for s in statuses:
        if s.bind_id == configured_model and s.status == "available":
            return configured_model, None
    warning = (f"Configured model '{configured_model}' is not available from the "
               f"proxy right now — it may need login or a proxy config refresh.")
    return configured_model, warning
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_model_availability.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add harness/model_availability.py tests/test_model_availability.py
git commit -m "feat(models): reconciler (available/login_needed/stale_config, warn-not-swap)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Wire the picker to the catalog (grouped, status-tagged)

**Files:**
- Modify: `harness/tui/widgets/select_modal.py` (add optional `group` + `disabled` to `SelectOption`)
- Modify: `harness/tui/app.py:962-1012` (`action_select_model` builds the reconciled, grouped list)
- Test: `tests/test_model_picker_render.py`

**Interfaces:**
- Consumes: `model_catalog.providers`, `model_keys.keys_present`, `model_availability.reconcile` (Tasks 3-5), `_fetch_models` (existing, the proxy id list).
- Produces: a grouped option list for the picker; a pure helper `build_picker_rows(statuses) -> list[SelectOption]` that is unit-tested without the TUI.

- [ ] **Step 1: Write the failing test**

Create `tests/test_model_picker_render.py`:
```python
from harness.tui.model_picker import build_picker_rows
from harness.model_availability import ModelStatus


def test_rows_grouped_by_provider_with_status_marks():
    statuses = [
        ModelStatus("anthropic", "Claude Opus 4.8", "claude-opus-4-8", "available"),
        ModelStatus("neuralwatt", "GLM 5.2", None, "login_needed"),
        ModelStatus("neuralwatt", "Qwen3.5", None, "stale_config"),
    ]
    rows = build_picker_rows(statuses)
    # provider header rows are non-selectable (id is None/empty), model rows carry bind_id or a sentinel
    labels = [r.label for r in rows]
    assert any("anthropic" in l.lower() for l in labels)      # a group header
    assert any("GLM 5.2" in l and "login" in l.lower() for l in labels)   # login_needed marked
    # available row is selectable (has a real bind id)
    avail = [r for r in rows if r.id == "claude-opus-4-8"]
    assert len(avail) == 1
    # login_needed / stale_config rows are not directly bindable (disabled)
    disabled = [r for r in rows if getattr(r, "disabled", False)]
    assert any("GLM 5.2" in r.label for r in disabled)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_model_picker_render.py -q`
Expected: FAIL — `ModuleNotFoundError: harness.tui.model_picker`.

- [ ] **Step 3: Extend SelectOption**

In `harness/tui/widgets/select_modal.py`, add optional fields to the `SelectOption` dataclass (keep existing `id`, `label`):
```python
@dataclass
class SelectOption:
    id: str
    label: str
    group: str | None = None      # provider header this row belongs under
    disabled: bool = False        # non-selectable (header, or login_needed/stale_config)
```
In the list-building code that renders options (the `_row`/ListView population around line 80-88), skip focus/selection for `disabled` rows (render them dimmed, never return their id on enter). Follow the non-selectable-header pattern already in `cron_dashboard.py:138-145`.

- [ ] **Step 4: Implement the pure row builder**

Create `harness/tui/model_picker.py`:
```python
"""Pure builder: reconciled ModelStatus list -> grouped, status-tagged
SelectOption rows for the model picker. No TUI, no I/O — unit-tested directly."""
from __future__ import annotations

from harness.tui.widgets.select_modal import SelectOption

_STATUS_TAG = {"available": "", "login_needed": "  — login needed",
               "stale_config": "  — refresh proxy config"}


def build_picker_rows(statuses) -> list[SelectOption]:
    rows: list[SelectOption] = []
    by_provider: dict[str, list] = {}
    for s in statuses:
        by_provider.setdefault(s.provider, []).append(s)
    for provider in sorted(by_provider):
        rows.append(SelectOption(id="", label=f"— {provider} —", group=provider, disabled=True))
        for s in sorted(by_provider[provider], key=lambda x: x.display_name):
            selectable = s.status == "available" and s.bind_id is not None
            rows.append(SelectOption(
                id=s.bind_id or "",
                label=f"{s.display_name}{_STATUS_TAG.get(s.status, '')}",
                group=provider,
                disabled=not selectable,
            ))
    return rows
```

- [ ] **Step 5: Wire it into `action_select_model`**

In `harness/tui/app.py`, change `action_select_model` (around line 969-977) to build the reconciled grouped list. Replace the `options = [...]` construction:
```python
        proxy_ids = await self._fetch_models()            # existing live proxy id list
        from harness import model_catalog, model_keys, model_availability
        from harness.tui.model_picker import build_picker_rows
        from harness.proxy_service import management, config_gen
        import os
        try:
            pw = config_gen.ensure_management_password()
            auth = management._get("get-auth-status", pw).json()
        except Exception:
            auth = {}
        keys = model_keys.keys_present(auth_status=auth, environ=os.environ)
        statuses = model_availability.reconcile(model_catalog.providers(), proxy_ids, keys)
        options = build_picker_rows(statuses)
```
Keep the existing `SelectModal(...)` / `_picked` flow. In `_picked`, ignore empty-id (disabled/header) selections; for a `login_needed`/`stale_config` row, the follow-up (login modal / regen) can be a follow-up PR — for THIS task, disabled rows are simply non-selectable (they render the affordance text and can't be chosen). Note this scope boundary in the commit body.

- [ ] **Step 6: Run tests to verify they pass**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_model_picker_render.py -q`
Expected: PASS.

- [ ] **Step 7: Run full suite (no regression; note known pre-existing failures)**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/ -q`
Expected: PASS except the known pre-existing/flaky ones (per #229 the proxy-coupled set is now hermetic; if any TUI-pilot flake appears, re-run it in isolation to confirm it's not from this diff).

- [ ] **Step 8: Commit**

```bash
git add harness/tui/widgets/select_modal.py harness/tui/model_picker.py harness/tui/app.py tests/test_model_picker_render.py
git commit -m "feat(tui): model picker grouped by provider with availability status

Renders the reconciled catalog: available models bindable, login_needed/
stale_config shown but non-selectable with an affordance label. Login/regen
follow-up actions deferred to a later PR (rows are informational here).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- Component 0 (normalization) → Task 2. ✓ Shared map (F4) → Task 1. ✓
- Component 1 (catalog + snapshot, fail-soft chain) → Task 3. ✓
- Component 2 (reconciler: 3 statuses + resolve_or_warn warn-not-swap) → Task 5. ✓
- Two-source keys (F8) + neuralwatt provider registration → Task 4. ✓
- Component 3 (grouped, status-tagged picker; three-id discipline: bind vs display) → Task 6. ✓
- **Gap acknowledged:** the spec's F9 **warning transport** (emit via ACP relay at session start) and the **login/regen follow-up actions** from `login_needed`/`stale_config` rows are NOT implemented in these 6 tasks — the picker shows the status but the interactive login/regen wiring + the resolve-path warning emission are a **follow-up PR**. This plan delivers the catalog + reconciler + grouped picker (the robustness core); it explicitly defers the two interactive affordances. This is a deliberate scope cut for a shippable first slice — flagged here and in Task 6's commit body. (If the reviewer wants F9 in this PR, add Task 7: wire `resolve_or_warn` into `persona_sessions.get_or_create` and emit the warning over the ACP `session_update` relay.)

**Placeholder scan:** No TBD/TODO; every code step shows complete content. The snapshot JSON (Task 3 Step 3) is a real minimal payload, extendable.

**Type consistency:** `Provider`/`Model` (Task 3) are consumed with the same fields in Tasks 4-5. `ModelStatus{provider, display_name, bind_id, status}` (Task 5) is consumed unchanged in Task 6. `keys_present(auth_status=, environ=)` (Task 4) is called with those kwargs in Task 6. `canonical`/`matches` (Task 2) used in Task 5. `SelectOption` fields (Task 6 Step 3) match the builder (Step 4). Consistent.
