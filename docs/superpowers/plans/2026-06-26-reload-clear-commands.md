# `/reload` and `/clear` Slash Commands Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `/reload` (kill + respawn the agent subprocess to pick up edited `harness/` code, preserving the model selection) and `/clear` (reset the session on the live subprocess), both wiping scrollback while staying in the conversation view.

**Architecture:** Factor `on_mount`'s load body into reusable `_connect`/`_teardown`/`_new_session` lifecycle methods on `HarnessTui`, shared by startup and `/reload`. A monotonic session-generation counter (`self._gen`), bumped on each `_connect`, lets late notifications and stale prompt-turns from a killed subprocess be dropped. A `self._busy` guard serializes the lifecycle handlers. `/clear` reuses only the session-reset path.

**Tech Stack:** Python 3.11, Textual (TUI), the `acp` SDK (`spawn_agent_process` context manager), pytest + Textual's `run_test()` pilot harness.

## Global Constraints

- **The agent's `session_id` is NOT a reliable freshness key.** The real agent issues a fresh uuid per `new_session`, but the **test fake agent returns the constant `"fake-session"`** (`tests/fake_agent.py:38`). The freshness filter therefore keys on the **generation counter `self._gen`**, not `session_id`. (Stale-update filtering may *additionally* compare `session_id` as defense-in-depth, but `_gen` is the load-bearing mechanism.)
- **The fake agent does NOT implement `harness/set_model`** (`tests/fake_agent.py` has no `ext_method`). Model re-apply MUST be wrapped so a method-not-found is swallowed — `/reload` must work against it.
- **Never flip `self._started` back to `False` on reset.** The first prompt removes `#landing` entirely (`app.py:382-391`); returning to landing state makes `_active_input()`/`_fatal` query the removed `#landing-input`/`#header-text` and crash.
- **Test command (from the worktree root):** `.venv/bin/python -m pytest tests/test_tui_pilot.py -q` (the worktree has no `.venv`; if absent use `../../.venv/bin/python`). Test files add `upstream/src` and `.` to `sys.path`.
- **Commit trailer:** end every commit message with `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

**Spec:** `docs/superpowers/specs/2026-06-26-reload-clear-commands-design.md`

---

## File Structure

- `harness/tui/app.py` — lifecycle methods (`_connect`/`_teardown`/`_new_session`), `_reset_conversation`, `_cancel_inflight`, `action_reload`/`action_clear`, `_gen`/`_busy`/`_launch_worker_model_id` state, `on_session_update` + `_send_prompt` generation gating. (All the behavior.)
- `harness/tui/client.py` — `TuiClient.session_update` forwards `session_id` (stops dropping it).
- `harness/tui/messages.py` — `SessionUpdate` carries `session_id`.
- `harness/tui/commands.py` — two new `Command` registry entries + thin handlers.
- `tests/fake_agent.py` — record process starts (a startup marker) so a respawn is observable in tests.
- `tests/test_tui_pilot.py` — pilot tests for `/clear`, `/reload`, failure, generation filter, guard.

Order: plumb `session_id` through messages/client first (Task 1), add generation state + lifecycle refactor (Task 2), reset helper (Task 3), the two handlers (Tasks 4–5), generation gating in send/update (Task 6), registry (Task 7), respawn + failure + race tests (Task 8).

---

## Task 1: Forward `session_id` through `SessionUpdate`

**Files:**
- Modify: `harness/tui/messages.py`
- Modify: `harness/tui/client.py:26-27`
- Test: `tests/test_tui_pilot.py`

**Interfaces:**
- Produces: `SessionUpdate(update, session_id=None)` with `.session_id` attribute; `TuiClient.session_update` passes the real `session_id`.

- [ ] **Step 1: Write the failing test** — append to `tests/test_tui_pilot.py`:

```python
def test_session_update_message_carries_session_id():
    from harness.tui.messages import SessionUpdate as SU
    msg = SU("the-update", session_id="sess-7")
    assert msg.update == "the-update"
    assert msg.session_id == "sess-7"

