# Streamed Markdown TUI Output — Implementation Plan

> **For agentic workers:** execute task-by-task with TDD. Steps use `- [ ]`.

**Goal:** Agent streams chat answers token-by-token; the TUI renders them as a
live Markdown widget with a "model is working" indicator.

**Architecture:** Agent side — `ChatHandler.answer_stream` (litellm stream=True),
chat branch emits one `message_chunk` per delta. Client side — transcript becomes
a `VerticalScroll`; streaming text → a `Markdown` widget `.update()`-ed per delta;
discrete items → `Static`; a `LoadingIndicator` shows while working.

**Tech Stack:** Python, litellm, acp SDK, Textual 8.2.7.

**Spec:** docs/superpowers/specs/2026-06-26-tui-streaming-markdown-design.md

## Global Constraints
- Zero upstream edits. STDOUT is the ACP wire (no agent-side stdout prints).
- No client-side threads (streaming pump runs on the agent worker thread via
  `asyncio.run_coroutine_threadsafe(...).result()`, the existing idiom).
- New widgets match the DoneDone theme. Mock mode needs no proxy.
- Test command (from worktree root):
  `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/ -q`
- Baseline: 100/100.

---

### Task 1: Agent streams chat answers

**Files:**
- Modify: `harness/chat_handler.py`
- Modify: `harness/acp_agent.py` (chat branch ~115-121)
- Test: `tests/test_chat_handler.py` (new)

**Interfaces:**
- Produces: `ChatHandler.answer_stream(prompt: str) -> Iterator[str]` yielding
  text pieces. Mock mode (model_id None) yields exactly one piece (the honest
  one-liner). Removes `answer()`.

- [ ] Step 1: Write failing tests in `tests/test_chat_handler.py`:
  - mock mode yields exactly `["[mock mode] classified as chat_question; chat answers require --model vibeproxy. (Routing worked: this did not run the agent.)"]`
  - real mode: monkeypatch `litellm.completion` to return an iterable of fake
    chunks (objects with `.choices[0].delta.content`), assert `answer_stream`
    yields the non-empty pieces in order AND that `completion` was called with
    `stream=True`.
- [ ] Step 2: Run → FAIL (answer_stream missing).
- [ ] Step 3: Implement `answer_stream` per spec; remove `answer()`.
- [ ] Step 4: Update `acp_agent.py` chat branch to pump pieces via
  `asyncio.run_coroutine_threadsafe(self._conn.session_update(session_id,
  message_chunk(piece)), loop).result()` inside a `loop.run_in_executor` pump
  (mirrors the existing tool-call marshalling at acp_agent.py:149-151).
- [ ] Step 5: Run `tests/test_chat_handler.py` + `tests/test_acp_smoke.py` → PASS.
- [ ] Step 6: Commit.

---

### Task 2: Transcript becomes a widget container + live Markdown + working indicator

**Files:**
- Modify: `harness/tui/app.py`
- Modify: `harness/tui/app.tcss`
- Test: `tests/test_tui_pilot.py` (extend), `tests/fake_agent.py` (extend)

**Interfaces:**
- Consumes: `render_update` (unchanged), `message_chunk` deltas from Task 1.
- Produces: transcript is `VerticalScroll(id="transcript")`; helpers
  `_append_line(markup)`, `_append(widget)`, `_show_working()`, `_hide_working()`;
  streaming state `_streaming_md`, `_stream_buf`.

- [ ] Step 1: Extend `tests/fake_agent.py` — on a prompt containing "STREAM",
  emit several `update_agent_message_text` chunks ("Hello ", "**world** ", "done")
  for one turn (plus the existing chip), then end_turn.
- [ ] Step 2: Write failing pilot test in `tests/test_tui_pilot.py`:
  send "STREAM please" → after send, a working indicator (`#working`) exists;
  after the turn, a `Markdown` widget exists in the transcript whose source ==
  "Hello **world** done" (accumulated, not 3 separate lines) and `#working` is
  gone. (Query the VerticalScroll; read `Markdown`'s source via its
  `_markdown`/`.source` attr or the documented accessor.)
- [ ] Step 3: Run → FAIL.
- [ ] Step 4: Implement in `app.py`:
  - `_enter_conversation` mounts `VerticalScroll(id="transcript")` (not RichLog).
  - `_transcript` property returns the VerticalScroll.
  - `_append(widget)`: mount into transcript + scroll_end.
  - `_append_line(markup)`: `_append(Static(markup, markup=True))`.
  - `_show_working()`: idempotent mount of `LoadingIndicator(id="working")` (or a
    themed animated Static) at the end; `_hide_working()`: remove if present.
  - `on_session_update` message kind: first delta `_hide_working()` +
    create/mount `Markdown("")` as `_streaming_md`, `_stream_buf=""`; each delta
    `_stream_buf += text`, `_streaming_md.update(_stream_buf)`, scroll_end.
  - tool / tool_update kinds: `_streaming_md = None` (finalize) then `_append_line`.
  - other kinds (chips/user/thought/meta/errors): migrate the existing
    `log.write(...)` calls to `_append_line(...)` (same markup strings).
  - `_send_prompt`: `_show_working()` before `await conn.prompt`; in finally /
    after return: `_streaming_md = None`, `_hide_working()`, then `_write_meta`.
  - `_add_user_message`, `_fatal`, any other `self._transcript.write(...)` →
    `_append_line(...)`.
- [ ] Step 5: Update `app.tcss` — style `#transcript Markdown` flush (kill default
  margins), style `#working`. Keep existing styles.
- [ ] Step 6: Migrate other pilot tests that read `RichLog.lines` (the
  `_transcript_text` helper in test_tui_pilot.py) to read widget contents from
  the VerticalScroll (concatenate Static renderables + Markdown sources).
- [ ] Step 7: Run full `tests/` suite → all PASS (was 100, now 100 + new).
- [ ] Step 8: Commit.

---

### Task 3: Visual verification + manual smoke

**Files:** none (verification only).

- [ ] Step 1: `App.run_test()` pilot → `save_screenshot` of a streamed markdown
  answer (with a code block + bold), convert via `qlmanage -t`, Read the PNG to
  confirm: markdown is formatted (not literal), and the working indicator renders.
- [ ] Step 2: Document a manual smoke in the PR/commit body: `dn` (live proxy) →
  ask "what is python in 2 sentences with a code example" → observe it stream +
  format + show the working indicator.
- [ ] Step 3: Final full suite run; record pass count.
