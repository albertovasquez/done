# `done.conf` model persistence — design

**Date:** 2026-06-26
**Branch:** `worktree-done-conf-model-persistence`
**Status:** approved design, ready for implementation plan

## Problem

The harness picks its model from two transient sources that are lost on every
logout/exit:

- the launch-time backend flag `--model {mock|vibeproxy}`
  (`harness/tui_main.py:53`, default `"vibeproxy"`), and
- the model string from `VIBEPROXY_MODEL` env (default `"gpt-5.4"`,
  resolved in `harness/acp_main.py:46-48`).

There is also a **runtime** hot-swap — `harness/set_model`
(`harness/acp_agent.py:48-58`) updates `self._worker_model_id` for subsequent
turns — but that change is in-memory only. When the user quits and relaunches,
the harness reverts to the hardcoded defaults; the model they were last using is
forgotten.

We want the **current model to persist across sessions**, and we want the file
shape to be ready for a **future** feature: multiple named agents (e.g. a
reserved `default` agent plus uuid-keyed agents like `bill`), each with its own
model. This task delivers persistence for the **default agent only**; the file
schema supports the future shape but no named-agent reading/selection is wired.

## Scope

**In scope (this task):**

- Define the `done.conf` TOML schema (reserved `default` key + future
  uuid-keyed agents; `backend` + `model` per agent).
