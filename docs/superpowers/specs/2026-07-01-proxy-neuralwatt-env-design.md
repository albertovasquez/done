# Design: NeuralWatt models (glm/qwen) vanish from `dn proxy` unless the key is exported

**Date:** 2026-07-01
**Status:** approved (brainstorming)
**Scope:** `harness/proxy_service/` — two small fixes + tests + a docs correction.

## Problem

The `/models` picker in the TUI is a live read of the running proxy's
`/v1/models` (`harness/tui/app.py::_fetch_models`, ~line 989). It shows exactly
what the CLIProxyAPI service serves — no hardcoded fallback. Which models the
proxy serves is fixed by the `config.yaml` that `dn proxy install`/`upgrade`
generate.

NeuralWatt models (aliases `glm`, `qwen`, `glm-fast`) are only written into that
config when `config_gen.generate()` finds `NEURALWATT_API_KEY` in `os.environ`
(`harness/proxy_service/config_gen.py:46`, `if nw_key:`). The user's key lives
durably in `~/.config/harness/.env`. Two independent defects keep it from being
seen:

1. **The `dn proxy` CLI never loads `.env`.** `harness/tui_main.py` intercepts
   `raw[0] == "proxy"` and returns via `proxy_cli.run()` at lines 96–98 —
   *before* the TUI's `paths.load_env(cwd)` call at line 118. So on the proxy
   path `os.environ` never receives `~/.config/harness/.env`, and
   `generate()` reads no key and omits the NeuralWatt block. It only works if the
   user happened to `export NEURALWATT_API_KEY` in the shell they ran `dn` from.

2. **`upgrade()` never regenerates `config.yaml`.** `config_gen.generate()` +
   `write_text` appears only inside `install()`
   (`harness/proxy_service/lifecycle.py:71–74`). `upgrade()` (lines 101–115)
   re-downloads the binary and does stop+start, but never rewrites config. Yet
   `docs/proxy.md:303` tells users that setting the key after install requires
   `dn proxy upgrade`. That remedy cannot work — only `install` rewrites config.

Together these mean: a user who has the key in `~/.config/harness/.env` and
follows the docs (`dn proxy upgrade`) still sees no glm/qwen. This is the exact
trap the user is in. The observed symptom — "glm/qwen only appear after a
`dn proxy install`, and disappear in new sessions" — is fully explained: `install`
is the only command that rewrites config, and it only succeeded when the key was
in that session's env.

### Verified against live code (2026-07-01)

- `~/.config/harness/.env` contains `NEURALWATT_API_KEY`; it is **absent** from
  the process env. `paths.load_env()` in a bare CLI context populates it
  (`NW key now in env: True`).
- The installed `~/.local/share/harness/proxy/config.yaml` (143 bytes) has **no**
  NeuralWatt block; the running proxy serves Claude + OpenAI models but not
  glm/qwen.
- `harness.paths` does **not** import `proxy_service` → no import cycle from
  adding `from harness import paths` to `proxy_service/cli.py`.
- `upgrade()` contains no `config_gen.generate()` call.

## Design

Two-part fix. Neither touches `config_gen.generate()`, which stays pure (env is
an injected parameter — see `tests/test_proxy_config_gen.py`).

### Part 1 — load `.env` in the proxy CLI

At the top of `harness/proxy_service/cli.py::run()`, before dispatch:

```python
def run(argv) -> int:
    from harness import paths
    paths.load_env()          # surface ~/.config/harness/.env (NEURALWATT_API_KEY, etc.)
    ...
```

- Called with **no `project_dir`** — deliberately. `dn proxy install` is a
  machine-global operation (writes one launchd/systemd service for the whole
  user). It must read only `~/.config/harness/.env`, not a per-project `./.env`
  that happens to sit in the invocation directory. A per-repo key leaking into
  the global proxy config would be surprising. This is a scoping decision, not a
  copy of the TUI's `load_env(cwd)`.
- `load_env` uses `override=False`, so a shell-exported key still wins over the
  `.env`. Documented precedence (shell > .env) is preserved.
- Placed in `run()` (one call) rather than in individual handlers: it is inert
  for commands that don't read the key (`status`, `stop`, `uninstall`), and any
  future proxy subcommand inherits it. `load_dotenv` on a missing file is a
  no-op (returns `False`).
- No import cycle (verified).

### Part 2 — `upgrade()` regenerates config

Insert config regeneration into `upgrade()` **after** the binary download and
**before** stop/start, mirroring `install()` Step 2, so the restarted service
reads the fresh config:

```python
def upgrade() -> str:
    try:
        download.download_and_install(binary.PINNED_VERSION)
    except download.ChecksumMismatch as exc:
        return f"CLIProxyAPI upgrade: binary verification failed — {exc}"
    except Exception as exc:
        return f"CLIProxyAPI upgrade: download error — {exc}"

    # Regenerate config so a newly-set NEURALWATT_API_KEY (or any config change)
    # is picked up on restart. install() does this; upgrade() must too, or the
    # documented "set key then upgrade" remedy silently does nothing.
    try:
        config_gen.ensure_management_password()
        cfg_path = paths.config_path()
        cfg_path.write_text(config_gen.generate())
    except Exception as exc:
        return f"CLIProxyAPI upgrade: config write failed — {exc}"

    stop_result = stop()
    start_result = start()
    return f"CLIProxyAPI upgrade: complete ({stop_result}; {start_result})"
```

**Import note (avoid confusion):** there are two `paths` modules.
`lifecycle.py` already imports `harness.proxy_service.paths` (owns
`config_path()`, `data_dir()`) — Part 2's `paths.config_path()` refers to *that*
one, consistent with `install()`. Part 1's `from harness import paths` refers to
the *top-level* `harness.paths` (owns `load_env()`, `config_dir()`). Different
modules, same short name; do not conflate them. `config_gen` and
`proxy_service.paths` are already imported in `lifecycle.py`, so Part 2 adds no
new imports.

### Part 3 — docs correction

In `docs/proxy.md`, note that `dn proxy` now auto-loads
`~/.config/harness/.env`, so a manually-exported key is no longer required, and
`dn proxy upgrade` now regenerates config (so it is a valid remedy after setting
the key). Remove/adjust the line that implies the user must re-export the key.

## Components & boundaries

| Unit | Change | Depends on |
| --- | --- | --- |
| `proxy_service/cli.py::run` | add `paths.load_env()` before dispatch | `harness.paths` (no cycle) |
| `proxy_service/lifecycle.py::upgrade` | add config regeneration step | `config_gen`, `proxy_service.paths` (already imported) |
| `docs/proxy.md` | correct the "re-export / upgrade" guidance | — |
| `config_gen.generate` | **unchanged** — stays pure | — |

## Error handling

- `load_env()` on a missing `.env` is a no-op; no new failure surface on the
  proxy path.
- `upgrade()`'s new config write is wrapped in try/except returning a status
  string, matching `install()`'s Step 2 and `upgrade()`'s existing error style.
  A config-write failure aborts before restart (does not leave a downloaded
  binary running against a half-written config).

## Testing

1. **`load_env` on the proxy path** (`tests/test_dn_proxy_routing.py` or a new
   `tests/test_proxy_env_load.py`): monkeypatch `paths.config_dir()` to a tmp dir
   holding a `.env` with `NEURALWATT_API_KEY=nw-x`; ensure the key is absent from
   `os.environ`; call `proxy_cli.run(["status"])` with `lifecycle.status`
   stubbed to a no-op; assert `os.environ["NEURALWATT_API_KEY"] == "nw-x"`.
2. **`upgrade()` regenerates config** (`tests/test_proxy_lifecycle.py`):
   monkeypatch `download.download_and_install`, `stop`, `start` to no-ops and
   `paths.config_path()` to a tmp file; set `NEURALWATT_API_KEY`; call
   `upgrade()`; assert the written config contains the NeuralWatt
   `openai-compatibility` block (`neuralwatt`, `glm`).
3. **Precedence preserved**: a shell-set `NEURALWATT_API_KEY` is not overwritten
   by a different `.env` value (covered by `override=False`; assert if a cheap
   test fits the existing suite).
4. **Manual**: from a fresh shell (no export), `dn proxy upgrade`, then
   `curl -s localhost:8317/v1/models | grep glm` shows glm/qwen.
5. Full `tests/` suite green (`.venv/bin/python -m pytest tests/ -q`).

## Out of scope

- Caching the model list between sessions — rejected. The picker must reflect
  the live proxy; a cached glm/qwen would appear but 404 on selection when the
  running proxy doesn't serve it. The fix makes the proxy actually serve the
  models instead of faking the list.
- Any change to `config_gen.generate()`'s purity or signature.
- Wiring xai/kimi logins (already out of scope in docs).

## Rationale for rejected alternatives

- **Load `.env` inside `config_gen.generate()`**: reintroduces a filesystem side
  effect into a deliberately pure, param-injected function; breaks its tests'
  clean seam.
- **Persist the key into `config.yaml` and have `upgrade` preserve the block**:
  adds config-merge machinery to keep a block that regenerating-from-env already
  produces once the key is visible. Unneeded complexity.