def test_session_update_session_id_defaults_to_none():
    from harness.tui.messages import SessionUpdate as SU
    assert SU("u").session_id is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tui_pilot.py::test_session_update_message_carries_session_id -q`
Expected: FAIL — `SessionUpdate.__init__()` got an unexpected keyword argument `session_id`.

- [ ] **Step 3: Implement** — `harness/tui/messages.py`, replace `SessionUpdate`:

```python
class SessionUpdate(Message):
    """An ACP session/update notification, marshalled to the app for rendering.
    Carries the originating session_id so the app can drop updates from a stale
    (reloaded-away) session."""
    def __init__(self, update: Any, session_id: str | None = None) -> None:
        super().__init__()
        self.update = update
        self.session_id = session_id
```

Then `harness/tui/client.py:26-27`:

```python
    async def session_update(self, session_id: str, update: Any, **kw: Any) -> None:
        self._app.post_message(SessionUpdate(update, session_id=session_id))
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tui_pilot.py -q`
Expected: PASS (the new 2 + all existing — the existing tests construct `SessionUpdate(update_agent_message_text(...))` positionally, which still works since `session_id` defaults to None).

- [ ] **Step 5: Commit**

```bash
git add harness/tui/messages.py harness/tui/client.py tests/test_tui_pilot.py
git commit -m "feat(tui): SessionUpdate carries session_id for stale-update filtering"
```

---

## Task 2: Lifecycle methods + generation/guard state; refactor `on_mount`

**Files:**
- Modify: `harness/tui/app.py` (`__init__` ~69-90; `on_mount` ~141-160)
- Test: `tests/test_tui_pilot.py`

**Interfaces:**
- Produces: `self._gen: int`, `self._busy: bool`, `self._launch_worker_model_id`; `async _connect(self)`, `async _teardown(self)`, `async _new_session(self)`. `_connect` bumps `_gen` and is failure-atomic; `_teardown` clears `_cm/_conn/_session_id` in `finally`.

- [ ] **Step 1: Write the failing test** — append:

```python
def test_teardown_then_connect_bumps_generation_and_reconnects():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()                 # on_mount → _connect ran once
            assert app._gen == 1, f"gen should be 1 after startup, got {app._gen}"
            assert app._conn is not None and app._session_id is not None
            await app._teardown()
            assert app._cm is None and app._conn is None and app._session_id is None
            await app._connect()
            assert app._gen == 2, f"gen should bump on reconnect, got {app._gen}"
            assert app._conn is not None and app._session_id is not None
    asyncio.run(go())

def test_teardown_is_idempotent_when_already_torn_down():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await app._teardown()
            await app._teardown()               # second call must not raise
            assert app._conn is None
    asyncio.run(go())
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tui_pilot.py::test_teardown_then_connect_bumps_generation_and_reconnects -q`
Expected: FAIL — `AttributeError: 'HarnessTui' object has no attribute '_gen'` (and no `_teardown`).

- [ ] **Step 3: Implement.**

In `__init__` (after `self._session_id = None`, ~line 80), add:

```python
        self._gen = 0                         # session generation; bumped each _connect
        self._busy = False                    # lifecycle guard (reload/clear/model)
        self._launch_worker_model_id = worker_model_id  # source of truth for "user switched model?"
