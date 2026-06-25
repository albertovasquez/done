# Phase 5 — Textual ACP Client (TUI) Design

**Status:** Design (pre-implementation)
**Date:** 2026-06-25
**Depends on:** Phase 4 (the engine is a working ACP agent at `trace/acp_main.py`).
**North-star reference (read-only):** Toad (`batrachianai/toad`) — Python/Textual/ACP, AGPL.
We study its patterns; we do **not** vendor or copy its code (license + it solves a
different, generic problem).

---

## Goal

Build a bespoke single-session Textual TUI that is an **ACP client** driving our
Phase-4 ACP agent over JSON-RPC/stdio, and that renders the one thing generic
ACP clients (Toad, Zed) drop: our custom `_meta["harness"]` stream
(`task_classified`, `skill_load`). That stream is the harness's signature — the
router classification and skill-injection visibility — so rendering it is the
entire reason to build our own client instead of pointing Zed at the agent.

## Why this, why now

Phase 4 inverted the architecture: the engine is an ACP **agent (server)**,
driven by editor clients. Toad proves the client half of that contract is real
and mature — but its RPC handlers accept `_meta` and **never read it** (verified
in `src/toad/acp/agent.py`). So a generic client runs our agent fine yet shows a
lobotomized view. The smallest thing that (a) teaches us the client half of ACP
and (b) surfaces our differentiator is a thin Textual client. The agent side is
done; this is "the same engine, our pixels."

## Non-goals (YAGNI — named so review does not flag them as gaps)

- **No concurrent/multiple sessions, no tabs.** One session, one cwd, one chat.
- **No agent picker / multiple backends.** We launch our agent only.
- **No web mode** (`toad serve` equivalent).
- **No builtin editor, no diff-apply UI, no sidebar/git widgets.**
- **No client-side terminal or filesystem rendering.** v1 advertises neither
  `terminal` nor `fs` capability; the agent falls back to its own
  `LocalEnvironment` (already proven by `test_terminal_fallback_uses_local_environment`).
- **No auto-reconnect** after agent death (manual restart).
- **No real-model TUI tests.** The agent side's `@needs_vibeproxy` smoke tests
  already cover real-model behavior; the TUI adds no new agent behavior.

These are deliberate later-iteration candidates, not omissions.

---

## Global Constraints

- **Zero upstream edits.** `upstream/` stays vendored unmodified. (HARD.)
- **Official SDK on both ends.** Client built on `agent-client-protocol`
  (imports as `acp`, v0.10.1; **never** `acp-sdk`). We do **not** roll our own
  JSON-RPC (Toad did; we have no reason to). Client surface used:
  `acp.Client` (Protocol), `acp.spawn_agent_process`, `acp.text_block`,
  `acp.PROTOCOL_VERSION`, `acp.schema.*`.
