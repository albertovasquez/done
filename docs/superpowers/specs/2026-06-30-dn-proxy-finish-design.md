# Finish `dn proxy` — Binary Download + Lifecycle + Login Design Spec

**Date:** 2026-06-30
**Status:** Approved (brainstorm), pending implementation plan
**Branch:** `dn-proxy-finish`
**Goal:** Let a user authenticate Done against Codex (OAuth), Claude (OAuth), and GLM via NeuralWatt (API key) entirely through `dn proxy` commands.

## Summary

PR #195 shipped the CLIProxyAPI scaffolding but deliberately stubbed the parts
needing a live binary download + OAuth. This spec finishes those stubs. No
redesign — every module from #195 stays; we fill in the bodies that return "not
yet implemented" and correct the binary-download facts that shipped as
placeholders. Closes the actionable half of #193 (binary pin) and makes the
`dn proxy install / login / start / stop / uninstall / upgrade` surface real.

## Goals

- `dn proxy install` → download + verify + extract the real CLIProxyAPI binary,
  write config, register the OS service, and start it. One command, proxy running.
- `dn proxy login codex` / `dn proxy login claude` → browser OAuth, headless
  fallback (print URL) when no browser; auto-start the proxy if it isn't running.
- GLM via NeuralWatt works: documented `openai-compatibility` config + a way to
  set `NEURALWATT_API_KEY`.
- `dn proxy {start,stop,uninstall,upgrade}` implemented.
- `dn proxy status` (already works) shows authed providers + live models.

## Non-Goals

- No redesign of the #195 modules; keep the `proxy.py` seam and `proxy_service/`
  layout.
- The #194 hardening minors (login rc-type, modal thread-safety, unit-file perms)
  stay separate — not blocking this.
