# Proxy config self-heal (delta on #292) + session-start model availability warning

**Date:** 2026-07-01 (revised 2026-07-02 after PR #292 merged)
**Status:** Implemented (this branch)

**Implementation note:** during final verification, the actual cause of the
user's recurring config loss was found and fixed on this branch:
`tests/test_proxy_lifecycle.py::test_install_downloads_then_registers_and_starts`
ran the real `install()` without isolating `paths.data_dir`, overwriting the
real `~/.local/share/harness/proxy/config.yaml` (keyless, since `generate()`
then read raw `os.environ`) on every test-suite run. Fixed by hermetic
isolation + a suite-wide conftest default (`config_drift` → "ok" unless a test
overrides), plus a mock-mode skip in the TUI drift check.

## Problem

`~/.local/share/harness/proxy/config.yaml` is derived state: `config_gen.generate()`
bakes provider keys and the NeuralWatt model list into a yaml that CLIProxyAPI
reads at boot. Observed on 2026-07-01 (ten `dn proxy install` runs in one shell
history day):

1. A terminal with an empty/stale `NEURALWATT_API_KEY` export masks the real key
   in `~/.config/harness/.env` → every install from that terminal silently
   writes a **keyless** config, and `install()` reports "running" anyway.
2. An agent whose configured model the proxy does not serve fails only at first
   LLM call, as an opaque `BadGatewayError: unknown provider for model glm-5.2`
   after a ~4.5-minute same-id retry loop (issue #290).

**Goal (user's words):** low friction when selecting models for agents; if
nothing changes in a version, nothing should need adjusting.

## What #292 already covers (do not re-implement)

PR #292 (`9aa3083`, closes #279) shipped:

- `config_gen.config_drift()` — "missing"/"drifted"/"ok" text comparison of
  on-disk config vs current `generate()` output (was section B of the original
  design).
- `proxy_service/auto_install.py` — session-start hook that auto-installs when
  config is **missing** (never installed). Detached spawn, never blocks.
- TUI `_check_proxy_config_drift()` — warns on "drifted" at startup:
  "proxy config stale — run `dn proxy upgrade`".
- **Hard constraint adopted:** no code path ever auto-restarts an
  already-running proxy — it is a machine-global service other sessions and
  cron may depend on. This spec honors that constraint; the original design's
  write-and-kickstart self-heal is dropped.

## Remaining gaps this spec closes

### A. Empty-string env keys are treated as set (the poisoned-shell foot-gun)

`_machine_global_env()` does `merged.update(os.environ)` — an exported
`NEURALWATT_API_KEY=""` beats the real key in `~/.config/harness/.env`, so
`dn proxy install` from that terminal writes a keyless config. Worse, the TUI
drift check overlays the pre-launch shell snapshot even when it is `""`
(`if self._shell_neuralwatt_key is not None`), so the same terminal that wrote
the bad config also sees "ok" and **suppresses the drift warning**. The
2026-07-01 failure still reproduces end-to-end after #292.

**Change:** empty-string values are treated as absent at every consumption
point — `_machine_global_env()` drops empty values after the merge,
`generate()` already skips falsy keys, and the TUI overlay skips
`self._shell_neuralwatt_key == ""` (treat like None). A non-empty shell export
still wins (preserves #292's documented precedence and any legit override);
only the "empty export masks real key" case dies.

**Additionally:** when a file key exists but is masked by a *different*
non-empty shell value, `install()`/`upgrade()` say so in their output
(one line: `note: shell NEURALWATT_API_KEY overrides ~/.config/harness/.env`).

*(Original design said "durable file only" — revised to empty-is-absent +
masking notice because #292 merged an explicit, documented process-env-wins
precedence; fighting it now would churn fresh code. The foot-gun dies either
way. Flagged for user re-approval.)*

### B. Drifted config: one-keypress consent instead of a shell errand

#292 warns "run `dn proxy upgrade`" — still a manual step. Honoring the
no-auto-restart constraint, reduce the friction to a consent prompt:

- On "drifted" at TUI startup, instead of a log line, show the existing
  modal/prompt pattern (mirrors `CronInstallModal`): "Proxy config is stale
  (NEURALWATT_API_KEY changed). Regenerate and restart the proxy now?
  [restart / not now]".
- Accept → regenerate config + `lifecycle.stop()`/`start()` (the same thing
  `dn proxy upgrade` does, minus the binary re-download). Decline → the #292
  log line, unchanged.
- The restart is user-consented, so the machine-global constraint holds: the
  user, not a background path, chose the restart moment.
- Never prompt in headless/agent contexts — TUI mount only, same placement
  #292 chose.

### C. Truthful reporting

`install()`/`upgrade()` report what the written config actually contains:

- `config: neuralwatt (3 models)` on success.
- `config: NO upstream providers — no NEURALWATT_API_KEY in
  ~/.config/harness/.env` when keyless.
- When a regen removes a provider present in the previous on-disk config, name
  it: `removed: neuralwatt (key no longer present)`. The file is truth —
  removal is honored, never silent.

### D. Session-start model availability warning (the #290 warning half)

Wire `model_availability.resolve_or_warn` (currently dead code — defined,
tested, called by nothing) into seat resolution: when a persona's worker model
resolves, check it against the proxy's served ids (`/v1/models`, short
timeout).

- Served → nothing. Missing → one visible line **before the first turn**:
  `bob: configured model glm-5.2 not served by proxy (no NeuralWatt key in
  ~/.config/harness/.env) — running anyway`. Reason detail from
  `model_availability.reconcile`'s existing states.
- Fail-open: proxy unreachable / fetch error → silent skip. Best-effort only;
  must never add latency-fragility to session start.
- No substitution, no blocking. `resolve_or_warn`'s NEVER-substitute contract
  stands.

The picker path is already honest (reconciled availability states, PR #249);
this closes the `done.conf`/env-configured path that bypasses the picker.

## Non-goals

- No unattended proxy restart (per #292's constraint; B is consented).
- No cron-daemon-side drift handling: if config drifts and only cron jobs run,
  the gap remains until the next `dn` launch. Accepted; follow-up if it bites.
- Worker-path unknown-model **fallback** (the other half of #290): retry-abort
  on deterministic "unknown provider" errors. Separate change.
- OAuth tokens untouched (auth-dir stable across regens).
- Shell `VIBEPROXY_MODEL` overrides unchanged (C1 ladder feature).

## Testing

All hermetic — no live proxy, no network:

- **Empty-is-absent:** `_machine_global_env()` with empty exported key + real
  file key → file key wins; TUI overlay with `""` snapshot → treated as None;
  drift check in the poisoned-terminal scenario now reports "drifted", not "ok".
- **Masking notice:** file key + different non-empty shell key → install output
  contains the override note.
- **Consent prompt:** stubbed lifecycle — accept regenerates + restarts;
  decline logs the #292 line; headless context never prompts.
- **Truthful reporting:** keyless vs keyed generate → message content; provider
  removal names the provider.
- **Availability warning:** stubbed proxy id list — warns with reason when
  model missing; silent when present; silent (fail-open) when fetch raises.
- `resolve_or_warn` gains its first production call site; existing tests keep
  passing.

## Risks

1. **Consent-prompt fatigue** if drift is frequent (editable installs change
   the model list whenever main moves). Mitigation: prompt at most once per
   TUI session; decline falls back to the log line.
2. **Non-empty stale shell key still wins** (by #292's precedence). Mitigated
   by the masking notice (A) rather than a precedence change.
3. **Restart drops in-flight proxy requests** — now user-consented, so the
   user picks the moment.
