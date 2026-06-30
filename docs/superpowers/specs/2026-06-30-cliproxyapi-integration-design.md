# CLIProxyAPI Integration — Design Spec

**Date:** 2026-06-30
**Status:** Approved (brainstorm), pending implementation plan
**Branch:** `cliproxy-integration`

## Summary

Replace [VibeProxy](https://github.com/automazeio/vibeproxy) with
[CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) as the model proxy
for `done`, promoting it to a **first-class, harness-managed dependency**.
CLIProxyAPI is a Go proxy that exposes an OpenAI/Gemini/Claude/Codex-compatible
API and aggregates multiple providers (Claude, OpenAI/Codex, Gemini, Grok) via
OAuth with round-robin multi-account load balancing, plus arbitrary
OpenAI-compatible API-key upstreams (e.g. NeuralWatt→GLM, OpenRouter).

The harness owns CLIProxyAPI's lifecycle the same way it owns the cron daemon
(PR #163): a pinned binary downloaded on `dn proxy install`, registered as an OS
service, with `dn proxy {install,uninstall,start,stop,status,upgrade}` and a
browser-launching `dn proxy login <provider>` TUI modal.

## Goals

- Make CLIProxyAPI a managed dependency: install, run-as-service, status, login —
  no "did you start the proxy manually?" friction.
- Support **all** OAuth providers (Claude, OpenAI/Codex, Gemini, Grok) and
  **API-key upstreams** (NeuralWatt→GLM, OpenRouter) through one proxy.
- Keep the existing per-persona model flow working unchanged.
- Preserve a thin Python seam so the proxy implementation can be swapped later.
- Non-breaking migration from the current VibeProxy setup.

## Non-Goals

- **Per-step / per-mission model selection** (different model per mission step by
  workload). This is the forward path once Missions land; see
  [[get-missions-done-epic]]. Out of scope here.
- A modal for API-key upstreams. Those are set-once config + docs (decision below).
- Embedding CLIProxyAPI's Go SDK in-process. The SDK is Go-only; the harness is
  Python. Binary + OS service is the right boundary.

## Decisions (locked with user)

| Decision | Choice | Why |
|---|---|---|
| Integration shape | **Managed dependency**, thin `proxy.py` seam | Same pattern as cron; swappable later |
| Install method | **Download pinned binary** on `dn proxy install` (GitHub release, checksum-verified) | No Go toolchain ask; reproducible |
| Lifecycle | **OS service** (launchd/systemd) | Survives reboots; mirrors `dn cron` |
| Providers | All — Claude, OpenAI/Codex, Gemini, Grok | CLIProxyAPI round-robin is the win over VibeProxy |
| Worker model | **Per-persona** from `done.conf` (current flow) | Unchanged; per-step deferred to Missions |
| Login UX | **TUI modal** = browser-launcher + status-poller | Auth is browser-based (verified vs Hermes), not a terminal form |
| API-key upstreams | **Config-file + docs only**, no modal | Set-once; only OAuth needs the browser dance |
| Env migration | **Single-file rename** with dual-name fallback | Smallest blast radius (16 files → ~1) |

## Architecture

```
┌─────────────────────────────────────────────┐
│  done / harness (Python, Textual TUI)        │
│  router model ──┐                            │
│  worker model ──┼──► harness/proxy.py ──────┐│
│  (per-persona)  │   (thin seam, os-only)    ││
└─────────────────┼───────────────────────────┼┘
                  │                            ▼
                  │                   localhost:8317
                  │                   /v1/chat/completions
        ┌─────────▼────────────────────────────▼──────┐
        │  CLIProxyAPI (Go binary, OS service)          │
        │  OAuth: Claude · OpenAI/Codex · Gemini · Grok │
        │  API-key upstreams: NeuralWatt→GLM, OpenRouter│
        │  mgmt API: /v0/management (localhost)         │
        └───────────────────────────────────────────────┘
```

### Component: `harness/proxy.py` (the seam)

Renamed from `harness/vibeproxy.py`. Same thin contract, still imports only `os`
(no litellm — callers own the litellm call; litellm costs ~1s at import and sits
on the startup path).

Exports (unchanged signatures): `base_url()`, `api_key()`, `default_model()`,
`model_id(name)`, `completion_kwargs()`, `model_kwargs()`.

**Env reads use dual-name fallback** so existing setups keep working:

```python
def base_url() -> str:
    return os.getenv("PROXY_BASE_URL") or os.getenv("VIBEPROXY_BASE_URL") or _DEFAULT_BASE_URL

def api_key() -> str:
    return os.getenv("PROXY_API_KEY") or os.getenv("VIBEPROXY_API_KEY") or _DEFAULT_API_KEY

def default_model() -> str:
    return os.getenv("PROXY_MODEL") or os.getenv("VIBEPROXY_MODEL") or DEFAULT_MODEL
```

All other call sites change **only their import** (`from harness import vibeproxy`
→ `from harness import proxy`). A module alias (`vibeproxy = proxy`) may be kept
for one release to avoid touching importers at all.

### Component: `harness/proxy_service.py` (lifecycle)

New module, modeled on the cron supervisor (PR #155/#163). Responsibilities:
binary download + checksum verify, `config.yaml` generation, OS-service
register/deregister (launchd plist / systemd unit), start/stop, and status via
the management API.

### CLIProxyAPI config (`config.yaml`)

Generated by `dn proxy install`. Key fields (verified against
`config.example.yaml`):

- `port: 8317`, `host: "127.0.0.1"` (localhost-only)
- `remote-management.secret-key: <auto-generated>` — REQUIRED to mount the
  management API at `/v0/management`; `allow-remote: false` (localhost only).
  Stored alongside config so the harness can poll auth status.
- `openai-compatibility:` — API-key upstreams (NeuralWatt, OpenRouter). See below.

## Lifecycle commands (`dn proxy`)

| Command | Action |
|---|---|
| `install` | Download pinned binary → checksum → write `config.yaml` (with generated mgmt secret) → register OS service → first-run opt-in modal |
| `uninstall` | Stop + deregister service; leave config/auth dirs intact |
| `start` / `stop` | Manual service control |
| `status` | Service liveness + authed providers (via `/v0/management`) + live model aliases |
| `upgrade` | Bump pinned version, re-download, restart |
| `login <provider>` | Run CLIProxyAPI OAuth login; opens system browser (CLI form of the modal) |

First-run opt-in reuses the `CronInstallModal` (Yes/No → `service.install()`).

## Login modal (browser-launcher + poller)

**Constraint (verified vs Hermes research):** provider auth is browser-based
OAuth. The modal **cannot** be a credential form — it launches the system
browser and polls for completion. Built on the `NewPersonaModal` state machine
(select → spinner → success/error) and `SelectModal` list styling.

```
┌─ Sign in to a provider ──────────── esc ─┐
│   Claude      ✓ authenticated            │
│ ▶ OpenAI      ✗ not signed in            │
│   Gemini      ✗ not signed in            │
│   Grok        ✓ authenticated            │
│   ↑↓ move · enter sign in · esc close    │
└──────────────────────────────────────────┘
        │ enter → shell out to login cmd → opens SYSTEM BROWSER
        ▼  ◐ Waiting for browser sign-in…  (poll /v0/management ~2s)
        ▼  ✓ OpenAI connected   (or ✗ error + retry)
```

- Provider `✓/✗` status from the management API on mount and after each login.
- Reuses spinner glyphs `['◐','◓','◑','◒']` @ 0.15s, `set_error()` keeps modal
  open for retry — consistent with the existing modal family.
- `dn proxy login <provider>` is the headless CLI path.

**OPEN ITEM — resolve in plan, do NOT guess:** the exact CLIProxyAPI login
command syntax (e.g. `cli-proxy-api --login <provider>`) is in the project Wiki,
not the fetched README/docs/config. The plan resolves it by inspecting the
downloaded binary's `--help`, not by assumption.

## Config & per-persona model routing

- `done.conf` gains a `[proxy]` section: `port`, pinned `version`, default `model`.
- Per-persona model (already in `done.conf [agents.<id>]`) routes unchanged — the
  persona's model id is just an alias CLIProxyAPI serves.

### API-key upstreams (NeuralWatt → GLM) — config + docs, no modal

NeuralWatt is a plain OpenAI-compatible endpoint (`https://api.neuralwatt.com/v1`
+ `NEURALWATT_API_KEY`). It maps directly onto CLIProxyAPI's `openai-compatibility`
block (the same mechanism CLIProxyAPI uses for OpenRouter):

```yaml
openai-compatibility:
  - name: "neuralwatt"
    base-url: "https://api.neuralwatt.com/v1"
    api-key-entries:
      - api-key: "${NEURALWATT_API_KEY}"
    models:
      - name: "<neuralwatt-GLM-model-id>"   # resolve exact id from NeuralWatt /v1/models
        alias: "glm"                          # client-visible alias the harness requests
```

The harness sees `glm` in `/v1/models` and routes a persona to it like any other
model — **no special-casing**. Documentation (a dedicated "Adding an API-key
upstream" doc section with this NeuralWatt example) is the deliverable; no modal.

## Migration strategy (non-breaking)

Chosen approach: **single-file rename**, smallest blast radius.

- `vibeproxy.py` → `proxy.py` reads `PROXY_*` first, falls back to `VIBEPROXY_*`.
- The scattered literal `"VIBEPROXY_MODEL" in os.environ` shell-detection checks
  (in `acp_main.py`, `tui_main.py`, `persona_sessions.py`, `jobs/executor.py`)
  stay **untouched** — they keep working because the old env name is still honored.
- New docs use `PROXY_MODEL`; existing `VIBEPROXY_MODEL` setups keep working.

**Known limitation (accepted, not in scope):** the shell-vs-dotenv precedence
ladder still keys only on `VIBEPROXY_MODEL`. A user who sets **only** `PROXY_MODEL`
in their *shell* (not `.env`) resolves the model correctly via `default_model()`
but won't trip the persona-override precedence subtlety. If this ever bites, the
clean follow-up is to centralize the env names into one
`proxy.model_set_in(env)` helper and update the ~8 check sites.

Migration doc steps: `dn proxy install` → `dn proxy login` per OAuth provider →
add `openai-compatibility` block for NeuralWatt → (optionally) rename
`VIBEPROXY_*` to `PROXY_*` in `.env`.

## Testing

- `proxy.py` keeps the `backend="mock"` seam → existing unit tests unaffected.
- `proxy_service.py` gets cron-supervisor-style tests: install writes config,
  status parses the management API response, checksum verification, plist/unit
  generation. All mocked — no real binary in CI.
- Login modal: test the state machine (select → polling → success/error) with a
  mocked management API and a mocked login subprocess. No real browser/OAuth.

## Open Items (for the implementation plan)

1. **Exact login command syntax** — inspect binary `--help`; do not assume.
2. **Exact NeuralWatt GLM model id** — read from NeuralWatt `/v1/models`.
3. **Pinned CLIProxyAPI version + release-asset URL pattern + checksum source**
   (project is very active — v7.2.47, 728 releases — pin deliberately).
4. **launchd/systemd unit details** — reuse cron service module's patterns.

## References

- CLIProxyAPI: https://github.com/router-for-me/CLIProxyAPI (MIT, Go)
- NeuralWatt OpenCode docs: https://portal.neuralwatt.com/docs/integrations/opencode
- Cron OS-service precedent: PR #163 (`cron-daemon → OS service`)
- Existing seam: `harness/vibeproxy.py`
- Modal patterns: `harness/tui/widgets/{select,new_persona,cron_install}_modal.py`
