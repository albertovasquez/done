# `/reload` and `/clear` slash commands — design

**Date:** 2026-06-26
**Branch:** `reload-command`
**Status:** approved design (revised post-Codex adversarial review), ready for implementation plan

## Problem

`done`'s TUI launches the agent as a subprocess once at startup
(`harness/tui/app.py` `on_mount`: `spawn_agent_process` → `initialize` →
`new_session`). The agent reads everything from disk **once** at that subprocess
startup — `.env`, the model factory, the `Router`, and the skills catalog
(`harness/acp_main.py:78-97`). Nothing re-reads mid-session.

Two operations are missing:

- **`/reload`** — pick up edited `harness/` **code** without quitting and
  relaunching the terminal. Because Python cannot cleanly hot-reload already
  imported modules, the only way the agent runs edited code is a **fresh OS
  process**. So `/reload` must **kill and respawn** the subprocess.
- **`/clear`** — start a **fresh conversation** (empty transcript) on the
  **same** running subprocess. No restart, no disk re-read.

## Two state layers

The codebase has two independent layers; the commands map cleanly onto them:

| Command | Subprocess | Model selection | Session / transcript | UI scrollback | Disk re-read |
|---|---|---|---|---|---|
| **`/clear`** | kept alive | kept | new (empty) | wiped | no |
| **`/reload`** | killed + respawned | re-applied | new (empty) | wiped | yes (new process) |

Both end in the same clean conversation view; they differ only in whether the OS
process is replaced.

## Approach (chosen)

Factor `on_mount`'s load body into reusable lifecycle methods on `HarnessTui`, so
startup and `/reload` share **one** load path and cannot drift. `/clear` reuses
only the session-reset part.

This design was revised after an adversarial review (Codex) found that a naive
version has several lifecycle and re-entrancy bugs. The sections below incorporate
those fixes; the bug each one closes is cited inline.

## Design

### 1. Lifecycle methods (`harness/tui/app.py`)

Extract from the current `on_mount` (`app.py:141-160`):

- **`async def _connect(self)`** — open the subprocess context and a session:
  1. `self._cm = acp.spawn_agent_process(...)`; `self._conn, _ = await self._cm.__aenter__()`.
  2. `await self._conn.initialize(...)`.
  3. `await self._new_session()` (sets `self._session_id`).
  4. re-apply the preserved model (§4).
  5. `self._gen += 1` — bump the **session generation** (§5).
  - **Failure-atomic:** if anything *after* `__aenter__()` raises, call
    `await self._teardown()` before re-raising, so a half-open `_cm` is never
    left behind. (Codex Q5: connect-after-spawn failure leaked a half-open
    context.)

- **`async def _teardown(self)`** — close the subprocess context:
  ```python
  try:
      if self._cm is not None:
          await self._cm.__aexit__(None, None, None)   # closes conn, then wait→terminate→kill
  finally:
      self._cm = self._conn = self._session_id = None  # ALWAYS clear, even if __aexit__ raised
  ```
  (Codex Q5: state must clear in `finally`, not only on success.) Closing the
  `spawn_agent_process` context terminates the child (verified:
  `acp/stdio.py` → `transports.py` escalate wait→terminate→kill).

- **`async def _new_session(self)`** — `new = await self._conn.new_session(...)`;
  `self._session_id = new.session_id`. Used by both `_connect` and `/clear`.

- **`on_mount`** becomes: mount status → focus input → `await self._connect()`
  (wrapped in the existing try/`_fatal`). Behavior at startup is unchanged.

New `__init__` state:
- `self._gen: int = 0` — session generation (§5).
- `self._launch_worker_model_id = worker_model_id` — the launch-time model, the
  source of truth for "did the user switch?" (§4). (Codex Q2: there was no
  TUI-side launch default to compare against.)
- `self._busy: bool = False` — lifecycle guard (§6).

### 2. `_reset_conversation()` — clear the view WITHOUT leaving conversation

```python
async def _reset_conversation(self) -> None:
    if self._started:
        await self._transcript.remove_children()   # empty #transcript; keep it + #composer mounted
    self._streaming_md = None
    self._stream_buf = ""
    self._stream_closed = True
    self._tokens = 0
    self._refresh_status()
```

**Critical:** it does **not** flip `self._started` back to `False`. The first
prompt removes `#landing` entirely (`_enter_conversation` at `app.py:382-391`),
so returning to the landing state would make `_active_input()` query the
non-existent `#landing-input` (and `_fatal` the non-existent `#header-text`) and
crash. (Codex Q3: three crashes from `_started=False` reset.) It also clears the
streaming widget refs so no stale `_streaming_md` survives the reset. If a reset
is somehow requested before the first prompt (`not self._started`), it is a no-op
— there is nothing to clear and the landing view stays intact.

### 3. The two handlers (`harness/tui/commands.py` → thin; logic on the app)

**`/clear`** (`async def action_clear(self)`):
```python
if self._busy: return            # guard (§6)
self._busy = True
try:
    await self._reset_conversation()
    await self._new_session()    # same subprocess → fresh empty transcript
finally:
    self._busy = False
```

**`/reload`** (`async def action_reload(self)`):
```python
if self._busy: return            # guard (§6)
self._busy = True
try:
    self._cancel_inflight()      # §6: cancel prompt workers + resolve pending permission
    await self._reset_conversation()
    self._append_line(_c("muted", "— reloading agent… —"))
    await self._teardown()       # kill old subprocess
    try:
        await self._connect()    # respawn + initialize + new_session + re-apply model
        self._append_line(_c("muted", "— reloaded —"))
    except Exception as e:
        self._fatal(f"reload failed: {e}")   # app alive, input disabled; fix code, /reload again
finally:
    self._busy = False
```