```

Add the three lifecycle methods (place them just above `on_mount`):

```python
    async def _new_session(self) -> None:
        new = await self._conn.new_session(cwd=self.cwd, mcp_servers=[])
        self._session_id = new.session_id

    async def _connect(self) -> None:
        """Spawn the agent subprocess, initialize, open a session, re-apply the
        preserved model, and bump the generation. Failure-atomic: if anything
        after __aenter__ raises, tear the half-open context down before re-raising."""
        self._cm = acp.spawn_agent_process(
            self._client, self.agent_cmd[0], *self.agent_cmd[1:],
            env=dict(os.environ), cwd=self.cwd,
        )
        self._conn, _proc = await self._cm.__aenter__()
        try:
            await self._conn.initialize(
                protocol_version=acp.PROTOCOL_VERSION,
                client_capabilities=ClientCapabilities(elicitation=ElicitationCapabilities()),
            )
            await self._new_session()
            await self._reapply_model()
        except Exception:
            await self._teardown()            # never leave a half-open _cm
            raise
        self._gen += 1

    async def _teardown(self) -> None:
        """Close the subprocess context (terminates the child). Clears connection
        state in finally so a raising __aexit__ can't leave a stale _conn."""
        try:
            if self._cm is not None:
                await self._cm.__aexit__(None, None, None)
        finally:
            self._cm = self._conn = self._session_id = None

    async def _reapply_model(self) -> None:
        """After a respawn, re-apply a runtime-selected model. No-op if unchanged
        from launch; swallow method-not-found for agents without the extension."""
        if (self._worker_model_id is not None
                and self._worker_model_id != self._launch_worker_model_id):
            try:
                await self._conn.ext_method("harness/set_model", {"model": self._worker_model_id})
            except Exception:
                pass
```

Replace the body of `on_mount` (`app.py:146-160`, the try/except that inlines spawn/init/session) with a call to `_connect`:

```python
    async def on_mount(self) -> None:
        await self._mount_status_contents()
        self.query_one("#landing-input", Input).focus()
        try:
            await self._connect()
        except Exception as e:
            self._fatal(f"could not start agent: {e}")
```

(The `import os`, `acp`, `ClientCapabilities`, `ElicitationCapabilities` are already imported at the top of `app.py` — verify; they are used by the current `on_mount`.)

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tui_pilot.py -q`
Expected: PASS (new 2 + all existing — startup behavior is unchanged; existing boot tests still pass).

- [ ] **Step 5: Commit**

```bash
git add harness/tui/app.py tests/test_tui_pilot.py
git commit -m "refactor(tui): _connect/_teardown/_new_session lifecycle + generation/guard state"
```

---

## Task 3: `_reset_conversation` — clear view, stay in conversation

**Files:**
- Modify: `harness/tui/app.py`
- Test: `tests/test_tui_pilot.py`

