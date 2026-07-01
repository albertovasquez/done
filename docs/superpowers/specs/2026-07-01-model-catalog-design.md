# Robust model catalog for `dn` — design spec

**Date:** 2026-07-01
**Status:** Approved design (2 caveman-review rounds), pending implementation plan
**Author:** Alberto (via Claude)

## Problem

`dn`'s model picker and resolution are fragile (verified against live code):

1. **Silent model drop.** `config_gen.py:46` emits the neuralwatt (glm/qwen) block only `if nw_key:`. If `NEURALWATT_API_KEY` isn't in the env when `dn proxy install/upgrade` runs, those models silently vanish — no error, no marker.
2. **No auto-recovery.** Config regenerates only on manual `dn proxy install/upgrade`. A key added later has no effect until a manual re-run. ("Sometimes I have to re-install.")
3. **Live-only picker.** `app.py:989 _fetch_models` reads the live proxy `/v1/models` on every open, uncached; any hiccup yields an empty picker. No static supported-models list.
4. **No availability validation.** `model_resolve.resolve_model` returns the configured model string verbatim; nothing checks it against what's served. A persona pinned to an unavailable model → the proxy silently substitutes another. ("Not robust.")
5. **No provider grouping / key awareness.** Flat sorted list; `providers.py` lists login mechanisms but no models and omits neuralwatt entirely. No "this model needs key X → log in" concept.

## Goal

Move from "the picker is whatever the proxy happens to serve" to "a **static catalog of supported models grouped by provider**, reconciled against what's actually served and which keys you have, so every model shows an accurate status." Modeled on opencode (models.dev registry + `connected`/`all` provider views).

**User-approved scope:** (1) catalog from models.dev + bundled snapshot, daily refresh, offline-safe; (2) on model-availability mismatch, **warn + offer to fix, never silently swap**; (3) show **all** supported models grouped by provider, mark login-needed ones, clicking triggers login.

## Grounded facts (verified)

- `models.dev/api.json`: 403 without a `User-Agent`, 200 with. Has `neuralwatt` as a provider with `env: ['NEURALWATT_API_KEY']`; 147 providers total; per-provider `env: [KEY]` field.
- **id-space mismatch (the core hazard).** models.dev neuralwatt ids are *upstream* (`glm-5.2`, `qwen3.5-397b-fast`, `glm-5.2-short-fast`); our proxy serves *aliases* (`glm`, `qwen`, `glm-fast`). **Zero direct overlap.** Bridge: `config_gen._NEURALWATT_MODELS` upstream ids match models.dev *exactly* (all 3 verified).
- **Claude id drift.** Proxy: `claude-haiku-4-5-20251001`; models.dev: `claude-haiku-4-5` (no date). Exact match false-flags served models.
- **Proxy reads config once at start**; a regenerated config takes effect only on **restart** (`lifecycle.py:118-124`). The proxy is a shared launchd service.
- **`set_model` passes the model string straight to the proxy call** (`acp_agent.py:126-147`) — so the bound id must be a real proxy id.

## Design

### The three-id discipline (core robustness rule — F6)

Every model is tracked with **three distinct ids**, never conflated:
- **bind id** — the proxy's actual served id (`glm`, `claude-opus-4-8`). This is what gets sent to the proxy. NEVER a catalog/upstream id or a normalized key.
- **display name** — the catalog's pretty name from models.dev, used for the grouped UI.
- **match key** — canonical normalization (below), used ONLY for the availability lookup. Never displayed, never bound.

### Component 0 — `model_ids.py` (canonical normalization; pure, no I/O)

- `canonical(id) -> str`: apply alias↔upstream map, then strip a strict trailing `-YYYYMMDD` (conservative — verified to leave `claude-opus-4-6`, `gpt-5.4` intact). Match-key only.
- `matches(a, b) -> bool`: `canonical(a) == canonical(b)`.
- The alias↔upstream pairs move into a **shared leaf** (`harness/proxy_service/model_map.py`) that both `config_gen` and `model_ids` import (F4) — id-semantics don't belong in the proxy-yaml module.

### Component 1 — `catalog.py` (static registry)