- **Version facts (pinned to avoid confusion):** pip package
  `agent-client-protocol == 0.10.1`; runtime `acp.PROTOCOL_VERSION == 1`; the
  generated `schema.py` carries an upstream schema ref `v0.12.2` in its header
  (the schema generator's tag, *not* the package version) — harmless, just don't
  mistake it for the installed version.
- **Textual 8.2.7** is already installed — no new heavyweight dependency.
- **Tests run as** `.venv/bin/python -m pytest tests/` — scoped to `tests/`,
  never bare `pytest` (it would walk `upstream/tests/`).
- **Demo-artifact discipline.** `examples/sample-repo/calculator.py` is a fixture
  that ships buggy (`return a - b`). Any manual demo that fixes it runs on a
  temp copy; the fixture is restored.
- **The agent runs as a separate subprocess**, launched via
  `spawn_agent_process`, so the ACP boundary stays real (the TUI drives the agent
  exactly as Zed would). The only exception is the in-process fake agent used in
  pilot tests, for speed.

---

## Architecture

Three-layer split + entrypoint, mirroring the separation that made Phase 4
testable (pure `acp_emit.py` / glue `acp_agent.py`):

```
trace/tui_main.py      entrypoint: argparse (--model, --cwd) → HarnessTui(...).run()
trace/tui/app.py       Textual App: widgets, agent-process lifecycle, message handlers
trace/tui/client.py    our acp.Client implementation (callbacks → app.post_message / Future)
trace/tui/render.py    PURE: update → RenderedItem; field_meta → chips; status → style
trace/tui/app.tcss     Textual stylesheet (layout + status/chip colors) — ships with app.py
```

Dependency direction (no cycles):
```
tui_main → app → {client, render, acp}
client  → acp  (+ holds an app handle for post_message; does NOT import render/widgets)
render  → stdlib only  (duck-types acp update objects via attributes)
```

The concurrency story is a single triad, all on Textual's own asyncio loop:
- **single loop** — the ACP connection lives inside Textual's loop; no worker
  thread (unlike the agent side, which needed a thread bridge because the engine
  is blocking/sync — the client is async all the way down);
- **worker-for-prompt** — `conn.prompt` is awaited inside an **async** Textual
  worker (`run_worker(..., thread=False)`, the default — a thread worker would
  spin up a *separate* event loop via `asyncio.run()` and break the single-loop
  design). The worker exists to keep Textual's message handling responsive while
  the long `prompt` coroutine is in flight, not because `prompt` blocks the
  asyncio loop (it doesn't — the SDK's receive/dispatch run as background tasks
  on the same loop and deliver `session/update`s concurrently);
- **Future-for-permission** — `request_permission` posts a modal and `await`s a
  Future the modal's button resolves.

---

## Components

### 1. `trace/tui/render.py` — pure render core (the differentiator)

No Textual import, no network, no async. Turns ACP updates into display-ready
values. Tested exhaustively with plain stubs.

```python
@dataclass(frozen=True)
class RenderedItem:
    kind: str                 # "message" | "thought" | "user" | "tool" | "tool_update"
    text: str = ""            # message/thought/user body
    id: str = ""              # tool_call_id (for tool / tool_update correlation)
    title: str = ""           # "$ <command>"  (tool)
    status: str = ""          # "pending"|"in_progress"|"completed"|"failed"
    body: str = ""            # tool output (tool_update)

def render_update(update) -> RenderedItem | None
    # dispatch on type(update).__name__:
    #   AgentMessageChunk  -> RenderedItem("message", text=update.content.text)
    #   AgentThoughtChunk  -> RenderedItem("thought", text=update.content.text)
    #   UserMessageChunk   -> RenderedItem("user",    text=update.content.text)
    #   ToolCallStart      -> RenderedItem("tool", id=update.tool_call_id,
    #                                       title=update.title, status=str(update.status))
    #   ToolCallProgress   -> RenderedItem("tool_update", id=update.tool_call_id,
    #                                       status=str(update.status), body=<first text content or "">)
    #   anything else (plan, current_mode_update, ...) -> None   (forward-compat)

def harness_chips(field_meta: dict | None) -> list[str]
    # reads field_meta["harness"]; returns chip strings (the bit Toad/Zed drop):
    #   {"task_classified": {"task_type": t, "skills": [...], "confidence": c}}
    #       -> "classified: {t} · skills: {', '.join(skills) or '—'} · conf: {c:.2f}"
    #   {"skill_load": {"injected": [...], "skipped": [...]}}
    #       -> "skills: {len(injected)} loaded, {len(skipped)} skipped"
    # returns [] for None / {} / missing-or-malformed keys — NEVER raises.

def status_style(status) -> str
    # "pending"->"yellow", "in_progress"->"blue", "completed"->"green", "failed"->"red"
    # accepts the enum OR its stringified form ("ToolCallStatus.failed") — the smoke
    # tests showed status arrives in both shapes. Default -> "white".
```

**`field_meta` shape (pinned, verified against running code).** Our agent emits
(in `trace/acp_agent.py` via `acp_emit.with_meta`):
`update.field_meta == {"harness": {"task_classified": {"task_type", "skills", "confidence"}}}`
on the post-classify update, and
`update.field_meta == {"harness": {"skill_load": {"injected", "skipped"}}}`
on the post-compose update. `harness_chips` reads exactly these. A render unit
test asserts both produce their pinned chip strings; if a future SDK/agent change
alters the shape, that test fails loudly rather than silently dropping the one
feature that makes this client ours.

### 2. `trace/tui/client.py` — our `acp.Client` implementation

Evolves `_CollectingClient` from `tests/test_acp_smoke.py`: instead of appending
to a list, each callback hands the update to the app. Knows ACP + the app handle;
knows nothing about widgets or styling.

```python
class TuiClient:                          # implements the acp.Client Protocol
    def __init__(self, app): self._app = app

    async def session_update(self, session_id, update, **kw) -> None:
        self._app.post_message(SessionUpdate(update))          # marshal; render later

    async def request_permission(self, options, session_id, tool_call, **kw):
        fut = asyncio.get_running_loop().create_future()
        self._app.post_message(PermissionRequest(options, tool_call, fut))
        option_id = await fut                                  # suspends; loop stays live
        if option_id:
            # AllowedOutcome REQUIRES the discriminator outcome="selected" — omitting it
            # raises pydantic ValidationError (verified against schema.py). DeniedOutcome's
            # discriminator is outcome="cancelled".
            return RequestPermissionResponse(
                outcome=AllowedOutcome(outcome="selected", option_id=option_id))
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))

    # Protocol completeness — benign defaults (no fs/terminal capability advertised in v1):
    async def read_text_file(self, *a, **k): return None
    async def write_text_file(self, *a, **k): return None
    async def create_terminal(self, *a, **k): return None
    async def terminal_output(self, *a, **k): return None
    async def wait_for_terminal_exit(self, *a, **k): return None
    async def release_terminal(self, *a, **k): return None
    async def kill_terminal(self, *a, **k): return None
    async def ext_method(self, method, params): return {}
    async def ext_notification(self, method, params): return None
    def on_connect(self, conn): pass
```

`SessionUpdate` and `PermissionRequest` are `textual.message.Message` subclasses
defined in `app.py` (or a small `messages.py`) — the typed handoff between client
callbacks and the app.

### 3. `trace/tui/app.py` — the Textual App (shell)

- **Widgets:** a `RichLog` transcript (messages, tool-call lines, harness chips),
  a single-line `Input`, a header showing `cwd` + model.
- **Lifecycle (`on_mount`):** `spawn_agent_process(self.client, *agent_cmd)` →
  `initialize(client_capabilities=ClientCapabilities(elicitation=ElicitationCapabilities()))`
  → `new_session(cwd)`; hold `conn` + `session_id`. `on_unmount` tears down (the
  context manager kills the subprocess) and resolves any pending permission Future
  to reject.
- **Input flow:** submit → write a "you:" line → disable Input →
  `run_worker(self._send_prompt(text), thread=False)` (async worker on the app loop).
- **`_send_prompt`:** `await conn.prompt([text_block(text)], session_id)`; on
  return, render an end-of-turn marker if `stop_reason != "end_turn"`; re-enable
  Input. Wrapped in try/except (see Error Handling).
- **`on_session_update`:** `for c in harness_chips(update.field_meta): log chip`;
  then `item = render_update(update)`. **Tool-line correlation — corrected:**
  `RichLog.write()` only appends; it returns the widget, **not** a line handle,
  so there is no way to mutate an already-written line. So `ToolCallStart`
  appends `"$ <cmd>  pending …"` and `ToolCallProgress` **appends a follow-up
  line** `"  → completed ✓"` / `"  → failed ✗"` (correlated visually by adjacency,
  and by `tool_call_id` in the text). This is the v1 choice: append, don't
  update-in-place. (A keyed-row widget for true in-place status flips is a clean
  later iteration; not worth a custom widget now.)
- **`on_permission_request`:** `push_screen(PermissionModal(options, tool_call),
  callback=lambda chosen: fut.set_result(chosen))`.
- **`PermissionModal(ModalScreen)`:** shows `$ <cmd>` + `[Allow once] [Reject]`;
  a button press `dismiss(option_id_or_None)`.
- **Cancel:** an `esc` binding → `await conn.cancel(session_id)` (agent's
  best-effort, command-boundary cancel from Phase 4).

### 4. `trace/tui_main.py` — entrypoint

```python
# --model mock|vibeproxy   --cwd <path> (default ".")
# AGENT_CMD = [sys.executable-equiv .venv/bin/python, trace/acp_main.py, --model, <model>]
# HarnessTui(agent_cmd=AGENT_CMD, cwd=<abs cwd>, model=<model>).run()
```
Mirrors `acp_main.py`'s arg surface. The agent is a real subprocess.

---

## Presentation (folded in; no separate styles file)

A short, pinned presentation contract. Cosmetic layout lives in
`trace/tui/app.tcss` (≈20–40 lines: log fills height, input pinned bottom, modal
centered) and is written alongside `app.py` — it is **not** a planning artifact
and there is **no** `styles.md` (single-screen app; a design-system doc is
premature). What IS pinned here, because it is testable and differentiating:

- **Status → color** (used by `status_style`, and the `.tcss`):
  `pending=yellow`, `in_progress=blue`, `completed=green`, `failed=red`,
  default=`white`.
- **Chip formats** (so `harness_chips` is unit-testable against exact output):
  - classified: `"classified: {task_type} · skills: {skills_joined_or_—} · conf: {confidence:.2f}"`
  - skill load: `"skills: {n_injected} loaded, {n_skipped} skipped"`
  - Chips render visually distinct from messages (dim/bracketed style in `.tcss`).
- **Tool line:** `"$ {command}"` with a trailing status glyph
  (`pending …`, `completed ✓`, `failed ✗`) colored by `status_style`.

---

## Data Flow

**Startup (once):** `tui_main → App.run() → on_mount`: spawn agent subprocess →
`initialize(elicitation)` → `new_session(cwd)` → header shows cwd+model, Input
focused.

**Normal turn:**
1. User submits prompt → "you:" line, Input disabled, `run_worker(_send_prompt)`.
2. `_send_prompt`: `await conn.prompt(...)` (returns only at turn end).
3. Concurrently on the same loop, agent streams `session/update`s →
   `TuiClient.session_update` → `post_message(SessionUpdate(update))`.
4. `on_session_update`: render chips (from `field_meta`) + the update
   (`render_update`). Typical code-fix order: task_classified chip →
   skill_load chip → `ToolCallStart` (yellow) → `ToolCallProgress` (green ✓) →
   agent text → … 
5. `prompt` returns `stop_reason` → end-of-turn marker if not `end_turn`; Input
   re-enabled.

**Why the worker matters:** `conn.prompt` is a long-lived coroutine (returns only
at turn end). The SDK's receive/dispatch tasks run as background tasks on the
*same* loop, so `session/update`s arrive concurrently regardless. The worker
(`thread=False`) exists so the input handler returns immediately and Textual
keeps processing posted messages (rendering, modal) while the turn streams —
awaiting `prompt` inline in the handler would tie up that handler for the whole
turn. (A `thread=True` worker would create a separate event loop and is wrong here.)

**Permission round-trip:** agent calls `request_permission` → client creates a
Future, posts `PermissionRequest`, `await`s (loop stays responsive) → app pushes
modal → user clicks → `dismiss(option_id)` → callback `fut.set_result(...)` →
client resumes, returns `AllowedOutcome(outcome="selected", option_id=...)` or
`DeniedOutcome(outcome="cancelled")` → Allow streams tool calls; Reject yields a
`failed` (red) `ToolCallProgress` from the agent's `AcpEnvironment`.

**Cancel:** `esc` → `await conn.cancel(session_id)` → agent skips next command at
the boundary → in-flight prompt returns `stop_reason="cancelled"`.

---

## Error Handling

Governing principle: **the TUI is long-lived; a failed turn never kills it.**
Four failure surfaces:

1. **Startup / handshake failure** (agent missing, import crash, protocol
   mismatch): `on_mount` wraps spawn+initialize+new_session in try/except; on
   failure, write `could not start agent: <err>` to the transcript and disable
   Input (do not let the exception escape into Textual's loop and wreck the
   terminal). App stays open so the user can read and quit.
2. **Turn failure** (subprocess dies / `prompt` raises mid-turn): `_send_prompt`
   try/except → red `agent disconnected — restart to continue` line, re-enable
   Input, end worker. App survives (no auto-reconnect in v1).
3. **Agent-level refusal** (router unreachable, engine exception): arrives
   *through the protocol* as a `message_chunk` + `stop_reason="refusal"`, not as
   a Python exception. Nothing special to catch — renders as a normal agent line;
   `_send_prompt` adds an end-of-turn marker. (We designed the agent to make
   failures legible over the wire; the client just displays them.)
4. **Pending permission Future on shutdown:** `on_unmount` cancels the prompt
   worker and resolves any pending permission Future to reject, so the agent gets
   a clean denial rather than a dangling RPC; the context manager kills the
   subprocess.

Defensive: `harness_chips` returns `[]` on any missing/malformed key.
Not handled (named, YAGNI): auto-reconnect, partial-stdio recovery (SDK parser
raises → treated as case 2), concurrent prompts (Input disabled during a turn).

---

## Testing

Strategy: **pure render units (many, fast, no loop) + 1–2 pilot smokes (real
Textual, fake in-process agent).**

### `tests/test_tui_render.py` — pure, no event loop, no network
- `render_update` for AgentMessageChunk / UserMessageChunk / AgentThoughtChunk →
  correct kind + text.
- `render_update` for ToolCallStart → `RenderedItem("tool", id, title, status)`.
- `render_update` for ToolCallProgress → `("tool_update", id, status, body)`.
- `render_update` for an unknown type (plan stub) → `None`.
- `status_style` for all four statuses in **both** shapes (enum and
  `"ToolCallStatus.failed"` string).
- `harness_chips` for task_classified → exact pinned chip string.
- `harness_chips` for skill_load → exact "N loaded, M skipped" string.
- `harness_chips(None)` / `({})` / malformed nested keys → `[]`, never raises.

### `tests/test_tui_pilot.py` — 1–2 tests via `App.run_test()`, fake in-process agent
A ~15-line `acp.Agent` whose `prompt` emits one `AgentMessageChunk` carrying
`field_meta={"harness": {"task_classified": {...}}}` then returns `end_turn`.
No VibeProxy, no real model. **Primary plan: a tiny fake-agent *script* launched
via `spawn_agent_process`** — the proven path (`stdio.py`, and
`tests/test_acp_smoke.py:309`+). `ClientSideConnection` requires real
`asyncio.StreamReader`/`StreamWriter`, so an in-memory stream pair would need a
helper we don't have; we use a subprocess instead (the agent it launches is the
fake, not `acp_main.py`, so it's still fast and deterministic). An in-memory
stream-pair helper is an optional optimization, not the plan.
- **Smoke 1 (required) — end-to-end render:** boot → pilot types a prompt +
  Enter → wait for turn → assert transcript contains the agent text **and** the
  harness chip ("classified: …"). Proves input → client → post_message → render
  → widget, including the differentiator.
- **Smoke 2 (optional) — permission round-trip:** fake agent calls
  `request_permission` → assert modal appears → pilot clicks Reject → assert the
  Future resolves and the agent receives a denial. Include if stable; drop to the
  render assertion if pilot timing is flaky.

### Not automated
Visual layout/colors (manual `tui_main.py --model mock`); real-model turns (the
agent side's `@needs_vibeproxy` tests already cover them; manual `--model
vibeproxy` on a temp fixture copy during the demo).

**Suite:** current 65 + render units + pilots; run as
`.venv/bin/python -m pytest tests/`.

---

## File Manifest

| File | New/Mod | Responsibility |
|---|---|---|
| `trace/tui/__init__.py` | new | package marker |
| `trace/tui/render.py` | new | pure update→RenderedItem, field_meta→chips, status→style |
| `trace/tui/client.py` | new | `acp.Client` impl: callbacks → post_message / Future |
| `trace/tui/app.py` | new | Textual App: widgets, lifecycle, handlers, PermissionModal |
| `trace/tui/app.tcss` | new | layout + status/chip colors |
| `trace/tui_main.py` | new | entrypoint (`--model`, `--cwd`) |
| `tests/test_tui_render.py` | new | pure render unit tests |
| `tests/test_tui_pilot.py` | new | 1–2 Textual pilot smokes (fake agent) |
| `README.md` / `docs/learning-log.md` | mod | Phase 5 entry |

## Success Criteria

1. `.venv/bin/python trace/tui_main.py --model mock` opens a usable chat loop:
   type a prompt, see streaming messages + tool-call lines + **harness chips**.
2. Permission prompts surface as a modal; Allow/Reject round-trips correctly.
3. `harness_chips`/`render_update`/`status_style` are pure and unit-tested,
   including both `status` shapes and malformed `field_meta`.
4. ≥1 pilot smoke proves an end-to-end turn renders the harness chip.
5. Full suite green via `.venv/bin/python -m pytest tests/`; agent side unchanged.
6. A turn failure (kill the agent) degrades to a transcript line, not a crash.