- A small `harness/config.py` module: load (stdlib `tomllib`), save the default
  entry (hand-rolled atomic writer that preserves other agents' tables),
  corrupt-file tolerant.
- Wire **only** the default agent:
  - **Startup:** load `[agents.default]` and apply it (unless `--model` is
    passed explicitly).
  - **Runtime:** when `harness/set_model` changes the model, persist it back to
    `[agents.default]`.

**Out of scope (future task):**

- Reading/selecting named (uuid-keyed) agents.
- Per-agent backend switching at runtime, an agent-management UI/commands,
  agent creation/deletion.

The file shape is forward-compatible so the future task layers on without a
migration.

## File format & location

A TOML file named **`done.conf`** at **`paths.config_dir() / "done.conf"`** —
i.e. `$XDG_CONFIG_HOME/harness/done.conf` or `~/.config/harness/done.conf`,
alongside the existing `.env` and `skills/` (`harness/paths.py:16-21`).

TOML chosen because stdlib `tomllib` reads it with zero new deps, it gives clean
`[agents.<key>]` tables, and it sits naturally next to the existing XDG config.

```toml
# done.conf — persisted agent → model selection.
# Managed by the harness; safe to hand-edit.

schema_version = 1

[agents.default]
backend = "vibeproxy"   # "mock" | "vibeproxy"
model   = "gpt-5.4"     # the model string (VIBEPROXY_MODEL value)

# Future named agents (NOT read/selected yet — shape is reserved):
# [agents.6f1c2b8e-...-uuid]
# name    = "bill"
# backend = "vibeproxy"
# model   = "claude-opus-4-8"
```

Schema notes:

- `schema_version = 1` — lets a future task migrate deliberately rather than
  guess.
- Reserved key is **`default`** (the always-present primary agent). It carries
  no `name`. Future agents are keyed by **uuid** and carry a human `name`.
- Each agent table has `backend` (mirrors the `--model` flag values) and
  `model` (mirrors `VIBEPROXY_MODEL`).
- Only the `default` table is read/written this task; other tables are
  **preserved verbatim** on write.

## Component: `harness/config.py`

A small, pure module — sibling to `paths.py`. It knows nothing about the TUI or
ACP; it only reads and writes `done.conf`.

```python
SCHEMA_VERSION = 1
RESERVED_KEY = "default"

@dataclass(frozen=True)
class AgentConfig:
    backend: str            # "mock" | "vibeproxy"
    model: str              # model string, e.g. "gpt-5.4"
    name: str | None = None # None for the reserved default; set for uuid agents

def conf_path() -> Path: ...                    # paths.config_dir() / "done.conf"
def load() -> dict[str, AgentConfig]: ...       # {} if missing/empty/malformed
def load_default() -> AgentConfig | None: ...   # the [agents.default] entry, or None
def save_default(cfg: AgentConfig) -> None: ... # upsert [agents.default], preserve rest
```

Behaviors:

- **Read** — `load()` uses stdlib `tomllib`. A missing file, empty file, or
  TOML/parse error returns `{}`; `load_default()` returns `None`. Malformed
  individual agent tables (missing `backend`/`model`) are skipped, not fatal.
  The harness must always boot even with a corrupt config — persistence is
  best-effort, never raises into the boot path.
- **Write** — TOML has no stdlib writer. `save_default` does
  **load-raw → mutate the `default` entry → serialize** with a tiny hand-rolled
  writer scoped to this flat schema (top-level `schema_version` +
  `[agents.<key>]` tables of string scalars). It **preserves all other agents'
  tables** so a future `bill` entry survives a default write. Values are
  defensively quote-escaped (basic TOML string escaping for `"` and `\`).
  Write is **atomic**: temp file in the config dir + `os.replace`; the config
  dir is `mkdir -p`'d first.
- **No new dependency** — stdlib read + hand-rolled write keeps the module
  self-contained. The schema is trivially flat, so a `tomli-w`-class dependency
  is unjustified.

### Why hand-rolled over `tomli-w`

The schema is flat tables of string key/values with one top-level int. The
values are constrained (backend is an enum, model/name are simple identifiers),
so escaping edge cases are bounded and handled defensively. Adding a write-only
TOML dependency for this is overkill and breaks the "read with stdlib, no new
deps" cleanliness.

## Integration (default agent only)

The TUI and the ACP agent are **separate processes** (`harness/tui_main.py:48-49`
spawns `python -m harness.acp_main --model ... --cwd ...`). The runtime model
change happens inside the **ACP** process. The two halves of integration map
onto the two processes.

### Startup — load & apply (TUI process)

Precedence, highest first:

1. **Explicit `--model <backend>` flag** (and `VIBEPROXY_MODEL` env) — if the
   user passes it, it wins for this session.
2. **`done.conf` `[agents.default]`** — the persisted choice.
3. **Hardcoded defaults** — current behavior (`vibeproxy` / `gpt-5.4`).

Concretely, in `harness/tui_main.py`, after `paths.load_env(cwd)`
(`harness/tui_main.py:44`): if `--model` was **not** given explicitly, call
`config.load_default()`. If it returns an `AgentConfig`:

- use its `backend` as the agent command's `--model` value, and
- export its `model` as `VIBEPROXY_MODEL` in the spawned subprocess's
  environment (via `override=False`-style precedence — process env still wins if
  already set), so the **existing** env-resolution path in `acp_main.py:46-48`
  picks it up unchanged.

This keeps the ACP side's startup untouched: it still just reads the flag + env.

> **Detecting "explicit `--model`":** argparse cannot distinguish a user-typed
> default from the fallback default. The plan must make this detectable — e.g.
> set the argparse default to `None` and substitute the hardcoded default only
> after the config lookup, OR track whether the flag was seen. The plan picks
> one; the requirement is: a user who types `--model vibeproxy` is honored as
> explicit and suppresses the config load.

### Runtime — persist on change (ACP process)

The save point is the **`harness/set_model` handler** in
`harness/acp_agent.py:48-58`. When it updates `self._worker_model_id`, it also
calls `config.save_default(AgentConfig(backend=<launch backend>, model=<new
model>))`.

- The ACP process knows its **launch backend** (passed via `--model` at spawn)
  and the **new model string** (the `set_model` param), so it can pair them.
- `set_model` today carries only `model` (the string), not `backend`. The
  default agent's backend is fixed at launch for this scope, so pairing the
  launch backend with the new model string is correct.
- Persistence is **best-effort**: a failed write logs/ignores and never breaks
  the hot-swap (`set_model` still returns `{"ok": True, ...}`).

### Precedence vs. persistence (decided)

- **Explicit `--model` overrides startup *load* only.** A user who launches with
  an explicit backend is not auto-overridden by `done.conf` for that session.
- **A runtime `set_model` *always* persists**, even when launched with an
  explicit `--model`. The user actively changed the model; that intent should
  survive logout. Precedence governs startup; runtime change always writes.

## Data flow

```
launch (TUI)
  └─ paths.load_env(cwd)
  └─ --model explicit? ──yes──> use flag + VIBEPROXY_MODEL env (config load skipped)
                        ──no───> config.load_default()
                                   └─ Some(cfg) → spawn --model=cfg.backend,
                                                   env VIBEPROXY_MODEL=cfg.model
                                   └─ None      → hardcoded defaults
  └─ spawn ACP subprocess (carries the resolved backend)

runtime (ACP)  harness/set_model{model}
  └─ self._worker_model_id = model           (existing behavior)
  └─ config.save_default(AgentConfig(
        backend=<launch backend>, model=model))   (NEW, best-effort)
  └─ return {"ok": True, "model": model}
```

## Error handling

- **Corrupt / unreadable `done.conf`** → `load()` returns `{}`; startup falls
  through to hardcoded defaults. Never fatal.
- **Partial agent table** (missing `backend`/`model`) → that agent skipped;
  others still load.
- **Unwritable config dir / disk error on save** → caught, logged at debug,
  ignored; the runtime model swap still succeeds. The persisted value simply
  doesn't update this time.
- **Concurrent writers** — out of scope (single TUI+ACP pair per user). Atomic
  `os.replace` makes a torn file impossible even so.

## Testing

Unit (`harness/config.py`), no process spawning:

- `conf_path()` resolves under `config_dir()` (respects `XDG_CONFIG_HOME` via
  monkeypatched env / `tmp_path`).
- `load()` on: missing file → `{}`; empty file → `{}`; malformed TOML → `{}`;
  valid file → parsed `AgentConfig`s.
- `load_default()` → the default entry, or `None` when absent.
- `save_default()` round-trips: write then `load_default()` returns it.
- `save_default()` **preserves** a pre-existing uuid agent table (write default,
  assert the other agent and its `name` survive).
- `save_default()` is atomic (no partial file) and creates the config dir.
- Value escaping: a model string containing `"` / `\` round-trips.
- Schema: `schema_version` is written and preserved.

Integration (kept light, matching existing patterns):

- Startup precedence: explicit `--model` suppresses config load; absent flag +
  present config applies `backend` + `VIBEPROXY_MODEL`; absent flag + absent
  config uses hardcoded defaults.
- `set_model` handler writes `[agents.default]` with the launch backend + new
  model, and still returns `{"ok": True}` when the write fails (monkeypatch
  `save_default` to raise).

## Files touched

- **new** `harness/config.py` — the load/save module.
- **new** `tests/test_config.py` — unit tests above.
- **edit** `harness/tui_main.py` — startup load + precedence (~startup block
  near line 44–53).
- **edit** `harness/acp_agent.py` — `set_model` handler persists
  (`acp_agent.py:48-58`).
- **edit** existing TUI/ACP integration tests as needed for the precedence cases.
