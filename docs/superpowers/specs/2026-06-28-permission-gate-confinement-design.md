# Permission gate + path confinement for file tools (#102 / #106 / #107)

**Date:** 2026-06-28
**Branch:** `perm-gate-confinement`
**Closes:** #102, #106, #107
**Status:** design approved; Codex-reviewed (6 findings folded in)

## Problem

Three coupled, verified-true security holes at the agent's tool-dispatch layer:

- **#102 — file tools bypass the gate.** Bash routes through `env.execute()`,
  which holds the only permission gate (`harness/acp_env.py:51`). File tools
  (`read`/`write`/`edit`) dispatch straight to `tool.execute(args, env)` with no
  gate, no path check, no YOLO check (`harness/tracing_agent.py:233-242`).
- **#106 — no path confinement.** File tools resolve relative paths against
  `env.config.cwd` but never normalize `..`, never resolve symlinks, and accept
  absolute paths verbatim (`harness/tools/read.py:34`, `write.py:35`,
  `edit.py:36`). `write("/etc/passwd", …)` succeeds today.
- **#107 — gate fails OPEN.** When the connecting ACP client does not advertise
  the `elicitation` capability, the gate returns `True` (allow-all) and runs
  every command unprompted (`harness/acp_agent.py:615`). The bundled TUI is safe;
  `dn-agent` as a standalone server is not.

These are one work-stream: the same dispatch chokepoint, the same gate callback.

## Goals

1. Every filesystem-touching tool (bash + read/write/edit) passes through one
   permission decision before it runs.
2. File-tool paths are normalized (`..`, symlinks, absolute) and classified
   against a small set of allowed roots.
3. The gate fails **closed** for risky operations when there is no prompt channel.
4. Preserve existing behavior: YOLO still allows everything; the bash flow,
   the `plan` sentinel, and the byte-identical-wire / no-op-without-persona
   invariants are unchanged.

## Non-goals (explicit scope guards)

- **Bash path confinement.** Bash runs arbitrary shell (`acp_env.py:88`,
  `shell=True`). It is gated as a *whole command*, exactly as today — it is NOT
  path-confined. `cat /etc/passwd` and `echo x > /etc/y` still run after a single
  command-level permission decision. The spec claims confinement for **structured
  file tools only**. Parsing/sandboxing bash is a future issue.
- **Sensitive-subpath denylist.** In-cwd writes stay free; we do NOT deny
  `.git/hooks`, shell rc files, `~/.ssh`, etc. even when they fall inside an
  allowed root. Documented limitation + follow-up issue.
- **Race-proof (fd-based / `O_NOFOLLOW`) confinement.** We resolve + re-check the
  parent chain immediately before write, which closes the static vectors
  (`..`, pre-existing symlinks, absolute escapes) but leaves a narrow
  same-process symlink-swap race. Documented + follow-up issue.
- **Grant enforcement (#141).** `PermissionRequest` lays groundwork (it carries
  `is_write`/`is_exec`) but this PR does not wire `Grant` into the decision.
- **Read-before-write tracking.** Already deferred in `write.py`'s docstring.

## Architecture

One new leaf module plus one chokepoint.

### New: `harness/permcheck.py` (stdlib-only leaf, like `harness/textgate.py`)

No `harness.*` imports → no import cycle.

- `PermissionRequest` dataclass:
  `kind: Literal["bash","file"]`, `command: str | None`, `path: Path | None`,
  `is_write: bool`, `is_exec: bool`, `outside_roots: bool`.
- `classify_path(raw: str, roots: Sequence[Path]) -> tuple[Path, bool]`:
  1. `Path(raw).expanduser()`.
  2. If relative, anchor against the first root (cwd).
  3. `resolved = Path(os.path.realpath(p))` — collapses `..` and symlinks for
     existing components. For a non-existent leaf, realpath resolves the existing
     parent prefix and appends the rest literally.
  4. `outside_roots = not any(resolved == r or r in resolved.parents
     for r in (os.path.realpath(root) for root in roots))`.
  Returns `(resolved, outside_roots)`.
- `parent_escapes(resolved: Path, roots) -> bool`: re-realpath the parent
  directory; used by write/edit immediately before touching disk (TOCTOU
  re-check). Returns True if the parent resolves outside all roots.

### Allowed roots

`{realpath(cwd), realpath(workspace_dir)}` (workspace dropped when there is no
persona). This fixes the memory-write break Codex flagged: the default workspace
lives at `config_dir()/agents/default/` (`harness/paths.py:99`), **outside**
project cwd, and the memory protocol instructs the agent to write absolute
workspace paths (`harness/memory.py:193`). cwd-only would silently deny those.

`workspace_dir` is plumbed onto the env the same way `_active_persona` already is
(`acp_agent.py:675`).

## Data flow

```
tracing_agent.execute_actions(action)            # seam 3 — the ONE chokepoint
   classify the action:
     ├─ plan sentinel / internal tool            → NO gate, dispatch as today
     │    (create_job, load_skill, load_memory — not arbitrary-fs)
     ├─ bash    → PermissionRequest(kind="bash", command, is_exec=True)
     └─ file    → resolved,outside = classify_path(args.path, roots)
                  PermissionRequest(kind="file", path=resolved,
                                    is_write=(name in {"write","edit"}),
                                    outside_roots=outside)
   if request and not env.check_permission(request):
        return permission-denied dict, SKIP execute       # #102 closed for files
   dispatch: bash → env.execute(); file → tool.execute(args_with_resolved_path)
```

