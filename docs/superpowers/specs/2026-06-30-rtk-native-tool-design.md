# RTK as a native tool of Done — design

**Date:** 2026-06-30
**Branch:** `rtk-native-tool`
**Status:** Spec (awaiting user review → writing-plans)

## Goal

Make [rtk](https://github.com/rtk-ai/rtk) — a single-binary CLI proxy that filters
dev-command output to cut LLM token consumption 60–90% — a **native, first-class
tool of Done**. The Done agent should be able to call `rtk("git status")` and get
back filtered output, with the binary shipped and managed by Done (no user
install step).

## Decisions locked (with Alberto, 2026-06-30)

1. **Integration depth: first-class `rtk` Tool.** Add an `RtkTool` to
   `build_registry()` that the agent invokes explicitly (like `read`/`write`/`edit`),
   NOT a transparent bash-rewrite seam. The model decides when to use it.
2. **Binary: Done-managed, version-pinned.** Done downloads a pinned `rtk` release
   (checksum-verified) on an explicit `dn rtk install`, mirroring the managed-dependency
   pattern locked for CLIProxyAPI (`cliproxyapi-integration-design` memory). rtk is a
   *passive per-command CLI*, so — unlike CLIProxyAPI — there is **no OS service, no
   login, no lifecycle**. This is a smaller instance of the same pattern.
3. **Install trigger: `dn rtk install/uninstall/status` + first-run opt-in.** Same
   shape as `dn cron …` (PR #163) and the planned `dn proxy …`.
4. **Config/telemetry: minimal.** At install, run `rtk telemetry disable` and use
   rtk's built-in default config. Done does **not** own/isolate rtk's config file.
   (Revised from an earlier "Done-owned config via `--config`" idea — see Finding F1.)
5. **Download helper: stand-alone now.** Write rtk's installer self-contained in
   `harness/rtk/manager.py`; factor a shared binary-installer out only when the
   CLIProxyAPI installer also lands (YAGNI).

## Load-bearing facts (verified against live code + rtk v0.42.0)

- **Tool surface** (`harness/tools/base.py`): a `Tool` is `name` + `schema` +
  `display_label(args)` + `execute(args, env) -> {"output", "returncode",
  "exception_info"}`. `RtkTool` implements this exactly like `ReadTool`.
- **Registry** (`harness/tools/registry.py`): `build_registry()` returns a FRESH list
  per agent. `load_memory` is appended only when `memory_mod.has_memory(root)` — the
  precedent for gating a tool on provisioning. `RtkTool` gates the same way.
- **Dispatch chokepoint** (`harness/tracing_agent.py:255–314`): `execute_actions`
  routes `bash` → `env.execute(action)` (line 270); every other tool →
  `_dispatch_tool` → `tool.execute(args, self.env)` (line 314). `RtkTool` plugs into
  the `_dispatch_tool` path.
- **Permission gate** (`harness/permcheck.py`): `decide_permission(PermissionRequest,
  yolo, has_elicitation)` is the single policy. In-root file ops are free; **bash /
  exec / out-of-root are risky → ask, else fail CLOSED → deny** (#107, #170).
  `kind="bash"` carries the subprocess-risk class.
- **`dn` subcommand interception** (`harness/tui_main.py:86`): `dn cron …` is caught
  before the TUI launches. `dn rtk …` uses the same interception point.
- **rtk CLI (v0.42.0)**: invocation is `rtk <subcommand> <args>` (e.g.
  `rtk git status`, `rtk npm …`, `rtk cargo …`, `rtk ls/tree/grep/diff/log/test/…`).
  `rtk run <cmd>` = raw passthrough; `rtk proxy <cmd>` = passthrough + tracking.
  `rtk telemetry` manages GDPR consent (default-off). `rtk gain` shows savings.
  `rtk config` shows/creates the config file. `rtk init --agent hermes` exists (the
  upstream Hermes hook adapter — NOT used by this design).

### Findings that revised the plan

- **F1 — no `--config` flag.** rtk v0.42.0 has no global `--config <path>`; it reads a
  fixed OS path (`~/Library/Application Support/rtk/config.toml` on macOS) and
  discovers it itself. So "Done-owned config via `--config`" is not possible as
  stated. Decision (#4 above): drop config ownership; just `rtk telemetry disable` at
  install and use rtk defaults.
- **F2 — telemetry is already opt-in/off by default** and GDPR-gated; `rtk telemetry
  disable` makes the off-state explicit/idempotent.

## Components

### Component 1 — `RtkTool` (`harness/tools/rtk.py`)

Schema (one required arg):

```jsonc
{ "name": "rtk",
  "parameters": { "properties": { "command": {
      "type": "string",
      "description": "A single dev command to run through rtk for token-optimized output, e.g. 'git status', 'npm ls', 'cargo check'." } },
    "required": ["command"] } }
```

`execute(args, env)`:
1. `binary = resolve_binary()` (Component 2). If `None` → return
   `{"output": "rtk is not installed; run `dn rtk install`. Falling back to bash.",
   "returncode": 1, "exception_info": None}` — the model falls back to `bash`, same
   reaction it has to a failed `read`.
2. Split `command` into `[subcommand, *rest]`; run `[binary, subcommand, *rest]` as a
   subprocess in `env.config.cwd`, telemetry-disabled environment.
3. Return the standard observation shape (`output`, `returncode`, `exception_info`),
   identical to `ReadTool`, so the existing formatter/TUI render it uniformly.

`display_label(args)` → `f"rtk {args.get('command','')}"`.

**Registry gating** (`build_registry`): append `RtkTool()` only when BOTH
`resolve_binary() is not None` AND `done.conf [rtk] enabled` is true. Otherwise the
tool is omitted — a byte-identical no-op for un-provisioned Done (the `load_memory`
on `has_memory()` precedent).

### Component 2 — Binary resolution & `dn rtk …` (`harness/rtk/manager.py`)

Thin seam module (parallels the planned `harness/proxy.py`):

- **`resolve_binary() -> Path | None`** — single source of truth. Precedence:
  1. Done-pinned binary `~/.harness/rtk/bin/rtk` (or platform data dir via `harness/paths.py`).
  2. `done.conf [rtk] path` (explicit override).
  3. `shutil.which("rtk")` (honors an already-installed rtk, like Alberto's env).
  Returns `None` if none resolve → gates the tool off.
- **`dn rtk install`** — resolve host OS/arch → map to the matching GitHub release
  asset → download → **verify checksum** → place in `~/.harness/rtk/bin/` (chmod +x)
  → pin version in `done.conf [rtk] version` → run `rtk telemetry disable` → set
  `[rtk] enabled = true`.
- **`dn rtk uninstall`** — remove the pinned binary; set `[rtk] enabled = false`.
- **`dn rtk status`** — print resolved path, version, enabled flag, and `rtk gain`
  savings summary.

`dn rtk …` is intercepted in `harness/tui_main.py` before TUI launch (same site as
`dn cron …`, `tui_main.py:86`).

**Per-OS/arch asset resolution** is the fiddliest part; keep it isolated in one
function so it can later be lifted into a shared installer alongside CLIProxyAPI.

### Component 3 — First-run opt-in

On the first TUI run where rtk is unprovisioned (`[rtk]` unset), show a one-time
prompt: *"Enable rtk for 60–90% token savings on dev commands? Done will download a
pinned binary."* Accept → run `dn rtk install` + `[rtk] enabled = true`. Decline →
`[rtk] enabled = false`; never re-prompt. Same first-run-opt-in shape as cron (#163)
and the planned proxy.

### Component 4 — Telemetry (minimal)

`dn rtk install` runs `rtk telemetry disable` (idempotent; default is already off).
No Done-owned config file, no XDG isolation. rtk uses its built-in defaults.

### Component 5 — Permission gate

`RtkTool` runs subprocesses, so it carries the **same risk class as bash**, not the
free in-root file class. Its dispatch MUST flow through the existing chokepoint as a
`PermissionRequest(kind="bash", command=...)` so YOLO / ask / deny policy
(`decide_permission`) applies uniformly. **Without this, rtk would be an ungated
subprocess path — the bypass family of #102/#170.** This is the one
must-not-miss correctness point.

Implementation note: today bash is gated inside the `env.execute` branch and file
tools are gated around `_dispatch_tool`. The gate for `RtkTool` must be wired so a
bash-class `PermissionRequest` is built for it — confirm during implementation
whether `_dispatch_tool` already has a hook for per-tool risk class, or whether
`RtkTool` needs to declare its risk class to the dispatcher.

## Data flow

```
agent emits tool call  rtk(command="git status")
        │
  tracing_agent.execute_actions  (name != "bash")
        │
  _dispatch_tool("rtk", RtkTool, {command})
        │
  decide_permission(PermissionRequest(kind="bash", command="rtk git status"), …)
        │  allow
  RtkTool.execute → subprocess [binary, "git", "status"] in cwd, telemetry off
        │
  {"output": <filtered>, "returncode": 0, "exception_info": None}
        │
  formatter / TUI  (uniform with read/write/edit)
```

## Error handling

- Binary unresolved → `returncode=1` + "run `dn rtk install`" message; model uses bash.
- Permission denied (headless, no elicitation) → fail CLOSED → deny (same as bash).
- rtk subprocess nonzero exit → pass through `returncode` + combined stdout/stderr so
  the model reacts as it would to a failed bash command.
- Download/checksum failure in `dn rtk install` → abort, leave no partial binary,
  print actionable error; `[rtk] enabled` stays false.

## Testing

- `RtkTool.execute`: resolved → runs & shapes output; unresolved → `returncode=1`
  fallback message; nonzero rtk exit → passthrough returncode.
- `build_registry` gating: present+enabled → tool added; absent OR disabled → omitted
  (no-op parity, like `load_memory`).
- Permission: rtk dispatch builds a bash-class `PermissionRequest`; headless-no-
  elicitation → deny.
- `resolve_binary` precedence order (pinned > config path > PATH).
- `dn rtk install`: checksum mismatch aborts cleanly; OS/arch asset mapping covers the
  supported targets; telemetry-disable invoked.

## Open questions (recorded; pin during eval)

1. **bash-vs-rtk prompt framing** — "rtk for known read-only inspections" vs
   "rtk-first, bash as escape hatch." Deferred by Alberto; pin with real turns during
   eval. rtk passes unknown commands through anyway, so this is a soft prompt hint, not
   a correctness constraint.
2. **Dispatcher risk-class hook** — confirm exactly how `_dispatch_tool` should learn
   that `rtk` is bash-class (existing per-tool hook vs. a new `risk_kind` attribute on
   the Tool). Resolve in the plan's first task.
3. **Supported OS/arch targets** — enumerate the GitHub-release assets to map (at
   least macOS arm64/x64, linux x64/arm64) when writing the installer.

## Out of scope

- Transparent bash→rtk rewrite seam (rejected in favor of the explicit tool).
- Done-owned rtk config / filter tuning (deferred; rtk defaults are fine).
- Shared binary-installer with CLIProxyAPI (factor out later).
- rtk's `init --agent hermes` upstream hook (Done wires the tool itself).

## References

- `cliproxyapi-integration-design` memory — the managed-dependency precedent.
- PR #163 (cron OS-service + first-run opt-in), #170/#171 (permission gate),
  #143/#154 (context compressor), PR #173 (subagents / `build_registry`).
- rtk: https://github.com/rtk-ai/rtk (binary v0.42.0 verified locally).
