# CLIProxyAPI Integration — Design Spec

**Date:** 2026-06-30
**Status:** Revised (v2, post-Codex review) — pending implementation plan
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
capability-aware `dn proxy login <provider>` TUI modal.

## Revision history

- **v2 (2026-06-30):** Rewritten after an adversarial Codex review whose six
  findings were each re-verified by the author against live CLIProxyAPI docs
  (`help.router-for.me`) and source (`cmd/server/main.go`) and the live harness.
  Material changes vs v1: (1) env migration is no longer "single-file rename" —
  it **must centralize env-name handling** across the resolution path; (2)
  dropped `done.conf [proxy].default model` (second-model-home violation); (3)
  login modal **splits by provider capability** (no uniform login flow exists);
  (4) management secret uses the in-memory `MANAGEMENT_PASSWORD` path, not a
  hashed config key; (5) OS-service unit fully specified; (6) proxy-service
  `api-keys` auth decision added.

## Goals

- Make CLIProxyAPI a managed dependency: install, run-as-service, status, login —
  no "did you start the proxy manually?" friction.
- Support **all** OAuth providers (Claude, OpenAI/Codex, Gemini, Grok) and
  **API-key upstreams** (NeuralWatt→GLM, OpenRouter) through one proxy.
- Keep the existing per-persona model flow working unchanged.
- Preserve a thin Python seam so the proxy implementation can be swapped later.
- Migrate the env-var surface from `VIBEPROXY_*` to `PROXY_*` **correctly** —
  meaning the model-resolution precedence ladder honors both names everywhere it
  is consulted, not just in the leaf accessor.

## Non-Goals

- **Per-step / per-mission model selection** (different model per mission step by
  workload). Forward path once Missions land; see [[get-missions-done-epic]].
- A modal for API-key upstreams. Those are set-once config + docs.
- Embedding CLIProxyAPI's Go SDK in-process (SDK is Go-only; harness is Python).
- A `done.conf`-level default worker model. The per-persona model in
  `done.conf [agents.<id>]` is the single home; see the precedence section.

## Decisions (locked with user + Codex-verified constraints)

| Decision | Choice | Why |
|---|---|---|
| Integration shape | **Managed dependency**, thin `proxy.py` seam | Same pattern as cron; swappable later |
| Install method | **Download pinned binary** on `dn proxy install` (GitHub release, checksum-verified) | No Go toolchain ask; reproducible |
| Lifecycle | **OS service** (launchd/systemd) with explicit unit | Survives reboots; mirrors `dn cron` |
| Providers | All — Claude, OpenAI/Codex, Gemini, Grok | CLIProxyAPI round-robin is the win over VibeProxy |
| Worker model | **Per-persona** from `done.conf [agents.<id>]` — single home | No second model home (see precedence) |
| Login UX | **Capability-split** modal (browser+poll vs CLI-flag vs API-key) | No uniform login flow exists upstream |
| API-key upstreams | **Config-file + docs only**, no modal | Set-once; only OAuth needs the browser dance |
| Env migration | **Centralize env-name handling** (dual-name `PROXY_*`/`VIBEPROXY_*`) across the whole resolution path | Single-file rename is incorrect (verified) |
| Mgmt secret | **In-memory `MANAGEMENT_PASSWORD`**, not a config secret-key | Config plaintext gets bcrypt-hashed on boot |

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

### Component: `harness/proxy.py` (the connection seam)

Renamed from `harness/vibeproxy.py`. Same thin contract, still imports only `os`
(no litellm — callers own the litellm call; litellm costs ~1s at import and sits
on the startup path).

Exports (unchanged signatures): `base_url()`, `api_key()`, `default_model()`,
`model_id(name)`, `completion_kwargs()`, `model_kwargs()`, plus **new**
`model_env_names()` / `model_set_in(env)` (see precedence).

Env reads use dual-name fallback:

```python
def base_url() -> str:
    return os.getenv("PROXY_BASE_URL") or os.getenv("VIBEPROXY_BASE_URL") or _DEFAULT_BASE_URL
def api_key() -> str:
    return os.getenv("PROXY_API_KEY") or os.getenv("VIBEPROXY_API_KEY") or _DEFAULT_API_KEY
def default_model() -> str:
    return os.getenv("PROXY_MODEL") or os.getenv("VIBEPROXY_MODEL") or DEFAULT_MODEL
```

A one-release module alias (`vibeproxy = proxy`) avoids touching importers.

## Model-precedence migration (the load-bearing change)

**Finding (CRITICAL, verified vs live code):** `default_model()` is NOT the only
place the worker model is resolved. The TUI/ACP startup path snapshots the model
*before* `load_env` and feeds it straight into the precedence ladder, bypassing
`default_model()` entirely:

- `harness/tui_main.py:112` — `shell_set_model = "VIBEPROXY_MODEL" in os.environ`
- `harness/acp_main.py:117` — same literal snapshot; then
  `shell_env = os.getenv("VIBEPROXY_MODEL")` (`acp_main.py:141`) is passed as both
  `shell_env` and `dotenv` into `resolve_session_model`
  (`harness/persona_sessions.py:20-40`), whose ladder is
  shell > persisted(`done.conf`) > dotenv > `DEFAULT_MODEL`.