**Plan-sentinel ordering (Codex #1).** `acp_env.execute` intercepts `plan …`
before its gate (`acp_env.py:45`); moving the gate up would prompt/deny the
sentinel. Fix: the chokepoint recognizes `parse_plan_command()` and classifies it
as ungated, and `acp_env` keeps its existing pre-gate interception. Covered by the
existing assertion at `tests/test_acp_env.py:139`.

**Internal tools (Codex #5).** `create_job`, `load_skill`, `load_memory` are also
dispatched here (`registry.py:31`). They are not arbitrary-filesystem tools
(create_job has its own gate skill; load_* are workspace-confined with their own
traversal tests). They are explicitly classified **ungated** so the chokepoint
does not sweep them into the gate.

## The gate decision

There is exactly ONE decision function, `check_permission(req: PermissionRequest)
-> bool`, owned by `acp_agent.py` (it replaces the old `request_permission`
closure). Both callers wrap their action into a `PermissionRequest` and call it:
`AcpEnvironment.execute` wraps bash (`kind="bash"`); `tracing_agent` wraps file
tools (`kind="file"`). `AcpEnvironment` is constructed with `check_permission=`
instead of `request_permission=`; the ~3 env tests migrate from
`lambda cmd: …` to `lambda req: …`. One door, no second shape.

The decision logic:

```
if yolo:                              return True      # _auto_allow, unchanged
if req.kind == "file" and not req.is_write and not req.outside_roots:
                                      return True      # in-root read: free
if req.kind == "file" and req.is_write and not req.outside_roots:
                                      return True      # in-root write: free (see non-goals)
# risky: bash, OR out-of-root, OR write/exec needing confirmation
if no elicitation channel:            return False     # #107 fail-CLOSED (was True)
else:                                 prompt the user  # AllowedOutcome → bool
```

- `acp_env.execute` builds `PermissionRequest(kind="bash", command=command,
  is_exec=True)` and calls the same `check_permission`, so bash semantics are
  byte-identical: still prompts with `$ <command>` (`acp_agent.py:624`), still
  YOLO-overridable. The `on_command("rejected", …)` branch (`acp_env.py:52`) is
  unchanged.
- The fail-closed flip is scoped to *risky* ops: an in-root read with no channel
  still returns True, so a no-elicitation client is not bricked.

## TOCTOU handling (Codex #2)

- The gate approves a **resolved** path; the tool writes **that same resolved
  path** (path resolution moves into `classify_path`, called once; the tool
  receives the resolved path, not the raw arg). No divergence between approved
  and written path.
- `write`/`edit` call `permcheck.parent_escapes(resolved, roots)` immediately
  before `mkdir`/`write_text` and abort if the parent now resolves outside roots.
  This is the SAME root boundary the gate already enforced — it re-validates it at
  write time (catching a parent symlinked out-of-root after approval); it does not
  add a new prompt or a stricter rule than the gate applied.
- `write` stops using `mkdir(parents=True)` blindly across untrusted components:
  it validates the parent chain first.
- **Residual:** a symlink swapped between the parent re-check and the open is a
  same-process race we do not close here. Documented; follow-up issue for
  fd-based `O_NOFOLLOW` writes.

## Components & boundaries

| Unit | Responsibility | Depends on |
|---|---|---|
| `permcheck.py` | request shape, `classify_path`, `parent_escapes` | stdlib only |
| `tracing_agent.execute_actions` | classify action, gate, dispatch | permcheck, env |
| `acp_env.execute` | build bash request, call `check_permission` | permcheck |
| `acp_agent.check_permission` | the one decision (yolo / roots / fail-closed / prompt) | permcheck, client caps |
| `read`/`write`/`edit` | receive resolved path; write/edit re-check parent | permcheck |

`check_permission(req: PermissionRequest) -> bool` is the single decision
function (in `acp_agent`). `AcpEnvironment` is constructed with it and both it and
`tracing_agent.execute_actions` call it with a wrapped request.

## Error handling

- Denied → `{"output": "permission denied", "returncode": -1, "exception_info": ""}`
  — the shape file tools already return on failure, so the model reacts as it does
  to any failed tool.
- Unresolvable / malformed path → denied with a clear message, never a crash.

## Testing

`permcheck` unit tests:
- `..` escape, pre-existing symlink escape, absolute-outside, `~` expansion,
  in-root pass, exact-root-equals, non-existent leaf under valid parent,
  second root (workspace) accepted, non-existent-parent rejection.

Gate-decision tests:
- yolo → allow-all (file + bash).
- no elicitation: out-of-root write → DENY; in-root read → ALLOW.
- with elicitation: out-of-root write → prompt path invoked.
- bash request → same prompt/title behavior as before.

Dispatch / regression tests:
- `write` to `/etc/x` with no channel → permission-denied **and file not created**
  (the one test that proves #102+#106+#107 closed together).
- `plan "A:pending"` → not gated, not executed (existing assertion still passes).
- `create_job`/`load_memory` dispatch → ungated.
- memory write to workspace-dir-outside-cwd with no channel → ALLOWED (roots fix).

Full suite green: `.venv/bin/python -m pytest tests/ -q`.

## Follow-up issues to file

- Sensitive-subpath denylist (`.git/hooks`, shell rc, `~/.ssh`) for in-root writes.
- fd-based / `O_NOFOLLOW` race-proof write confinement.
- Bash path confinement (parse or sandbox) — currently out of scope by design.