- `providers() -> list[Provider]` where `Provider{id, name, env: list[str], models: list[Model{id, name}]}`, filtered to supported providers (neuralwatt, anthropic, openai, google, …).
- Source chain (fail-soft, never throws to UI): fresh disk cache (`~/.config/harness/models.json`, daily TTL) → stale cache → **bundled snapshot** (`harness/proxy_service/models_snapshot.json`, checked in) → empty.
- Fetch: models.dev/api.json with `User-Agent`; atomic write (temp + rename); daily TTL. Background refresh is out of scope for v1 — refresh lazily on catalog read when cache is older than TTL (simpler than opencode's forked hourly loop; revisit if needed).

### Component 2 — `availability.py` (reconciler; pure)

**`keys_present` is a two-source union (F8).** Provider key/auth state comes from two different places today and the reconciler must combine both:
- **OAuth / browser-login providers** (anthropic, codex, xai, kimi, antigravity) — from the proxy management `get-auth-status` endpoint (`management._AUTH_URL_PATHS`, surfaced by `lifecycle.status()`).
- **`api_key` providers** (neuralwatt, gemini) — from **env-var presence** (`NEURALWATT_API_KEY`, etc.), read the same way `config_gen.py:46` reads it. These never appear in `get-auth-status`.

**Prerequisite:** `providers.py` currently omits neuralwatt and carries no env-var names. Add neuralwatt to the provider registry with `env: ["NEURALWATT_API_KEY"]`, and give every `api_key` provider its env-var name(s), so `keys_present` is derivable rather than hand-waved. `keys_present` is computed by a small adapter that unions the two sources into `{provider_id: bool}`; the reconciler itself stays pure (takes the dict as input).

- `reconcile(catalog, proxy_ids, keys_present) -> list[ModelStatus]` where
  `ModelStatus{provider, bind_id | None, display_name, status}` and status ∈:
  - `available` — a proxy-served id whose `canonical` matches a catalog model; `bind_id` = the proxy id.
  - `login_needed` — catalog model, provider key absent; `bind_id = None` (can't bind until login).
  - `stale_config` — catalog model, key **present**, but not served (config generated before the key existed) → offer regen. `bind_id = None` until regen.
- `resolve_or_warn(configured_model, statuses) -> (model, warning | None)`: if the configured model is `available`, return it; otherwise return the configured string plus a **structured warning** (never a silent swap). The turn is not hard-blocked (avoids the "warn-storm makes agent unusable" failure — the warning is informational, the user's configured model is still attempted).

**Warning transport (F9 — must be specified, not hand-waved).** `resolve_model`/`resolve_session_model` run in **headless contexts** — `tui_main.py:51` (pre-spawn footer) and `persona_sessions.py:72` (`get_or_create`, inside the agent process, no TUI). Neither can render UI. So `resolve_or_warn` only *computes* the warning; **surfacing is a separate, explicit step**: the agent process emits the warning to the TUI over the **existing ACP relay** (the same `session_update` channel `set_model`/the turn loop already use), so it appears as a notice line in the session. The reconcile/validate call that produces the warning happens at **session start** (when the seat is created and the model is bound), emitting one notice — not on every turn (no warn-storm). The picker path additionally shows status inline, so the validation warning and the picker are consistent.

### Component 3 — UI + config_gen

- **Picker** (`app.py`): render `reconcile()` output **grouped by provider**, each row status-tagged. `login_needed` → dimmed + selecting triggers the existing login modal. `stale_config` → offers "regenerate proxy config" (calls the existing `upgrade()`, which safely restarts). `available` → selectable, binds `bind_id`.
- **config_gen** (`config_gen.py`): unchanged emission logic, but the missing-key case is no longer a silent hole — the reconciler derives `login_needed` from catalog + key state, so the model appears as "needs login" instead of vanishing.
- **No auto-regen-on-launch** (rejected in review — would restart the shared proxy and kill other sessions' in-flight turns). Regen stays an explicit, guarded action (install/upgrade/login, which already restart safely).

## Data flow

```
models.dev (cache/snapshot) ──► catalog.providers() ─┐
proxy /v1/models (live) ─────────────────────────────┼─► availability.reconcile() ─► [ModelStatus]
keys present (providers.py auth + NEURALWATT_API_KEY)─┘                                   │
                                                          picker renders grouped ◄────────┤
                                       resolve_or_warn(persona model) ◄────────────────────┘
```

## Error handling

- Catalog fetch failure → snapshot fallback → never an empty picker (fixes gap #3).
- Proxy `/v1/models` unreachable → statuses computed from catalog + keys alone; served-ness unknown → mark catalog models `login_needed`/`stale_config` conservatively and note the proxy is down (don't crash the picker).
- `resolve_or_warn` never raises and never silently swaps — worst case it returns the configured model with a warning.

## Testing

- **Component 0** (`model_ids`): exhaustive unit tests with the real cases the review found — `glm`↔`glm-5.2`, `qwen`↔`qwen3.5-397b-fast`, `claude-haiku-4-5-20251001`↔`claude-haiku-4-5`, and negative cases (`gpt-5.4` unchanged, no over-strip).
- **Component 1** (`catalog`): fixture snapshot; forced-offline path asserts snapshot fallback; TTL staleness logic. No live network in tests (honor the #229 hermetic lesson).
- **Component 2** (`availability`): pure — hand-built (catalog, proxy_ids, keys) triples covering all three statuses + `resolve_or_warn` warn-don't-swap and available-passthrough.
- **Component 3**: reconcile→render boundary (grouping, status tags); the `stale_config`→regen and `login_needed`→login affordances wired to existing lifecycle/login handlers.
- All hermetic: no live models.dev / proxy calls in the suite.

## Risks & rejected alternatives

- **Rejected: auto-regen on every launch.** Restarts the shared launchd proxy → kills other sessions'/cron in-flight turns; masks the real bug (silent drop). Kept regen explicit.
- **Rejected: exact-id matching.** Fails for both neuralwatt aliases and Claude date drift → would mark served models unavailable → warn-storm. Replaced with canonical normalization.
- **Rejected: hard-block on unavailable model.** Too disruptive; `resolve_or_warn` warns but still attempts, so a transient reconcile miss can't brick a turn.
- **Watch:** normalization over-collision (two catalog variants → same canonical). Low-risk on current data; mitigated by keeping normalization match-only (never rewrites display/bind).
- **Known limitation (F10): `available` means "served," not "callable this instant."** A model can be in `/v1/models` yet be **cooling down / rate-limited** (`router.py:65-73`), invisible to the model list. We do not probe callability. This is acceptable — the router already fails over on cooldown (`router.py:100`) — but the status label means "the proxy serves this id," not "a call will succeed right now." The picker/warning copy must not over-promise.

## Out of scope (v1)

Background refresh loop (lazy-on-read TTL instead); per-model cost/limit display; provider inference for non-catalog proxy models (models the proxy serves that aren't in models.dev — shown ungrouped under "Other", still bindable).
