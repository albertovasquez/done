# Streamed Markdown TUI Output — Design

**Date:** 2026-06-26
**Status:** Approved
**Branch / worktree:** `tui-streaming-markdown` (`.worktrees/tui-streaming-markdown`)

## Goal

Agent answers stream token-by-token into the TUI and render as live, formatted
**Markdown** (bold, headings, code blocks, lists), with a visible **"model is
working"** indicator so the user can see when the model is active. Fixes the
observed "what is python" case: the answer arrived as one unformatted blob.

## Root cause (diagnosed in code)

Two independent bugs, one per side of the ACP wire:

- **No streaming (agent side):** for a `chat_question`, `acp_agent.py:115-121`
  calls `handler.answer(text)` which runs `litellm.completion(...)` to completion
  (non-streaming, `chat_handler.py:26`) and emits the whole answer as ONE
  `message_chunk`. Nothing can stream.
- **No markdown (client side):** `app.py:418` renders agent text via
  `log.write(_c('foreground', self._escape(item.text)))` into an append-only
  `RichLog`. `_escape` backslash-escapes `[`; markdown shows as literal chars.

VibeProxy streaming is verified working (probe: 6 incremental `delta.content`
chunks for a 3-word reply). Textual 8.2.7 `Markdown.update(str) -> AwaitComplete`
is the live-update API (verified).

## Architecture

Fix both sides:

- **A — Agent streams chat answers.** `ChatHandler` gains a generator
  `answer_stream(prompt) -> Iterator[str]` using `litellm.completion(...,
  stream=True)` yielding `delta.content` pieces. The chat-dispatch branch
  iterates it on the worker thread and emits one `message_chunk(piece)` per
  piece via `session_update`, marshalled to the loop the same way the agent path
  already marshals env callbacks. Mock mode yields its honest one-liner as a
  single piece (no proxy needed). The non-streaming `answer()` is removed (only
  caller is the chat branch).
- **B — Client renders deltas as a live Markdown widget.** The transcript stops
  being a single append-only `RichLog` and becomes a `VerticalScroll` hosting
  mountable widgets. Streaming agent text is a `textual.widgets.Markdown` widget
  that accumulates the turn's text and is `.update()`-ed on each delta. Discrete
  items (user msg, chips, tool calls, tool updates, meta line) are `Static`
  widgets mounted into the same scroll.
- **C — "Working" indicator.** A themed activity line ("⠋ thinking…" via a
  Textual `LoadingIndicator` or animated Static) mounts at turn start and is
  removed when the first agent `message` delta arrives or the turn ends. Tool
  calls already show `→ in_progress` so the agent path is covered too.

## Components

### `harness/chat_handler.py` (modified)

```python
class ChatHandler:
    def __init__(self, worker_model_id: str | None): ...
    def answer_stream(self, prompt: str) -> Iterator[str]:
        if self._model_id is None:
            yield "[mock mode] classified as chat_question; chat answers require " \
                  "--model vibeproxy. (Routing worked: this did not run the agent.)"
            return
        import litellm  # lazy
        stream = litellm.completion(
            model="openai/" + self._model_id,
            api_base=os.getenv("VIBEPROXY_BASE_URL", "http://localhost:8317/v1"),
            api_key=os.getenv("VIBEPROXY_API_KEY", "dummy-not-used"),
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1000, stream=True,
        )
        for chunk in stream:
            piece = (chunk.choices[0].delta.content or "") if chunk.choices else ""
            if piece:
                yield piece
```
Remove `answer()` (no other caller).

### `harness/acp_agent.py` (modified — chat branch ~115-121)

Replace the single blocking call + single emit with: iterate
`handler.answer_stream(text)` on the worker thread, emitting each piece. Because
the generator is blocking (litellm), run it in the executor and pump pieces back
to the loop. Pattern (mirrors how the agent path marshals from the worker
thread via `asyncio.run_coroutine_threadsafe`):

```python
if cls.task_type == "chat_question":
    handler = ChatHandler(self._worker_model_id)
    def pump():
        for piece in handler.answer_stream(text):
            asyncio.run_coroutine_threadsafe(
                self._conn.session_update(session_id, message_chunk(piece)), loop
            ).result()
    await loop.run_in_executor(None, pump)
    self._store.record(session_id, {"prompt": text, "stop_reason": "end_turn",
                                    "kind": "chat"})
    return acp.PromptResponse(stop_reason="end_turn")
```
(Exact marshalling detail is the implementer's to match existing code in
`acp_agent.py` — the existing agent path already does loop-safe scheduling from
the worker thread; reuse that mechanism. STDOUT stays the wire — no prints.)

### `harness/tui/app.py` (modified — the structural change)

- `_enter_conversation`: mount a `VerticalScroll(id="transcript")` instead of a
  `RichLog`. Keep the bottom composer + statusbar as-is.
- New thin helper `_append(widget)` mounts a widget into the scroll and scrolls
  to end. A `_append_line(markup: str)` convenience mounts a themed `Static`
  (markup=True) — used by all the existing discrete writers (chips, user, tool,
  tool_update, meta, errors) so their call sites change minimally.