So a user who sets only `PROXY_MODEL` (shell or `.env`) is **invisible** to this
ladder — `default_model()`'s fallback never runs on this path. A "single-file
rename" is therefore **incorrect**.

**Required fix (accepted larger blast radius):** centralize env-name handling.

1. Add to `proxy.py`:
   ```python
   _MODEL_ENVS = ("PROXY_MODEL", "VIBEPROXY_MODEL")     # precedence: PROXY_ wins
   def model_set_in(env) -> bool: return any(k in env for k in _MODEL_ENVS)
   def model_value(env) -> str | None:
       for k in _MODEL_ENVS:
           if env.get(k): return env[k]
       return None
   ```
2. Replace every literal `"VIBEPROXY_MODEL" in os.environ` snapshot and every
   `os.getenv("VIBEPROXY_MODEL")` read on the resolution path with these helpers:
   `tui_main.py`, `acp_main.py`, `persona_sessions.py`, `jobs/executor.py`,
   `jobs/cron_main.py`, `run_traced.py`, `compress_cli.py`, and their tests.
3. Define explicit conflict precedence when **both** names are set: `PROXY_MODEL`
   wins; document it.

This is the change v1 wrongly scoped as one file. It is mechanical but spans
~8 modules + tests, and must land **before** any doc tells users to use
`PROXY_MODEL`.

### No second model home