- The `VIBEPROXY_*` shim removal (#193's other half) stays separate.
- Antigravity/xai/kimi/gemini login: out of scope (only the three the user wants —
  codex, claude OAuth; GLM API-key). The provider map already includes the others
  for later; we don't wire their login flows now.
- `dn proxy login --print-url` (explicit headless mode): deferred; the automatic
  no-browser fallback covers the need now.

## Decisions (locked with user)

| Decision | Choice | Why |
|---|---|---|
| Version selection | **Pinned constant**, bumped per harness release | Reproducible; user controls upgrades. Checksum fetched from release, not hand-maintained. |
| Install location | **Harness data dir** (`~/.local/share/harness/proxy/`) | Consistent with shipped `paths.py` + cron; clean uninstall. No new config knob. |
| CLI login | **Reuse modal's browser-open + poll, headless** | One code path for TUI + CLI. |
| No-browser case | **Auto-fallback to printing the URL**, keep polling | Never hard-fails on SSH/servers (where cron Done runs). |
| `install` scope | **Full: download → config → register → start** | Cron "install once, it runs" model. |
| `login` preflight | **Auto-start the proxy if not running** | Login just works. |

## Verified upstream facts (corrects #195 placeholders)

Confirmed 2026-06-30 against the live GitHub releases API + help.router-for.me
(see memory `cliproxy-download-auth-facts`):

- **Release assets** (`GET /repos/router-for-me/CLIProxyAPI/releases/tags/<tag>`):
  - Tag is `v7.2.47`; asset filename uses bare version `7.2.47`.
  - Binary asset = `CLIProxyAPI_<ver>_<os>_<arch>.tar.gz` — a **versioned `.tar.gz`
    archive that must be extracted**, NOT a bare `cli-proxy-api-<plat>` binary.
  - Arch tokens are `aarch64` / `x86_64` (NOT `arm64`/`amd64`); OS is `darwin`/`linux`.
  - A `checksums.txt` asset lists sha256 per file; the GitHub API also exposes each
    asset's `digest: "sha256:..."`. → fetch the checksum, don't hardcode it.
- **The shipped `binary.py` `platform_key()` and `asset_url()` are both wrong** and
  must be corrected (wrong arch tokens, wrong filename shape, no extraction).
- **Login flow:** `GET /v0/management/{anthropic,codex}-auth-url` (Bearer mgmt pw)
  → `{status:"ok", url, state}` → open `url` → poll
  `GET /v0/management/get-auth-status?state=<state>` until terminal. Tokens save
  under CLIProxyAPI's `auths/` dir. (`anthropic` is the provider id for Claude.)

## Architecture (unchanged from #195; bodies filled in)

```
dn proxy <cmd> ──► tui_main route ──► proxy_service/cli.py ──► proxy_service/lifecycle.py
                                                                  │
   ┌──────────────────────────────────────────────────────────────┼─────────────┐
   ▼                  ▼                    ▼                        ▼             ▼
binary.py +       config_gen.py      service_launchd/         management.py   login.py
download.py(new)  (0600 mgmt pw)     systemd.py (units)       (auth-url/poll) (dispatch)
(fetch+verify+                                                                 + headless
 extract tarball)                                                              CLI runner
```

### Component: `binary.py` (corrected) + `download.py` (new)

- `binary.py` constants/helpers corrected:
  - `PINNED_VERSION = "v7.2.47"` (tag form; a real, verified release).
  - `platform_key()` → returns `(os, arch)` with arch in CLIProxyAPI tokens:
    `x86_64` stays `x86_64`; `arm64`/`aarch64` → `aarch64`. OS `darwin`/`linux`.
  - `asset_name(version)` → `CLIProxyAPI_<ver-without-v>_<os>_<arch>.tar.gz`.
  - `asset_url(version)` → `https://github.com/<repo>/releases/download/<tag>/<asset_name>`.
  - `checksums_url(version)` → `.../download/<tag>/checksums.txt`.
  - `target_path()` → `paths.data_dir()/cli-proxy-api` (the extracted binary). unchanged.
  - `verify_checksum(path, expected_sha256)` — unchanged (already correct).
- `download.py` (new, the orphaned-verify gap closed):
  - `fetch_checksums(version) -> dict[filename, sha256]` — GET `checksums.txt`, parse.
  - `download_and_install(version) -> Path`:
    1. download the `.tar.gz` to a temp file,
    2. look up its expected sha256 from `fetch_checksums`,
    3. `verify_checksum` — **abort if mismatch** (no unverified binary ever runs),
    4. extract the archive, locate the inner binary, move it to `target_path()`,
       `chmod +x`,
    5. return `target_path()`.
  - stdlib only: `urllib.request`, `tarfile`, `hashlib`, `tempfile`, `shutil`.

### Component: `lifecycle.py` (stub bodies filled)

- `install()`: `download.download_and_install(PINNED_VERSION)` →
  `config_gen.generate` + `ensure_management_password` (already) →
  `_register_os_service` (already) → **start the service** → readiness-poll
  `management.is_ready` until up or timeout. Returns the step log.
- `upgrade()`: same as install's download step for `PINNED_VERSION`, replacing the
  binary, then restart the service. (Re-download even if present, since a harness
  bump changes the pin.)
- `start()` / `stop()`: shell out to the platform service manager
  (`launchctl kickstart`/`bootout` or `systemctl --user start/stop`), mirroring
  how `service_launchd`/`service_systemd` already register. Each catches shell-out
  failure and returns a human-readable string.
- `uninstall()`: stop + deregister the service (reuse the service modules'
  bootout/disable), then remove the harness proxy data dir. Leave `auths/` note:
  removing the data dir drops downloaded tokens too — state this in the output.
- `login(provider)`: preflight `is_ready`; if not, call `start()` and re-poll.
  Then delegate to the CLI login runner (below). Validates provider against the
  browser-OAuth set (`anthropic`, `codex`) for this scope.

### Component: `login.py` (CLI headless runner added)

The existing `login.start(provider, password, *, open_browser, run_subprocess)`
stays. Add a headless CLI driver used by `lifecycle.login`:

```
run_cli_login(provider, password, *, open_browser=webbrowser.open,
              poll=management.poll_auth_status, sleep=time.sleep,
              out=print) -> bool
  url, state = management.auth_url(provider, password)
  if not open_browser(url):        # headless fallback
      out(f"open this URL to sign in:\n  {url}")
  else:
      out("opened browser — waiting for sign-in…")
  loop poll(state, password) until terminal ("ok"/"success") or timeout:
      sleep(interval)
  return True on success, False on timeout/failure
```

All I/O is injected (browser, poll, sleep, out) so it is unit-testable with fakes
— no real browser/network/sleep in tests. This mirrors the #195 testability rule.

### GLM via NeuralWatt (API-key upstream)

No login command — it's config. Provide:
- A documented `openai-compatibility` block (already in `docs/proxy.md` from #195)
  appended to the generated `config.yaml`. Since `config_gen.generate()` currently
  emits a fixed localhost config, add an **optional upstreams section**: if
  `NEURALWATT_API_KEY` is set in the environment, `generate()` appends the
  NeuralWatt `openai-compatibility` block with the `glm` alias. If unset, omit it
  (no broken upstream). The exact GLM model id is read from NeuralWatt
  `/v1/models` and documented as the value to fill — defaulted to a placeholder
  the user confirms once.
- `dn proxy status` already lists live model aliases, so `glm` shows up once the
  proxy is running with the key set.

## Error handling

- **Checksum mismatch** → abort install/upgrade with a clear error; never run an
  unverified binary. (This is the security-critical path.)
- **Download network failure** → caught, human-readable message, non-zero result;
  config/service untouched so a retry is clean.
- **No browser** → auto-fallback to printing the URL (not an error).
- **Login timeout** → return False with "sign-in didn't complete — re-run
  `dn proxy login <provider>`"; the proxy stays up.
- **Service shell-out failure** (launchctl/systemctl) → caught and reported, as the
  existing `_register_os_service` already does.

## Testing

- `binary.py` corrected helpers: unit tests for `platform_key` token mapping,
  `asset_name`/`asset_url`/`checksums_url` string shape (darwin_aarch64,
  linux_x86_64), version-without-v handling.
- `download.py`: `fetch_checksums` parses a sample `checksums.txt`;
  `download_and_install` tested with injected fakes for urlopen + a tiny in-memory
  tar.gz fixture → asserts checksum-mismatch ABORTS (no file installed) and a
  matching checksum installs + chmods. No real network.
- `login.run_cli_login`: injected browser/poll/sleep/out fakes → asserts browser
  path, headless-fallback path (open_browser returns False → URL printed), success
  on terminal status, timeout returns False. No real I/O.
- `lifecycle`: `install`/`upgrade`/`start`/`stop`/`uninstall` tested with the
  download + service shell-outs mocked; assert the step sequence and that
  `login` auto-starts when `is_ready` is False. Routing test (#195) stays green.
- `config_gen.generate`: with `NEURALWATT_API_KEY` set → includes the neuralwatt
  block + `glm` alias; unset → omitted.

## Open Items (resolve in plan)

1. **Confirm `v7.2.47` is the version to pin** (or bump to the then-latest at
   implementation time) and that its `checksums.txt` + `darwin_aarch64` /
   `linux_x86_64` assets exist. (Spot-checked present today.)
2. **Exact `get-auth-status` terminal value** ("ok" vs "success" vs "completed")
   — read the live response once during implementation; `run_cli_login`'s
   terminal check keys on it. (Default: treat `status in {"ok","success","completed"}`
   as done, anything else as pending.)
3. **Inner tarball layout** — confirm the binary's path inside
   `CLIProxyAPI_*.tar.gz` (top-level `cli-proxy-api` vs nested) to locate it on
   extract.
4. **NeuralWatt GLM model id** — the exact upstream model name for the `glm` alias
   (from NeuralWatt `/v1/models`).

## References

- `cliproxy-download-auth-facts` (verified release/auth facts), `cliproxyapi-integration-design` (the #195 build)
- Shipped modules: `harness/proxy_service/{binary,config_gen,lifecycle,login,management,service_launchd,service_systemd}.py`
- Issues: #193 (binary pin — this closes the actionable half), #194 (login hardening — separate)