**Interfaces:**
- Consumes: `_enter_conversation`, `#transcript` (from Task 2's app).
- Produces: `async _reset_conversation(self)` — empties `#transcript`, resets streaming + token state, never flips `_started`.

- [ ] **Step 1: Write the failing test** — append:

```python
def test_reset_conversation_empties_transcript_keeps_started():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await _send_first_prompt(pilot, app, "hello")
            for _ in range(50):
                await pilot.pause()
                if "done" in _transcript_text(app):
                    break
            assert _transcript_text(app).strip(), "precondition: transcript has content"
            await app._reset_conversation()
            await pilot.pause()
            assert _transcript_text(app) == "", "transcript should be emptied"
            assert app._started is True, "must stay in conversation view, not return to landing"
            assert app.query("#transcript"), "#transcript widget must remain mounted"
            assert app._streaming_md is None and app._stream_buf == ""
            assert app._tokens == 0
    asyncio.run(go())
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tui_pilot.py::test_reset_conversation_empties_transcript_keeps_started -q`
Expected: FAIL — `AttributeError: 'HarnessTui' object has no attribute '_reset_conversation'`.

- [ ] **Step 3: Implement** — add to `app.py` (near `_enter_conversation`):

```python
    async def _reset_conversation(self) -> None:
        """Empty the transcript and reset per-conversation state WITHOUT leaving
        the conversation view (flipping _started=False would query the removed
        #landing-input/#header-text and crash). No-op before the first prompt."""
        if self._started:
            await self._transcript.remove_children()
        self._streaming_md = None
        self._stream_buf = ""
        self._stream_closed = True
        self._tokens = 0
        self._refresh_status()
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tui_pilot.py::test_reset_conversation_empties_transcript_keeps_started -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/tui/app.py tests/test_tui_pilot.py
git commit -m "feat(tui): _reset_conversation empties transcript, stays in conversation"
```

---

## Task 4: `/clear` handler

**Files:**
- Modify: `harness/tui/app.py`
- Test: `tests/test_tui_pilot.py`

**Interfaces:**
- Consumes: `_reset_conversation` (T3), `_new_session` (T2), `_busy` (T2).
- Produces: `async action_clear(self)` — reset view + new session on the live subprocess; no respawn; guarded by `_busy`.

- [ ] **Step 1: Write the failing test** — append:

```python
def test_clear_resets_session_without_respawn():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await _send_first_prompt(pilot, app, "hello")
            for _ in range(50):
                await pilot.pause()
                if "done" in _transcript_text(app):
                    break
            gen_before = app._gen
            await app.action_clear()
            await pilot.pause()
            assert _transcript_text(app) == "", "clear should empty the transcript"
            assert app._gen == gen_before, "clear must NOT respawn (generation unchanged)"
            assert app._conn is not None, "subprocess/connection stays alive"
            assert app._session_id is not None, "a fresh session exists"
            assert app._busy is False, "busy flag released"
    asyncio.run(go())
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tui_pilot.py::test_clear_resets_session_without_respawn -q`
Expected: FAIL — `AttributeError: ... no attribute 'action_clear'`.

- [ ] **Step 3: Implement** — add to `app.py`:

```python
    async def action_clear(self) -> None:
        if self._busy:
            return
        self._busy = True
        try:
            await self._reset_conversation()
            await self._new_session()         # same subprocess → fresh empty transcript
        finally:
            self._busy = False
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tui_pilot.py::test_clear_resets_session_without_respawn -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/tui/app.py tests/test_tui_pilot.py
git commit -m "feat(tui): /clear resets the session on the live subprocess"
```

---

## Task 5: `/reload` handler + `_cancel_inflight`

**Files:**
- Modify: `harness/tui/app.py`
- Test: `tests/test_tui_pilot.py`

**Interfaces:**
- Consumes: `_reset_conversation` (T3), `_teardown`/`_connect` (T2), `_busy` (T2), `_fatal`, `_pending_perm`.
- Produces: `async action_reload(self)`; `_cancel_inflight(self)`.

- [ ] **Step 1: Write the failing test** — append (uses the fake agent's process-start marker added in Task 8; for now assert generation bump + survival):

```python
def test_reload_respawns_and_bumps_generation():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await _send_first_prompt(pilot, app, "hello")
            for _ in range(50):
                await pilot.pause()
                if "done" in _transcript_text(app):
                    break
            gen_before = app._gen
            await app.action_reload()
            await pilot.pause()
            assert app._gen == gen_before + 1, "reload must respawn (generation bumps)"
            assert app._conn is not None, "reconnected after reload"
            assert _transcript_text(app) == "", "scrollback wiped on reload"
            assert app._busy is False
    asyncio.run(go())
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tui_pilot.py::test_reload_respawns_and_bumps_generation -q`
Expected: FAIL — `AttributeError: ... no attribute 'action_reload'`.

- [ ] **Step 3: Implement** — add to `app.py`:

```python
    def _cancel_inflight(self) -> None:
        """Cancel any running prompt/model worker and resolve a pending permission
        future (the subprocess about to die will never answer it)."""
        self.workers.cancel_all()
        if self._pending_perm is not None and not self._pending_perm.done():
            self._pending_perm.set_result(None)
            self._pending_perm = None
        if isinstance(self.screen, PermissionModal):
            self.pop_screen()

    async def action_reload(self) -> None:
        if self._busy:
            return
        self._busy = True
        try:
            self._cancel_inflight()
            await self._reset_conversation()
            self._append_line(_c("muted", "— reloading agent… —"))
            await self._teardown()
            try:
                await self._connect()
                self._append_line(_c("muted", "— reloaded —"))
            except Exception as e:
                self._fatal(f"reload failed: {e}")
        finally:
            self._busy = False
```

(`PermissionModal` is already imported/defined in `app.py` — it's exported and used by the pilot tests. `_c` and `_append_line` exist.)

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tui_pilot.py::test_reload_respawns_and_bumps_generation -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/tui/app.py tests/test_tui_pilot.py
git commit -m "feat(tui): /reload kills + respawns the agent, preserving model"
```

---

## Task 6: Generation gating in `_send_prompt` and `on_session_update`

**Files:**
- Modify: `harness/tui/app.py` (`_send_prompt` ~437-455; `on_session_update` ~506-508)
- Test: `tests/test_tui_pilot.py`

**Interfaces:**
- Consumes: `self._gen` (T2).
- Produces: `_send_prompt` captures its generation and only touches input in `finally` when still current; `on_session_update` drops updates whose generation/session is stale.

- [ ] **Step 1: Write the failing tests** — append:

```python
def test_stale_session_update_after_reload_is_dropped():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await _send_first_prompt(pilot, app, "hello")
            for _ in range(50):
                await pilot.pause()
                if "done" in _transcript_text(app):
                    break
            await app.action_reload()           # bumps _gen; transcript wiped
            await pilot.pause()
            # an update tagged with the OLD generation must be ignored
            stale = SessionUpdate(update_agent_message_text("GHOST"), session_id="fake-session")
            stale._gen = app._gen - 1            # mark as belonging to the prior session
            app.on_session_update(stale)
            await pilot.pause()
            assert "GHOST" not in _transcript_text(app), "stale update must be dropped"
    asyncio.run(go())

def test_send_prompt_finally_no_reenable_after_generation_bump():
    # An old prompt worker whose generation is stale must NOT re-enable input
    # (that would undo a _fatal disable after a reload failure).
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await app._enter_conversation()
            class _Conn:
                async def prompt(self, **kw):
                    return NS(stop_reason="end_turn")
            app._conn = _Conn(); app._session_id = "fake-session"
            app._send_gen = app._gen
            app._active_input().disabled = True
            app._gen += 1                        # simulate a reload happening mid-flight
            await app._send_prompt("x")          # its captured gen is now stale
            assert app._active_input().disabled is True, "stale worker must not re-enable input"
    asyncio.run(go())
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_tui_pilot.py::test_stale_session_update_after_reload_is_dropped tests/test_tui_pilot.py::test_send_prompt_finally_no_reenable_after_generation_bump -q`
Expected: FAIL — `GHOST` renders (no gating); input gets re-enabled.

- [ ] **Step 3: Implement.**

In `_send_prompt` (capture the generation at entry, gate the `finally`). Replace its body's start and `finally`:

```python
    async def _send_prompt(self, text: str) -> None:
        gen = self._gen                           # this turn belongs to this generation
        self._show_working()
        try:
            resp = await self._conn.prompt(
                prompt=[acp.text_block(text)], session_id=self._session_id)
            elapsed = time.monotonic() - self._turn_start
            self._write_meta(elapsed)
            if getattr(resp, "stop_reason", "end_turn") != "end_turn":
                self._append_line(_c("muted", f"— turn ended: {resp.stop_reason} —"))
        except Exception as e:
            self._append_line(_c("error", f"agent disconnected — restart to continue ({e})"))
        finally:
            if gen == self._gen:                  # only the CURRENT generation touches the UI
                self._hide_working()
                self._active_input().disabled = False
                self._active_input().focus()
```

In `on_session_update` (`app.py:506-508`), gate on generation/session. Replace the guard:

```python
    def on_session_update(self, msg: SessionUpdate) -> None:
        if not self._started:
            return
        # drop updates from a reloaded-away session: an explicit _gen tag wins;
        # otherwise a session_id that no longer matches the live session is stale.
        if getattr(msg, "_gen", self._gen) != self._gen:
            return
        if msg.session_id is not None and self._session_id is not None \
                and msg.session_id != self._session_id:
            return
        ...   # rest unchanged
```

> The real `TuiClient` does not set `msg._gen`; the `session_id` comparison covers production (real agent issues fresh uuids). The `_gen` tag is a test seam (and a belt-and-suspenders for the fake agent's constant session_id). `getattr(msg, "_gen", self._gen)` makes untagged production updates pass the gen check and fall through to the session_id check.

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tui_pilot.py -q`
Expected: PASS (new + all existing; the existing `test_late_prior_turn_delta_...` still passes — same generation throughout).

- [ ] **Step 5: Commit**

```bash
git add harness/tui/app.py tests/test_tui_pilot.py
git commit -m "feat(tui): generation gating drops stale updates and stale prompt turns"
```

---

## Task 7: Register `/reload` and `/clear` in the slash menu

**Files:**
- Modify: `harness/tui/commands.py`
- Test: `tests/test_tui_commands.py`

**Interfaces:**
- Consumes: `action_reload`/`action_clear` (T4/T5).
- Produces: registry contains `reload` and `clear`; handlers delegate to the app actions.

- [ ] **Step 1: Write the failing test** — append to `tests/test_tui_commands.py` (read the file's existing style first):

```python
def test_registry_includes_reload_and_clear():
    from harness.tui.commands import build_registry
    names = [c.name for c in build_registry()]
    assert "reload" in names
    assert "clear" in names

def test_reload_clear_handlers_delegate_to_app_actions():
    import asyncio
    from harness.tui.commands import build_registry

    class _App:
        def __init__(self): self.called = []
        async def action_reload(self): self.called.append("reload")
        async def action_clear(self): self.called.append("clear")

    reg = {c.name: c for c in build_registry()}
    app = _App()
    asyncio.run(reg["reload"].handler(app))
    asyncio.run(reg["clear"].handler(app))
    assert app.called == ["reload", "clear"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tui_commands.py -q`
Expected: FAIL — `reload`/`clear` not in registry.

- [ ] **Step 3: Implement** — in `harness/tui/commands.py`, add the handlers and entries:

```python
async def _reload(app) -> None:
    await app.action_reload()


async def _clear(app) -> None:
    await app.action_clear()
```

And in `build_registry()`'s returned list, add after the `models` entry:

```python
        Command("reload", "Restart the agent (reloads edited code)", _reload),
        Command("clear", "Clear the conversation", _clear),
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tui_commands.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/tui/commands.py tests/test_tui_commands.py
git commit -m "feat(tui): register /reload and /clear slash commands"
```

---

## Task 8: Respawn-observability + failure + re-entrancy tests

**Files:**
- Modify: `tests/fake_agent.py` (record process starts)
- Test: `tests/test_tui_pilot.py`

**Interfaces:**
- Consumes: everything above.
- Produces: a startup marker the test reads to PROVE a new OS process spawned on reload; failure + guard tests.

- [ ] **Step 1: Make respawn observable.** In `tests/fake_agent.py`, append a process-start line to a file named by an env var, at the very top of `_main` (before `run_agent`):

```python
async def _main():
    import os
    marker = os.getenv("FAKE_AGENT_STARTS_FILE")
    if marker:
        with open(marker, "a") as f:
            f.write("start\n")
    await acp.run_agent(FakeAgent())
```

- [ ] **Step 2: Write the failing tests** — append to `tests/test_tui_pilot.py`:

```python
def test_reload_starts_a_new_os_process(tmp_path):
    import os
    marker = tmp_path / "starts.txt"
    cmd = [sys.executable, str(REPO / "tests/fake_agent.py")]
    async def go():
        os.environ["FAKE_AGENT_STARTS_FILE"] = str(marker)
        try:
            app = HarnessTui(agent_cmd=cmd, cwd=str(REPO), model="mock")
            async with app.run_test() as pilot:
                await pilot.pause()
                for _ in range(50):
                    await pilot.pause()
                    if marker.exists() and marker.read_text().count("start") >= 1:
                        break
                starts_before = marker.read_text().count("start")
                await app.action_reload()
                for _ in range(50):
                    await pilot.pause()
                    if marker.read_text().count("start") > starts_before:
                        break
            assert marker.read_text().count("start") == starts_before + 1, (
                "reload must spawn exactly one new agent process")
        finally:
            os.environ.pop("FAKE_AGENT_STARTS_FILE", None)
    asyncio.run(go())

def test_reload_failure_keeps_app_alive_and_input_disabled():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await _send_first_prompt(pilot, app, "hello")
            for _ in range(50):
                await pilot.pause()
                if "done" in _transcript_text(app):
                    break
            # make the next _connect fail
            app.agent_cmd = [sys.executable, "-c", "import sys; sys.exit(3)"]
            await app.action_reload()
            await pilot.pause()
            assert app._conn is None, "failed reload leaves no live connection"
            assert app._active_input().disabled is True, "_fatal must disable input"
            assert app._busy is False, "busy released even on failure"
            assert "reload failed" in _transcript_text(app)
    asyncio.run(go())

def test_reload_is_guarded_against_reentry():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            app._busy = True                     # simulate a reload in progress
            gen_before = app._gen
            await app.action_reload()            # must early-return
            assert app._gen == gen_before, "re-entrant reload must be a no-op"
            app._busy = False
    asyncio.run(go())
```

- [ ] **Step 3: Run to verify they fail (then pass after the fake-agent edit)**

Run: `.venv/bin/python -m pytest tests/test_tui_pilot.py -q`
Expected: the three new tests PASS once the fake-agent marker (Step 1) and Tasks 2/5/6 are in place. If `test_reload_starts_a_new_os_process` fails because the marker file isn't written, confirm the fake agent edit landed.

- [ ] **Step 4: Run the FULL suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS — entire suite green (existing TUI tests, capabilities, smoke, plus all new reload/clear tests). No fixture left dirty.

- [ ] **Step 5: Commit**

```bash
git add tests/fake_agent.py tests/test_tui_pilot.py
git commit -m "test(tui): prove reload respawns; cover failure + re-entrancy"
```

---

## Self-Review

**Spec coverage:**
- §1 lifecycle methods (`_connect`/`_teardown`/`_new_session`, failure-atomic) → Task 2. ✓
- §1 generation bump, `_launch_worker_model_id`, `_busy` state → Task 2. ✓
- §2 `_reset_conversation` (stay in conversation, clear streaming/tokens) → Task 3. ✓
- §3 `/clear` handler → Task 4; `/reload` handler → Task 5. ✓
- §4 model re-apply (differs-from-launch + guarded for unsupported agents) → Task 2 (`_reapply_model`). ✓
- §5 session-generation filter + `session_id` plumbing → Task 1 (plumbing) + Task 6 (gating). ✓
- §6 lifecycle guard (`_busy`) + `_cancel_inflight` (workers + permission) → Task 5 (+ guard tested Task 8). ✓
- §7 registry entries → Task 7. ✓
- Testing bullets (clear no-respawn, reload respawn, failure, stale-update, teardown idempotence, re-entrancy) → Tasks 2,4,5,6,8. ✓

**Placeholder scan:** No TBD/TODO. Task 7 references reading `test_tui_commands.py`'s existing style — its test code is fully provided; the note is orientation, not a gap.

**Type consistency:** `_connect()/_teardown()/_new_session()/_reapply_model()` (async, no args), `_reset_conversation()` (async), `action_reload()/action_clear()` (async), `_cancel_inflight()` (sync), `_gen:int`, `_busy:bool`, `_launch_worker_model_id`, `SessionUpdate(update, session_id=None)` — names/signatures consistent across all tasks and match the spec. ✓

**Risk note:** Task 6's `on_session_update` change is the subtlest (gating logic). The existing `test_late_prior_turn_delta_...` is the regression guard — it runs entirely within one generation, so the new gate is transparent to it. Verify it stays green in Task 6 Step 4.
