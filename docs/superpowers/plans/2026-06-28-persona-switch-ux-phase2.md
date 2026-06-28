# Persona-Switch UX — Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the user switches BACK to a persona that has history, replay that persona's prior conversation into the freshly-cleared transcript and show a `── resumed ──` seam — so the engine's per-seat persistence becomes visible on screen.

**Architecture:** Phase 1 already clears the transcript and shows a room header on switch (client-only). Phase 2 adds the engine half: a new `harness/replay_session` ext-method streams the seat's stored `transcript` back as ACP `session_update` notifications, which render through the TUI's **existing** `on_session_update → render_update` path (zero new client renderer). `set_persona` gains a `message_count` so the client knows whether to replay (and scopes the empty-room copy to genuinely-new personas). The client triggers replay as an async worker after `_clear_transcript()`, keeping `_apply_persona_switch` synchronous (a Phase-1 invariant).

**Tech Stack:** Python 3.11, ACP (agent-client protocol) over JSON-RPC, Textual TUI, pytest.

## Global Constraints

- **`_apply_persona_switch` stays SYNCHRONOUS.** Phase 1's I1 fix and the create-persona modal callback depend on this. The async replay is scheduled via `self.run_worker(...)`, never by making the method async.
- **Reuse the existing render path.** Replayed messages must flow through `on_session_update → render_update → _stream_message`/user-line. Do NOT write a second renderer or build transcript widgets directly from `{role, content}`.
- **Transport = `session_update` notifications** (decided): the engine emits ACP updates; the client renders them via its normal path.
- **Trigger = a separate `harness/replay_session` ext-method** (decided), called by the client AFTER `_clear_transcript()`. `set_persona` does NOT stream the transcript itself (keeps its payload small).
- **Replay reads `SessionState.transcript`** (`[{role, content, origin}]`, acp_session.py:22), populated every turn via `store.extend`. NOT `state.history` (that's turn summaries — what `load_session` wrongly uses for this purpose).
- **Copy upgrade is gated on `message_count`:** a persona WITH history → replay + `── resumed ──` seam + room subline `a separate conversation · remembers across switches`; a persona with `message_count == 0` → the existing empty-room line (now correctly only for new personas), no seam.
- **Test command (from this worktree root):** `<repo-root>/.venv/bin/python -m pytest tests/<file> -q -p no:cacheprovider`. `tests/conftest.py` (PR #94) resolves imports to this worktree.
- **Verify the switch-back behavior visually** (SVG screenshot) at the end — never trust tests alone for TUI layout (project rule).

---

## File Structure

- `harness/acp_emit.py` (modify) — add a `user_message_chunk(text)` builder (mirrors `message_chunk`) so the engine can emit user-role messages that render as `kind="user"`.
- `harness/acp_agent.py` (modify) — (a) add `message_count` to `_activate_seat`'s return; (b) add the `harness/replay_session` handler in `ext_method` dispatch that streams `state.transcript` as `session_update`s + a `resumed` seam.
- `harness/tui/app.py` (modify) — (a) fold a `harness.resumed` meta branch in `on_session_update` into a `── resumed ──` divider line; (b) in `_apply_persona_switch`, after `_clear_transcript()`, schedule `_replay_session(id)` as a worker when `message_count > 0`, and gate the empty-room line on `message_count == 0`; (c) add the async `_replay_session` method.
- Tests: `tests/test_replay_session.py` (engine: new), `tests/test_persona_switch_ux.py` (client: extend).

---

## Task 1: Add `user_message_chunk` builder to acp_emit

**Files:**
- Modify: `harness/acp_emit.py`
- Test: `tests/test_acp_emit.py`

**Interfaces:**
- Produces: `user_message_chunk(text: str)` returning `update_user_message_text(text)` (an ACP update that `render_update` maps to `kind="user"`).

**Context:** `acp_emit.py` currently imports only `update_agent_message_text` and exposes `message_chunk` (acp_emit.py:37-38). The ACP lib also provides `update_user_message_text` (verified). Replay needs to emit user-role messages; add the symmetric builder.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_acp_emit.py  (add to the existing file)
def test_user_message_chunk_builds_user_update():
    from harness.acp_emit import user_message_chunk
    from harness.tui.render import render_update
    upd = user_message_chunk("hello from the past")
    item = render_update(upd)
    assert item is not None
    assert item.kind == "user"
    assert item.text == "hello from the past"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `<repo-root>/.venv/bin/python -m pytest tests/test_acp_emit.py::test_user_message_chunk_builds_user_update -q -p no:cacheprovider`
Expected: FAIL with `ImportError: cannot import name 'user_message_chunk'`

- [ ] **Step 3: Add the import and builder**

In `harness/acp_emit.py`, add `update_user_message_text` to the `from acp import (...)` block (after `update_agent_message_text`), then add below `message_chunk`:

```python
def user_message_chunk(text: str):
    return update_user_message_text(text)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `<repo-root>/.venv/bin/python -m pytest tests/test_acp_emit.py::test_user_message_chunk_builds_user_update -q -p no:cacheprovider`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add harness/acp_emit.py tests/test_acp_emit.py
git commit -m "feat(acp): add user_message_chunk builder for transcript replay"
```

---

## Task 2: Add `message_count` to `_activate_seat`'s return

**Files:**
- Modify: `harness/acp_agent.py` (`_activate_seat` ~L205-222)
- Test: `tests/test_acp_agent.py`

**Interfaces:**
- Consumes: `self._store.get(session_id).transcript` (a `list`).
- Produces: `_activate_seat` return dict gains `"message_count": int` = `len(transcript)`.

**Context:** `_activate_seat` returns `{"ok": True, "id": pid, "session_id": seat.session_id, "model": seat.model}` (acp_agent.py:222). The client uses `message_count` to decide whether to replay and which copy to show. It does NOT send the transcript itself (replay is a separate call), so the payload stays small.

- [ ] **Step 1: Write the failing test**

Look first at how `tests/test_acp_agent.py` constructs the agent + a session with a transcript (grep for `set_persona`, `_activate_seat`, `extend`, `transcript`). Then:

```python
def test_set_persona_returns_message_count():
    # Build the agent the way the existing set_persona test does, switch to a
    # persona, push two transcript messages into its session, switch away and
    # back, and assert the returned message_count reflects the stored transcript.
    # (Mirror the existing _activate_seat/set_persona test's construction.)
    agent = _make_agent_for_test()              # reuse the existing helper/fixture
    resp = agent._activate_seat("default")
    sid = resp["session_id"]
    agent._store.extend(sid, [
        {"role": "user", "content": "hi", "origin": "chat"},
        {"role": "assistant", "content": "hello", "origin": "chat"},
    ])
    resp2 = agent._activate_seat("default")
    assert resp2["message_count"] == 2
```

> If `_make_agent_for_test`/the construction helper differs, copy the EXACT construction the nearest existing `_activate_seat`/`set_persona` test uses in `tests/test_acp_agent.py`. The assertion (message_count == len(transcript)) is the invariant.

- [ ] **Step 2: Run test to verify it fails**

Run: `<repo-root>/.venv/bin/python -m pytest tests/test_acp_agent.py::test_set_persona_returns_message_count -q -p no:cacheprovider`
Expected: FAIL with `KeyError: 'message_count'`

- [ ] **Step 3: Add `message_count` to the return**

In `_activate_seat` (acp_agent.py ~L222), before the return, read the transcript length and include it:

```python
        count = len(self._store.get(seat.session_id).transcript)
        return {"ok": True, "id": pid, "session_id": seat.session_id,
                "model": seat.model, "message_count": count}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `<repo-root>/.venv/bin/python -m pytest tests/test_acp_agent.py::test_set_persona_returns_message_count -q -p no:cacheprovider`
Expected: PASS

- [ ] **Step 5: Run the existing set_persona/agent tests (regression)**

Run: `<repo-root>/.venv/bin/python -m pytest tests/test_acp_agent.py -q -p no:cacheprovider`
Expected: PASS (the added key is additive; existing assertions on `ok`/`id`/`session_id`/`model` are unaffected).

- [ ] **Step 6: Commit**

```bash
git add harness/acp_agent.py tests/test_acp_agent.py
git commit -m "feat(acp): set_persona returns message_count for replay gating"
```

---

## Task 3: Add the `harness/replay_session` ext-method (engine streams the transcript)

**Files:**
- Modify: `harness/acp_agent.py` (`ext_method` dispatch ~L177; add a handler + helper)
- Test: `tests/test_replay_session.py` (create)

**Interfaces:**
- Consumes: `user_message_chunk` (Task 1), `message_chunk`/`with_meta` (existing acp_emit), `self._store.get(sid).transcript`, the persona→seat resolution (`_persona_sessions.get_or_create` / the active seat).
- Produces: an `ext_method` branch `method == "harness/replay_session"` that, for the persona `{id}`, emits one `session_update` per transcript message (user vs assistant by `role`) then one `resumed`-seam update carrying `field_meta={"harness": {"resumed": True}}`, and returns `{"ok": True, "count": n}`.

**Context:** `ext_method` dispatches `harness/set_persona` etc. (acp_agent.py ~L177). The replay emits via `await self._conn.session_update(session_id, update)` — the SAME notification `load_session` uses (acp_agent.py:248-261) — but loops over `state.transcript`, not `state.history`. The client's existing `on_session_update` renders each. The seat for `{id}` already exists (the client called `set_persona` first), so resolve its session_id the same way `_activate_seat` does.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_replay_session.py  (new)
import asyncio
import pytest

# Reuse the agent-construction + fake-conn pattern from tests/test_acp_agent.py.
# The fake conn must record every session_update(session_id, update) call so we
# can assert the replay stream.

def test_replay_session_streams_transcript_then_resumed_seam():
    async def go():
        agent, conn = _make_agent_with_recording_conn()      # mirror test_acp_agent.py
        resp = agent._activate_seat("default")
        sid = resp["session_id"]
        agent._store.extend(sid, [
            {"role": "user", "content": "remember 42", "origin": "chat"},
            {"role": "assistant", "content": "noted: 42", "origin": "chat"},
        ])
        out = await agent.ext_method("harness/replay_session", {"id": "default"})
        assert out == {"ok": True, "count": 2}
        # conn.updates is a list of (session_id, update) recorded by the fake conn.
        kinds = [_render_kind(u) for (_sid, u) in conn.updates]   # helper: render_update(u).kind
        # two messages then a resumed-seam update (meta-bearing, empty text)
        assert kinds[:2] == ["user", "message"]
        last_sid, last_upd = conn.updates[-1]
        meta = getattr(last_upd, "field_meta", None) or {}
        assert (meta.get("harness") or {}).get("resumed") is True

    asyncio.run(go())


def test_replay_session_empty_transcript_emits_no_messages_only_returns_zero():
    async def go():
        agent, conn = _make_agent_with_recording_conn()
        agent._activate_seat("default")
        out = await agent.ext_method("harness/replay_session", {"id": "default"})
        assert out == {"ok": True, "count": 0}
        # no per-message updates (a resumed seam with zero history is pointless);
        # assert no message/user updates were emitted.
        kinds = [_render_kind(u) for (_sid, u) in conn.updates]
        assert "user" not in kinds and "message" not in kinds

    asyncio.run(go())
```

> Build `_make_agent_with_recording_conn`, `_render_kind`, by copying the exact agent + fake-conn construction from `tests/test_acp_agent.py` (it already fakes `self._conn` and exercises `ext_method`). `_render_kind(u)` = `from harness.tui.render import render_update; render_update(u).kind`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `<repo-root>/.venv/bin/python -m pytest tests/test_replay_session.py -q -p no:cacheprovider`
Expected: FAIL — `ext_method` returns `{}` for the unknown `harness/replay_session` method, so `out == {}` ≠ expected.

- [ ] **Step 3: Implement the handler + helper**

In `harness/acp_agent.py`, add to the `ext_method` dispatch (near the `set_persona` branch ~L177):

```python
        if method == "harness/replay_session":
            pid = (params or {}).get("id")
            if not isinstance(pid, str) or not pid:
                return {"ok": False, "error": "missing id"}
            from harness import persona_select
            try:
                return await self._replay_session(pid)
            except (persona_select.UnknownPersona, persona_select.InvalidPersonaId) as e:
                logger.warning("replay_session rejected id %r: %s", pid, e)
                return {"ok": False, "error": str(e)}
```

Add the helper (near `_activate_seat`):

```python
    async def _replay_session(self, pid: str) -> dict:
        """Stream the persona's stored transcript back to the client as ACP
        session_update notifications (rendered by the client's normal path), then
        a `resumed` seam. The seat already exists (set_persona ran first)."""
        from harness.acp_emit import message_chunk, user_message_chunk, with_meta
        seat = self._persona_sessions.get_or_create(
            pid, cwd=self._cwd, store=self._store,
            resolve_ws=__import__("harness.persona_select", fromlist=["resolve_workspace"]).resolve_workspace,
            resolve_model=lambda p: seat_model_unused)  # see note
        sid = seat.session_id
        transcript = self._store.get(sid).transcript
        for m in transcript:
            upd = (user_message_chunk(m["content"]) if m["role"] == "user"
                   else message_chunk(m["content"]))
            await self._conn.session_update(sid, upd)
        if transcript:
            seam = with_meta(message_chunk(""), {"resumed": True})
            await self._conn.session_update(sid, seam)
        return {"ok": True, "count": len(transcript)}
```

> **Implementer note on the seat lookup:** `_activate_seat` already resolves the seat via `self._persona_sessions.get_or_create(...)` with `resolve_ws=persona_select.resolve_workspace` and a `resolve_session_model_for` lambda. Do NOT duplicate that lambda crudely (the sketch above is a placeholder). Instead, **extract** the seat-resolution from `_activate_seat` into a small private helper `_seat_for(pid) -> Seat` and call it from BOTH `_activate_seat` and `_replay_session`. This avoids duplicating the model-resolver wiring. Keep `_activate_seat`'s behavior identical. If extraction proves noisy, the minimal alternative is: since `set_persona` ran immediately before, the active seat's `session_id` is `self._store`-resolvable via the persona id through the same `get_or_create` call `_activate_seat` makes — reuse exactly that call.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `<repo-root>/.venv/bin/python -m pytest tests/test_replay_session.py -q -p no:cacheprovider`
Expected: PASS (both)

- [ ] **Step 5: Run the agent suite (regression — seat extraction must not change set_persona)**

Run: `<repo-root>/.venv/bin/python -m pytest tests/test_acp_agent.py tests/test_acp_session.py tests/test_persona_sessions.py -q -p no:cacheprovider`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add harness/acp_agent.py tests/test_replay_session.py
git commit -m "feat(acp): harness/replay_session streams a seat's transcript + resumed seam"
```

---

## Task 4: Client renders the `resumed` seam + triggers replay on switch-back

**Files:**
- Modify: `harness/tui/app.py` — `on_session_update` (fold `harness.resumed`), `_apply_persona_switch` (schedule replay + gate empty-room copy), add `_replay_session`
- Test: `tests/test_persona_switch_ux.py` (extend)

**Interfaces:**
- Consumes: `resp["message_count"]` (Task 2), the engine's `harness/replay_session` (Task 3), and the streamed updates incl. the `harness.resumed` meta.
- Produces: `_replay_session(self, pid: str) -> None` (async) calling `ext_method("harness/replay_session", {"id": pid})`; a `── resumed ──` divider rendered when a `harness.resumed` update arrives; the empty-room line shown only when `message_count == 0`.

**Context:** `_apply_persona_switch` (app.py ~L1228 in the merged Phase 1) repoints the session, calls `_clear_transcript()`, then writes the room header. `on_session_update` already folds `harness.*` meta (stream_reset/task_classified/persona) before `render_update` (~L1013-1044). The replay updates arrive AFTER the switch repoints `_session_id`, so the session-id/gen guards pass.

- [ ] **Step 1: Write the failing test (seam renders)**

```python
def test_resumed_meta_renders_seam_divider():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await _send_first_prompt(pilot, app, "hello")   # reach conversation view
            for _ in range(60):
                await pilot.pause()
                if app._started and app.query("#transcript"):
                    break
            # craft a resumed-seam update and post it like the engine would
            from harness.acp_emit import message_chunk, with_meta
            seam = with_meta(message_chunk(""), {"resumed": True})
            from harness.tui.messages import SessionUpdate
            app.post_message(SessionUpdate(seam, session_id=app._session_id, gen=app._gen))
            for _ in range(20):
                await pilot.pause()
            text = _transcript_text(app)
        assert "resumed" in text.lower(), f"resumed seam not rendered:\n{text}"

    asyncio.run(go())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `<repo-root>/.venv/bin/python -m pytest tests/test_persona_switch_ux.py::test_resumed_meta_renders_seam_divider -q -p no:cacheprovider`
Expected: FAIL — no `harness.resumed` branch exists, so the empty message renders as nothing/blank, "resumed" absent.

- [ ] **Step 3: Fold the `resumed` meta into a seam divider**

In `on_session_update` (app.py), alongside the existing `harness.*` meta folds (where `stream_reset`/`task_classified` are handled, ~L1013-1044), add — BEFORE `render_update` is called:

```python
        if isinstance(meta, dict) and (meta.get("harness") or {}).get("resumed"):
            self._end_stream(boundary=True)
            self._append_line(_c("muted", "── resumed ──────────────────────────────"))
            return
```

- [ ] **Step 4: Run the seam test to verify it passes**

Run: `<repo-root>/.venv/bin/python -m pytest tests/test_persona_switch_ux.py::test_resumed_meta_renders_seam_divider -q -p no:cacheprovider`
Expected: PASS

- [ ] **Step 5: Write the failing test (switch-back triggers replay + gates copy)**

```python
def test_switch_with_history_replays_and_skips_empty_copy():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await _send_first_prompt(pilot, app, "first")
            for _ in range(60):
                await pilot.pause()
                if app._started and app.query("#transcript"):
                    break

            class _ReplayConn:
                def __init__(self):
                    self.calls = []
                async def ext_method(self, method, params):
                    self.calls.append((method, params))
                    if method == "harness/set_persona":
                        return {"ok": True, "id": "maya", "session_id": "sess-maya",
                                "model": "mock", "message_count": 3}
                    if method == "harness/replay_session":
                        return {"ok": True, "count": 3}
                    return {}
            app._conn = _ReplayConn()
            app._turn_active = False
            await app.on_persona_selected(PersonaSelected("maya"))
            for _ in range(30):
                await pilot.pause()
            text = _transcript_text(app)
        # with history: replay_session was called; the empty-room line is NOT shown
        assert ("harness/replay_session", {"id": "maya"}) in app._conn.calls
        assert "separate from your others. Say hello." not in text

    asyncio.run(go())


def test_switch_to_new_persona_shows_empty_copy_no_replay():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await _send_first_prompt(pilot, app, "first")
            for _ in range(60):
                await pilot.pause()
                if app._started and app.query("#transcript"):
                    break

            class _NewConn:
                def __init__(self): self.calls = []
                async def ext_method(self, method, params):
                    self.calls.append((method, params))
                    if method == "harness/set_persona":
                        return {"ok": True, "id": "fresh", "session_id": "sess-fresh",
                                "model": "mock", "message_count": 0}
                    return {}
            app._conn = _NewConn()
            app._turn_active = False
            await app.on_persona_selected(PersonaSelected("fresh"))
            for _ in range(20):
                await pilot.pause()
            text = _transcript_text(app)
        assert ("harness/replay_session", {"id": "fresh"}) not in app._conn.calls
        assert "separate from your others. Say hello." in text

    asyncio.run(go())
```

- [ ] **Step 6: Run to verify they fail**

Run: `<repo-root>/.venv/bin/python -m pytest tests/test_persona_switch_ux.py -k "replays_and_skips or new_persona_shows_empty" -q -p no:cacheprovider`
Expected: FAIL — replay is never called and the empty line always shows (no gating yet).

- [ ] **Step 7: Add replay trigger + copy gating in `_apply_persona_switch`, plus `_replay_session`**

In `_apply_persona_switch` (app.py ~L1228), after `self._clear_transcript()`, schedule replay and gate the empty-room line on `message_count`. The current Phase-1 block writes the header then three lines unconditionally; change the empty-room line to be conditional and add the replay worker:

```python
        self._clear_transcript()
        count = resp.get("message_count", 0)
        if self._started:
            name = self._persona_display_name(resp["id"])
            if note:
                self._append_line(_c("muted", note))
            else:
                self._append_line(_c("accent", f"now in {name}'s conversation"))
                self._append_line(_c("muted", "a separate conversation · remembers across switches"))
                if count == 0:
                    self._append_line(_c(
                        "muted",
                        f"This is {name}'s conversation. It's separate from your others "
                        f"and remembers across switches. Say hello."))
        if count > 0 and self._conn is not None:
            self.run_worker(self._replay_session(resp["id"]), thread=False)
        self._show_drawer(False)
        self._active_input().focus()
```

Add the async method (near `_switch_persona`):

```python
    async def _replay_session(self, pid: str) -> None:
        """Ask the engine to stream this persona's prior transcript back; the
        streamed session_updates render through the normal on_session_update path,
        ending with the `resumed` seam."""
        try:
            await self._conn.ext_method("harness/replay_session", {"id": pid})
        except Exception as e:
            self._notify_line(f"could not load earlier messages: {e}")
```

> Note: the replay worker is scheduled AFTER the room header is written, so the header appears first, then the replayed messages, then the seam — matching the spec's visual (`…earlier… / ── resumed ── / you: ▌`). The header "a separate conversation · remembers across switches" subline is now used for ALL switches (the persistence claim is true in Phase 2); the empty-room "Say hello" line only when `count == 0`.

- [ ] **Step 8: Run the gating tests to verify they pass**

Run: `<repo-root>/.venv/bin/python -m pytest tests/test_persona_switch_ux.py -k "replays_and_skips or new_persona_shows_empty" -q -p no:cacheprovider`
Expected: PASS

- [ ] **Step 9: Update the Phase-1 copy test if it asserts the old subline**

The Phase-1 test asserted the room header / `a separate conversation` subline. The subline text changed to `a separate conversation · remembers across switches`. Run the persona-switch suite and update any test asserting the old subline verbatim.

Run: `<repo-root>/.venv/bin/python -m pytest tests/test_persona_switch_ux.py -q -p no:cacheprovider`
Expected: PASS (after updating the subline assertion).

- [ ] **Step 10: Commit**

```bash
git add harness/tui/app.py tests/test_persona_switch_ux.py
git commit -m "feat(tui): replay persona transcript on switch-back + resumed seam"
```

---

## Task 5: End-to-end integration + full suite + visual check

**Files:**
- Test: `tests/test_persona_switch_ux.py` (one e2e) — and copy updates in the spec's §5.4/§5.5 inventory are already in §6.
- Modify (copy, if a test asserts it): none expected beyond Task 4's subline update.

**Interfaces:** none new.

**Context:** Prove the full loop with the real fake-agent subprocess: send a turn as the launch persona, switch to a second persona (new), switch back to the launch persona, and assert its earlier message reappears above a resumed seam. Then full suite + visual.

- [ ] **Step 1: Write the e2e test (real subprocess, full loop)**

```python
def test_e2e_switch_away_and_back_replays_history():
    """Send as persona A → switch to B (new) → switch back to A → A's earlier
    message is visible again, above a resumed seam. Uses the REAL fake-agent so
    set_persona/replay_session round-trip through the actual ext_method dispatch."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await _send_first_prompt(pilot, app, "REMEMBER_ALPHA")
            for _ in range(60):
                await pilot.pause()
                if "REMEMBER_ALPHA" in _transcript_text(app):
                    break
            # NOTE: this requires the fake agent to support set_persona/replay_session
            # over the real conn. If the fake agent does not implement these ext-methods,
            # SKIP the real-subprocess e2e and instead assert the wiring via the
            # _ReplayConn fake used in Task 4 (the unit path already covers behavior).
            ...
    asyncio.run(go())
```

> **Implementer decision:** check whether `tests/fake_agent.py` routes `ext_method` to a real `HarnessAgent` (in which case the e2e works end-to-end) or is a thin stub (in which case `set_persona`/`replay_session` aren't implemented there). If thin, do NOT bloat the fake agent for one test — the Task 3 engine tests + Task 4 client tests together already prove the loop. In that case, replace this e2e with a comment documenting the split coverage and move to Step 2.

- [ ] **Step 2: Run the FULL suite**

Run: `<repo-root>/.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Expected: PASS (all). Record the count. (The known flake `test_pilot_streams_deltas_into_one_markdown_widget` may fire under load — confirm it passes in isolation; do not chase it.)

- [ ] **Step 3: Visual confirmation (project rule)**

Write a temporary pilot (scratchpad, NOT tests/) that boots, sends a prompt as persona A, switches to B, switches back to A via a fake `_conn` whose `set_persona` returns `message_count: 1` and whose `replay_session` streams one `user_message_chunk("REMEMBER_ALPHA")` + the resumed seam, then `app.save_screenshot(scratchpad/switchback.svg)`. Read the SVG and confirm: (a) "REMEMBER_ALPHA" is visible again, (b) a "resumed" divider appears above the composer, (c) nothing is visually broken. Delete the temp script; do not commit the SVG.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "test(tui): persona switch-back replay coverage + full-suite green"
```

---

## Self-Review

**Spec coverage (§6):**
- §6 transport = session_update stream → Tasks 1 (user builder) + 3 (engine emits) + 4 (client renders via normal path) ✓
- §6 trigger = separate `harness/replay_session` after clear → Task 3 (engine) + Task 4 (client schedules worker post-clear) ✓
- §6.1 reads `transcript` not `history` → Task 3 ✓; `message_count` on set_persona → Task 2 ✓
- §6.1 resumed seam via `with_meta({resumed:True})` → Task 3 (emit) + Task 4 (render) ✓
- §6.2 async-seam (worker, `_apply_persona_switch` stays sync) → Task 4 ✓
- §6.2 copy upgrade gated on count → Task 4 (subline always; empty-room only when count==0) ✓
- §6.2 insertion point after `_clear_transcript()` → Task 4 ✓

**Placeholder scan:** Task 3's `_replay_session` sketch contains a deliberately-flagged placeholder for the seat lookup with an explicit implementer note to **extract `_seat_for(pid)` from `_activate_seat`** (not copy the model-resolver lambda). This is the one spot requiring judgment; it's called out, not hidden. All other steps have concrete code.

**Type consistency:** `user_message_chunk(text)`, `message_count: int`, `harness/replay_session` params `{id}` / return `{ok, count}`, `_replay_session(pid)` (both engine async helper and client async method share the name across files but are distinct methods on different classes — acceptable; note in dispatch).

**Naming collision to flag at review:** both the engine (`HarnessAgent._replay_session`) and the client (`HarnessTui._replay_session`) use `_replay_session`. They're on different classes so there's no real clash, but the reviewer should confirm the engine one is `async def _replay_session(self, pid) -> dict` (returns) and the client one is `async def _replay_session(self, pid) -> None` (fire-and-forget worker). If the duplication is confusing, rename the client's to `_request_replay`.

**One open risk for the reviewer:** Task 3's seat extraction touches `_activate_seat` (shared by set_persona AND create_persona). The regression run (Task 3 Step 5) must stay green to prove create/switch are unaffected.
