# Hooks — internal lifecycle events

Done has a small **internal hook system**: built-in code can run at lifecycle
moments (session start / end). It is Python-only and lives in the TUI process.

> **Internal only (for now).** There is no user-configurable shell-hook layer
> yet — you cannot declare `run ./script.sh on session_end` in config. That is
> planned as a follow-on (the event names and payloads below are the contract
> that layer will build on). Today, hooks are registered in Python by the
> harness itself.

## Events

| Event | Fired | Payload (keyword args) |
| --- | --- | --- |
| `session_start` | TUI mount, after the agent connects | `tracer`, `cwd: str`, `persona_id: str \| None` |
| `session_end` | TUI unmount, before the trace file is closed | `tracer`, `cwd: str`, `persona_id: str \| None` |

`session_end` fires before the tracer closes so handlers can record
breadcrumbs. `persona_id` may be `None`; consumers that touch files should not
require it (walk all persona workspaces instead).

## Adding a consumer

```python
from harness import hooks

def on_session_end(*, tracer=None, cwd=None, persona_id=None, **_):
    ...  # do your thing

hooks.register("session_end", on_session_end, label="my.consumer")
```

Rules a handler MUST honor:

- **Never block.** `session_end` runs during app teardown. Do slow work in a
  detached subprocess (see `auto_regen` below), not inline.
- **Never assume it won't be skipped.** A raising handler is caught, logged via
  `tracer.emit("dn", "hook.error", …)`, and skipped — it never breaks the
  session or other handlers. `dispatch` itself never raises.
- **Accept `**_`.** Take the payload as keyword args plus `**_` so new payload
  fields never break you.
- **Self-register at import.** Put `hooks.register(...)` at module top level and
  import the module once at TUI startup (see `harness/tui/app.py`), so
  registration is deterministic and one-time.

## Worked example: `auto_regen`

`harness/compress/auto_regen.py` keeps compressed siblings fresh. On
`session_end` it finds stale **existing** siblings (it never creates new ones)
via `harness/compress/targets.py`, and if any are stale it spawns a detached
worker (`python -m harness.compress.regen_worker <paths…>`) to rebuild exactly
those. Quitting the TUI is never blocked; a failed regen just leaves the sibling
stale (it heals next session). It is gated on the existing per-persona
`compress_aware` flag — no separate setting.

See `docs/compress-aware.md` for the compression feature itself.