- Streaming state: `self._streaming_md: Markdown | None`, `self._stream_buf: str`.
- `on_session_update`, `message` kind:
  - if `self._streaming_md is None`: remove the working indicator, create a
    `Markdown("")`, mount it, set `_stream_buf = ""`.
  - `_stream_buf += item.text`; `self._streaming_md.update(self._stream_buf)`;
    scroll to end.
- `on_session_update`, other kinds → `_append_line(...)` (same themed markup as
  today). A `tool` or `tool_update` item **finalizes** the current streaming
  Markdown (`self._streaming_md = None`) so post-tool text starts a new block
  below the tool (preserves chronological order).
- Turn boundary: in `_send_prompt`, after `prompt(...)` returns, set
  `self._streaming_md = None` and write the meta line (existing). The next
  turn's first delta creates a fresh Markdown widget.
- Working indicator: at turn start (when the prompt is sent), mount a
  `LoadingIndicator` (or themed animated Static) with a known id; remove it on
  the first `message` delta OR when the turn ends (whichever first). Helper
  `_show_working()` / `_hide_working()` — idempotent (safe to call when absent).

### `harness/tui/app.tcss` (modified)

Style the `Markdown` widget to sit flush in the transcript (no default margins
that fight the themed look), and style `#working` (the indicator). Keep the
existing `.compose` / statusbar / modal styles.

## Data flow

```
USER sends prompt
  → _send_prompt: _show_working(); await conn.prompt(...)
AGENT (chat_question): answer_stream yields pieces → message_chunk per piece
  → on_session_update("message"):
       first delta: _hide_working(); create+mount Markdown(""); buf=""
       each delta: buf += text; md.update(buf); scroll_end
  (tool call, if any): _append_line(tool); _streaming_md=None  (finalize block)
prompt(...) returns
  → _streaming_md=None; _hide_working(); _write_meta(elapsed)
```

Mock mode: one piece → one delta → Markdown widget updated once. Identical path.

## Error handling

- Stream raises mid-flight (proxy drop): the generator's caller catches it; the
  agent emits a final `message_chunk` with a short error note and returns a
  non-`end_turn` stop reason; the client finalizes the Markdown widget and the
  existing themed error/`— turn ended —` line shows. No crash.
- Empty stream (no pieces): no Markdown widget is created; `_hide_working()` runs
  at turn end; a muted "(no response)" line is appended. (Edge: implementer adds
  this only if reachable; mock always yields one piece.)
- Client mount/update exception: caught by the existing `_send_prompt`
  try/except → themed error line; input re-enabled in `finally` (unchanged).

## Testing

Run from worktree root: `<main-checkout>/.venv/bin/python -m pytest tests/`.
(The worktree has no own venv; tests already `sys.path.insert(".")` +
`upstream/src`. Pytest must target `tests/` — `upstream/tests` needs optional
deps and must not be collected.) Baseline: 100/100.

- `tests/test_chat_handler.py` (new): `answer_stream` with a monkeypatched
  litellm stream yields the pieces in order; mock mode (model_id None) yields
  exactly the honest one-liner as a single piece; `stream=True` is passed.
- `tests/test_tui_render.py` (unchanged core — render.py is untouched).
- `tests/test_tui_pilot.py` (extend): a fake-agent turn that emits MULTIPLE
  message deltas → assert a `Markdown` widget exists in the transcript and its
  source accumulates across deltas (not one-per-line); assert the working
  indicator appears after send and is gone after the turn; assert markdown
  source contains the concatenation. Mock/fake-agent driven (no live proxy).
- `tests/fake_agent.py` (extend): add a path that emits several
  `update_agent_message_text` chunks for one prompt so the pilot can exercise
  accumulation + the working indicator.
- Existing pilot tests that assert against `#transcript` as a `RichLog` and read
  `log.lines` must be migrated to query the `VerticalScroll` and read widget
  contents (Static renderables / Markdown source). This is required, not
  optional — the transcript type changed.

## Global Constraints

- **Zero upstream edits.** Nothing under `upstream/`.
- **STDOUT is the ACP wire** for the agent — no stray stdout prints in agent code
  paths (existing `MSWEA_SILENT_STARTUP=1` discipline).
- **No client-side threads.** The client stays async-all-the-way; only the AGENT
  uses worker threads (engine is blocking). Streaming pump runs on the agent's
  worker thread, marshalled to the loop — not the client.
- **Theme fidelity.** New widgets (Markdown, working indicator) match the
  existing DoneDone theme (navy+blue); no default Textual chrome clashing.
- **Mock mode needs no proxy.** Every test path is exercisable with `--model
  mock` / fake agent.
- **Single async loop on the client** (`run_worker(thread=False)` discipline
  from Phase 5 stands).

## Out of scope

- Streaming the agent-path (tool-using) assistant text token-by-token — the
  agent path emits at step boundaries; this spec streams chat answers. (The
  Markdown-widget rendering applies to any `message` kind, so if the agent path
  later emits incremental message chunks it benefits for free.)
- Syntax highlighting beyond what Textual's Markdown gives by default.
- Persisting/scroll-restoring rendered markdown across sessions.