`done.conf` gains **no** `[proxy].default model`. `persona_sessions.py:1-5` states
the single-home rule ("No second model home: persisted model lives only in
done.conf"); a `[proxy]` default would reintroduce the dual-home clobber trap
([[persona-phaseC1-selection]]). The `[proxy]` section holds **only**
infrastructure (`port`, pinned `version`) — never a model.

## CLIProxyAPI config & management secret

Generated by `dn proxy install` into a harness-owned `config.yaml`:

- `port: 8317`, `host: "127.0.0.1"` (localhost only).
- `openai-compatibility:` — API-key upstreams (NeuralWatt, OpenRouter).

**Management secret (HIGH, verified):** CLIProxyAPI bcrypt-hashes a plaintext
`remote-management.secret-key` on startup and writes the hash back; API requests
require the *plaintext* (`Authorization: Bearer <plaintext>` or `X-Management-Key`).
Storing the secret only in `config.yaml` loses the usable plaintext after boot.

**Decision:** the harness does **not** use `remote-management.secret-key`. It
launches CLIProxyAPI with the **`MANAGEMENT_PASSWORD` env var** (verified:
"never persisted... only lives in memory"; forces management reachable on
localhost). The harness generates this secret per-install, stores it in a
0600 harness-owned file, injects it into the service env, and sends it on every
`/v0/management` poll. No hashed-config roundtrip. (`cliproxy run --password` /
SDK `WithLocalManagementPassword` are equivalent localhost-only alternatives.)

## Proxy-service auth (`api-keys`)

**Finding (MEDIUM, verified):** CLIProxyAPI has a proxy-service `api-keys` auth
layer (managed via `/v0/management/api-keys`). The harness currently sends
`api_key="dummy-not-used"`.

**Decision:** generated configs run the proxy **with client auth disabled**
(empty `api-keys`), since it binds localhost-only. `proxy.api_key()` keeps
returning a harmless placeholder (litellm requires a non-empty string). If a
user enables `api-keys`, they set `PROXY_API_KEY` to match — documented, not
default.

## Lifecycle commands (`dn proxy`)

| Command | Action |
|---|---|
| `install` | Download pinned binary → checksum → write `config.yaml` → generate mgmt password (0600) → register OS service → first-run opt-in modal |
| `uninstall` | Stop + deregister service; leave config/auth dirs intact |
| `start` / `stop` | Manual service control |
| `status` | Service liveness + authed providers (`/v0/management` poll) + live model aliases |
| `upgrade` | Bump pinned version, re-download, restart |
| `login <provider>` | Provider-appropriate login (see capability split) |

### OS-service unit (MEDIUM finding — fully specified)

Mirror the cron service modules (`harness/jobs/service_launchd.py`,
`service_systemd.py`):

- **ExecStart/ProgramArguments:** `<binary> --config <harness-config-path>`
  (explicit `--config` — CLIProxyAPI otherwise defaults to `config.yaml` in the
  process working dir).
- **Working dir:** the harness config dir.
- **Environment:** inject `MANAGEMENT_PASSWORD=<generated>`.
- **Logs:** `StandardOutPath`/`StandardErrorPath` (launchd) /
  `StandardOutput=append:` (systemd) under the harness runs/log dir.
- **Restart policy:** launchd `KeepAlive=true` + `ThrottleInterval`; systemd
  `Restart=always` + `RestartSec=5` (match cron precedent).
- **Readiness:** after start, poll `GET /v0/management` (with the mgmt password)
  until 200 or timeout, before reporting "running".
- **Port collision:** if `:8317` is already bound by a non-harness process,
  `install`/`start` fails with a clear message rather than silently colliding.

## Login UX — capability-split (HIGH finding, verified vs `main.go` + mgmt docs)

There is **no uniform login flow**. Verified facts:

- **Browser + management-API poll** (Anthropic/Claude, Codex, Antigravity):
  `GET /v0/management/{anthropic,codex,antigravity}-auth-url` →
  `{ "url": ..., "state": ... }` → open `url` in system browser → poll
  `GET /v0/management/get-auth-status?state=...` until done.
- **CLI-flag subprocess** (xAI/Grok, Kimi): binary flags `--xai-login`,
  `--kimi-login` (also `--claude-login`, `--codex-login`,
  `--antigravity-login`, `--codex-device-login`; plus `--no-browser`,
  `--oauth-callback-port`). **No `--gemini-login`, no generic `--login`.**
  Modal shells out and parses process exit/status.
- **Gemini / AI Studio:** separate flow (browser app / Generative Language API
  key), **not** an `auth-url` endpoint. Treated as **docs-only / API-key** in v1
  of this feature unless a concrete OAuth path is verified later.

The modal lists providers with `✓/✗` status (from a management status poll) and
dispatches each provider to its correct mechanism. State machine
(select → working → success/error) and styling reuse `NewPersonaModal` /
`SelectModal`. Concretely:

```
┌─ Sign in to a provider ──────────── esc ─┐
│   Claude   ✓ authenticated  (browser+poll)│
│ ▶ OpenAI   ✗ not signed in  (browser+poll)│
│   Grok     ✗ not signed in  (CLI flag)    │
│   Gemini   — API-key (see docs)           │
│   ↑↓ move · enter sign in · esc close     │
└──────────────────────────────────────────┘
```

## API-key upstreams (NeuralWatt → GLM) — config + docs, no modal

NeuralWatt is a plain OpenAI-compatible endpoint (`https://api.neuralwatt.com/v1`
+ `NEURALWATT_API_KEY`). It maps onto CLIProxyAPI's `openai-compatibility` block
(same mechanism as OpenRouter):

```yaml
openai-compatibility:
  - name: "neuralwatt"
    base-url: "https://api.neuralwatt.com/v1"
    api-key-entries:
      - api-key: "${NEURALWATT_API_KEY}"
    models:
      - name: "<neuralwatt-GLM-model-id>"   # resolve from NeuralWatt /v1/models
        alias: "glm"                          # client-visible alias
```

The harness sees `glm` in `/v1/models` and routes a persona to it like any other
model. A dedicated "Adding an API-key upstream" doc section with this NeuralWatt
example is the deliverable; no modal.

## Testing

- `proxy.py` keeps the `backend="mock"` seam → existing unit tests unaffected.
- **Precedence tests (new, critical):** assert `PROXY_MODEL` and `VIBEPROXY_MODEL`
  each resolve correctly via the centralized helpers across shell-set vs
  `.env`-set vs `done.conf`-persisted, including the both-set conflict
  (`PROXY_MODEL` wins). Cover `tui_main`/`acp_main`/`persona_sessions`/executor.
- `proxy_service.py`: install writes config + 0600 mgmt password; status parses a
  mocked `/v0/management`; checksum verification; launchd/systemd unit generation
  includes `--config` and `MANAGEMENT_PASSWORD`. All mocked — no real binary in CI.
- Login modal: test capability dispatch (browser+poll vs CLI-flag vs API-key) with
  mocked management API + mocked login subprocess. No real browser/OAuth.

## Open Items (for the implementation plan)

1. **Exact NeuralWatt GLM model id** — read from NeuralWatt `/v1/models`.
2. **Pinned CLIProxyAPI version + release-asset URL pattern + checksum source**
   (v7.2.47, 728 releases — pin deliberately).
3. **Gemini/AI Studio**: confirm whether a management-API OAuth path exists; if
   not, keep it API-key/docs-only for v1.
4. **`get-auth-status` exact response shape** for the poll loop (field names,
   terminal states) — read from `help.router-for.me/management/api`.

## References (verified)

- CLIProxyAPI: https://github.com/router-for-me/CLIProxyAPI (MIT, Go)
- Management API: https://help.router-for.me/management/api (auth-url + state
  poll; plaintext key; `MANAGEMENT_PASSWORD`; `api-keys`)
- Basic config: https://help.router-for.me/configuration/basic (`--config`;
  secret-key bcrypt-hashed on startup)
- Login flags: `cmd/server/main.go:91-110` (`--claude-login`, `--codex-login`,
  `--xai-login`, `--kimi-login`, `--antigravity-login`; no `--gemini-login`)
- NeuralWatt OpenCode docs: https://portal.neuralwatt.com/docs/integrations/opencode
- Cron OS-service precedent: PR #163; `harness/jobs/service_{launchd,systemd}.py`
- Live precedence path: `harness/{tui_main,acp_main,persona_sessions}.py`,
  `harness/jobs/executor.py`
- Modal patterns: `harness/tui/widgets/{select,new_persona,cron_install}_modal.py`
