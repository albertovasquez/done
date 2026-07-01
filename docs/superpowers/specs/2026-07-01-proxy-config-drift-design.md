# Design: detect + surface proxy config drift (issue #279)

**Date:** 2026-07-01
**Status:** approved (brainstorming)
**Scope:** `harness/proxy_service/` + `harness/tui/app.py` — one new pure
function, one auto-install hook, two warn surfaces, tests.

## Problem

`harness/proxy_service/config_gen.py:generate()` snapshots
`NEURALWATT_API_KEY` into `config.yaml` only when `dn proxy install` or
`dn proxy upgrade` runs (`lifecycle.py:54,101`). Nothing detects that the key
(or the model map) changed since the last generate, and nothing regenerates
config automatically. The running CLIProxyAPI service keeps serving whatever
config it read at its last start.

Concretely: if a user adds/rotates `NEURALWATT_API_KEY` in
`~/.config/harness/.env` while the proxy is already running (or has never
been installed), GLM/Qwen models silently do not appear in
`curl localhost:8317/v1/models` until they remember to run
`dn proxy install`/`upgrade`. Nothing in the UI surfaces this gap.

This spec implements the direction agreed in
[issue #279](https://github.com/albertovasquez/done/issues/279), reviewed via
Fable 5 (see issue comments): reject any auto-restart of an already-running
proxy (it's a machine-global service other sessions/cron may depend on), and
split the fix into two safe cases.

## Design

### 1. `config_drift()` — pure detection, `harness/proxy_service/config_gen.py`

```python
def config_drift(*, env=None) -> str:
    """Return "missing", "drifted", or "ok".

    "missing"  — config.yaml does not exist yet (never installed).
    "drifted"  — config.yaml exists but differs from what generate() would
                 produce right now (key changed, model map changed, etc).
    "ok"       — config.yaml matches current generate() output.
    """
```

- No new marker/hash file. `generate()` is already deterministic given `env`,
  and `config.yaml` already contains the plaintext key — diff the full text.
- `config_path()` already exists at `harness/proxy_service/paths.py:12`.
- Must accept the same `env` parameter `generate()` accepts (default
  `os.environ`), so callers can pass whichever resolved env is correct for
  their context (see the env-source-asymmetry note below).
- Effectively pure: never writes `config.yaml`, never raises on a missing
  file (that's the `"missing"` case, not an error). Note `generate()` calls
  `paths.data_dir()`, which does `mkdir(parents=True, exist_ok=True)` — so
  calling `config_drift()` (which calls `generate()` to diff) can create the
  data dir as a side effect even when only checking status. Harmless
  (idempotent, same dir `install()` would create anyway) but worth knowing —
  this function is not 100%  read-only.

**Known caveat to verify during implementation:** `config_gen.py`'s existing
comment notes some secrets are bcrypt-hashed by CLIProxyAPI on boot. If the
running service rewrites `config.yaml` post-start (not just reads it), a
byte-compare against fresh `generate()` output would show permanent false
"drifted". First implementation task must confirm this does *not* happen
(inspect the CLIProxyAPI binary's behavior or existing `config.yaml` on a
running install: does its content stay byte-identical to what `install()`
wrote?). If it does rewrite, `config_drift()` must compare only the
`api-key`/model-list-relevant lines, not full-file equality.

### 2. Auto-install when missing — `session_start` hook, unconditional

- New hook handler registered on `session_start` (not `session_end` — decided
  in brainstorming: the detached spawn is non-blocking either way, so there's
  no speed cost to firing at session_start, and it gives the current session
  a chance to get a usable proxy instead of guaranteeing only a future
  session benefits).
- Fires in `harness/tui/app.py::on_mount`, alongside the existing
  cron-autostart block (~line 416-429), before `_hooks.dispatch("session_start", ...)`.
- Condition: `config_drift() == "missing"`.
- Action: spawn `dn proxy install` detached, exactly following the
  `harness/compress/auto_regen.py` precedent — `subprocess.Popen(...,
  start_new_session=True, stdout=DEVNULL, stderr=<log file>, close_fds=True)`.
  Never blocks session startup. Never raises past the handler (wrap in
  try/except, log + tracer.emit on failure, matching `auto_regen.py`'s
  `on_session_end` shape).
- **Only handles "missing".** Never fires when `config_drift() == "drifted"`
  — that case is warn-only (see below), by design, because a proxy process
  already exists that other sessions may be using.
- **Multi-session race is acceptable, not new risk.** If two sessions launch
  close together and both observe `"missing"`, both spawn `dn proxy install`
  concurrently. `install()`'s own steps are already idempotent-safe for this
  (`download.download_and_install` checks an existing stamp before
  re-downloading; OS-service registration checks `.exists()` before writing) —
  this is a pre-existing property of `install()`, not a new hazard introduced
  here. Worth one test asserting two concurrent `config_drift()=="missing"`
  hook fires don't crash, but not a blocking concern.

### 3. Warn-only on drift — two surfaces, no auto-restart ever

- **`lifecycle.status()`**: append a line when `config_drift() == "drifted"`:
  `"proxy config stale — run \`dn proxy upgrade\` to pick up changes."` This
  is the default `dn proxy` invocation and `cli.py` already calls
  `paths.load_env()` before it runs, so the env is correctly resolved there.
- **TUI `on_mount`**: a guarded one-line log (same style as the existing
  cron-autostart try/except at app.py:421-426) when drifted. Not a modal, not
  blocking — matches the existing "guarded one-liner" bootstrap logging
  pattern already in that method.
- **No code path anywhere auto-restarts a running proxy.** This is a hard
  constraint carried over from the issue's review, not a preference — remind
  reviewers of this if a future change proposes it.

### Env-source asymmetry (must hold)

`dn proxy` (`proxy_service/cli.py`) deliberately does NOT load a per-project
`.env` — proxy install/upgrade is machine-global, so it only loads
`~/.config/harness/.env` (see the existing comment in `cli.py:5-12`). The TUI,
by contrast, loads project `.env` too. `config_drift()` calls from each
context must use that context's own already-resolved env (whatever
`os.environ` looks like at the call site after that context's own
`load_env()`), not a fresh independent resolution — otherwise a per-project
key would cause a phantom "drifted" warning in the TUI that `dn proxy upgrade`
can never clear (since upgrade never sees that per-project key). This
requires no new code — just calling `config_drift()` after each context's
existing env-loading step, not before.

## Testing

- Unit tests for `config_drift()`: missing file → `"missing"`; file matches
  current `generate()` → `"ok"`; file differs (simulate a key change via the
  `env=` param) → `"drifted"`.
- Test the `session_start` hook handler: fires `Popen` only when `"missing"`;
  no-ops on `"drifted"`/`"ok"`; never raises out of the handler on a forced
  exception (mirrors existing `auto_regen.py` test patterns if any exist —
  check `tests/` for `test_compress_auto_regen*` or similar as a template).
- Test `status()` includes the warning line only when drifted.
- No live-proxy-required tests — everything here is testable via `env=`
  injection and file mocking, no real CLIProxyAPI process needed.

## Out of scope

- Auto-restart/auto-upgrade on drift while already installed — explicitly
  rejected per the issue review.
- Changing what `upgrade()`/`install()` do internally — this only adds
  detection + one new safe auto-install-if-missing path + warnings.
- A general user-facing hook/shell-hook layer — `harness/hooks.py` already
  documents this is internal-only for now; out of scope here.