Order is kill-then-respawn (accepts brief dead air; simpler than double-spawn).
On respawn failure the existing `_fatal` path disables input and shows the error.
Because `_reset_conversation` kept us in the conversation view, `_fatal` uses its
`_append_line` branch (safe), not the `#header-text` branch.

### 4. Model preservation across reload

After respawn, re-apply the runtime model **only when it differs from launch and
the agent supports the extension**:

```python
if (self._worker_model_id is not None
        and self._worker_model_id != self._launch_worker_model_id):
    try:
        await self._conn.ext_method("harness/set_model", {"model": self._worker_model_id})
    except Exception:
        pass   # agent without the harness extension (e.g. fake agent) → method-not-found; ignore
```

- Source of truth: `self._worker_model_id` (what `/models` set) vs
  `self._launch_worker_model_id` (construction-time). (Codex Q2.)
- Mock mode ignores the model server-side, so a no-op set is harmless.
- The guarded `try/except` keeps `/reload` working against agents that don't
  implement `harness/set_model` (the test fake agent). (Codex Q2 impl detail.)

### 5. Session-generation filter — drop stale updates and stale turns

`TuiClient.session_update` currently **discards** the `session_id`
(`client.py:26-27`), and `on_session_update` gates only on `_started`
(`app.py:506-508`). After a reload, a late notification from the **killed**
subprocess would render into the **new** conversation. Fix with a generation
counter:

- `self._gen` is bumped in `_connect` (§1).
- **`_send_prompt`** captures `gen = self._gen` and `session_id`/`conn` locally at
  send time; in its `finally`, it re-enables input **only if `gen == self._gen`**.
  (Codex Q4: an old prompt worker's `finally` re-enabled input after a reload
  failure, undoing `_fatal`. Codex Q6: a scheduled prompt ran against the swapped
  connection.)
- **Stale updates:** `TuiClient.session_update` stops dropping `session_id`; it is
  forwarded on the `SessionUpdate` message. `on_session_update` no-ops when the
  update's `session_id != self._session_id`. (Codex Q4: stale session updates
  could not be filtered.)

This is the one change that reaches outside `app.py` (into `client.py` and the
`SessionUpdate` message in `messages.py`) — small and additive.

### 6. Lifecycle guard + in-flight cancellation

- **`self._busy`** serializes the lifecycle handlers: `/reload`, `/clear`, and
  `/models` early-return while busy. (Codex Q4: no re-entrancy guard; Q6: `/models`
  during reload raced a transient-`None` conn.) Prompt-send also checks `_busy`.
- **`_cancel_inflight()`** (called at the top of `/reload`):
  - `self.workers.cancel_all()` — cancel any running `_send_prompt`/`_apply_model`
    worker (they are dispatched via `run_worker`, `app.py:221,321`).
  - if `self._pending_perm is not None and not self._pending_perm.done():`
    `self._pending_perm.set_result(None)` and dismiss the permission modal
    (`self.pop_screen()` if one is open), then `self._pending_perm = None`.
    (Codex Q1: a permission modal for the dead subprocess otherwise stays on
    screen, unanswerable.)

### 7. Registry (`harness/tui/commands.py`)

Two new entries in `build_registry()` (display order after `models`):
```python
Command("reload", "Restart the agent (reloads edited code)", _reload),
Command("clear", "Clear the conversation", _clear),
```
Handlers are thin and delegate: `_reload` → `app.action_reload()`,
`_clear` → `app.action_clear()`.

## Error handling summary

- **`/reload` respawn fails** → `_fatal` (input disabled, error shown, app alive);
  the conversation view is intact so `_fatal` uses its safe `_append_line` branch.
- **`_connect` fails after `__aenter__`** → `_teardown` runs first (no half-open
  `_cm`).
- **`_teardown`'s `__aexit__` raises** → state still cleared in `finally`.
- **Stale notifications / old prompt turn** → dropped by the generation/`session_id`
  filter (§5).
- **`/clear` on a dead connection** → guarded by `_busy`; if `_conn` is somehow
  None, `_new_session` raises and is surfaced; `/reload` is the recovery path.

## Testing (`tests/test_tui_pilot.py`, `tests/fake_agent.py`)

The TUI pilot harness drives the app with a fake agent; the fake agent records
`initialize`/`new_session` calls. Add:

- **`/clear`** issues a **new** `session_id` with **no** new `initialize`
  (subprocess not restarted); transcript children are emptied; `_started` stays
  `True`; streaming state reset.
- **`/reload`** triggers a **new `initialize`** (respawn) **and** a new
  `session_id`; model preserved across the respawn (if one was set, the fake agent
  records the `harness/set_model` re-apply, or the call is gracefully skipped when
  unsupported).
- **`/reload` with a failing spawn** → app stays alive, `_fatal` shown, input
  disabled, and a lingering old prompt worker's `finally` does **not** re-enable
  input (generation filter).
- **Stale update filter** → a `SessionUpdate` carrying the **old** `session_id`
  after a reload is ignored (not rendered into the new transcript).
- **`_teardown` idempotence** → calling it with `_cm is None` (already torn down)
  does not raise.
- **Re-entrancy guard** → a second `/reload` while one is in progress (`_busy`) is
  a no-op.

## Out of scope (follow-up)

- Reloading disk config (`.env`, skills catalog) *without* a process restart — not
  needed; `/reload` (full respawn) already picks these up, and a no-restart config
  reload is a separate feature.
- Replaying the prior transcript into the new session across `/reload` —
  deliberately rejected: a code reload should start from a clean slate to test the
  changed behavior.
- A confirmation prompt before `/reload` — the operation is cheap and the dev
  explicitly invoked it.
