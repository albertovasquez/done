# Phase 4 — ACP Agent — Design

**Status:** design (pre-plan)
**Date:** 2026-06-25
**Depends on:** Phase 0 (tracer), Phase 1 (AgentRunner), Phase 2 (Router), Phase 3 (skills) — all merged to `main`.

## Goal

Make the harness engine a valid **Agent Client Protocol (ACP) agent** — a
JSON-RPC 2.0 server over stdio that editor/TUI clients (Zed, our test client,
later Toad) launch and drive. This turns the engine into a headless service
speaking a real standard, reusing Phases 0–3 unchanged underneath. The full
agent (permissions, fs/terminal delegation, session resume) is the target,
delivered in four independently-reviewable layers.

## Why ACP (decisions settled during brainstorming, 2026-06-25)

- ACP is the standardized contract between a coding-agent backend and an
  editor/TUI frontend (by Zed Industries; consumed by Zed, JetBrains, VS Code,
  Neovim, Emacs). Targeting it means our engine plugs into a real ecosystem
  instead of a bespoke protocol — the purest expression of the project's
  founding principle ("borrow the pattern once you understand why it exists").
- It is the standardized form of the runner↔client boundary the harness has
  built toward since Phase 0 ("everything becomes an event"). ACP is simply a
  second consumer of the event stream, alongside the existing `events.jsonl`.
- **Architectural inversion:** the engine becomes the ACP **agent (server)**; a
  **client** launches it as a subprocess and drives it over stdio. This is the
  opposite direction from today's "CLI drives the engine."
- **Official Python SDK** (`agent-client-protocol`, v0.10.1, by Zed) does the
  stdio JSON-RPC plumbing and provides typed Pydantic models — we do not
  hand-roll the wire protocol. Package imports as `acp`. **NOT `acp-sdk`** —
  that is a different "Agent Communication Protocol" (name collision).
- **Full agent target, layered delivery:** one spec, one plan; each layer is its
  own task with its own test gate.

## Global Constraints

- **Zero upstream edits.** Nothing under `upstream/` changes. The ACP layer is a
  new adapter; Router/skills/runner/tracer/LocalEnvironment are reused as-is.
- **Phases 0–3 untouched.** `router.py`, `skills.py`, `runner.py`,
  `tracing_agent.py`, `events.py` are not modified by this phase (the adapter
  consumes them). All existing tests stay green.
- **Python ≥ 3.10**, run via `.venv/bin/python`. Tests run as
  `.venv/bin/python -m pytest tests/` — scope to `tests/`, NOT bare `pytest`
  (bare collection walks `upstream/tests/` and errors on optional deps).
- **New dependency:** `agent-client-protocol` (the Zed SDK, imports as `acp`),
  installed into `.venv`. Add to README setup. Do NOT install `acp-sdk`.
- **STDOUT IS THE WIRE.** Under the ACP agent, nothing may write to stdout
  except JSON-RPC. All console/event output (today `Emitter._print_console` →
  `print()`) must go to **stderr** or the `events.jsonl` file when running as an
  ACP agent. This is a hard rule; a stray stdout write corrupts the protocol.
- **A turn never crashes the process.** Every `session/prompt` resolves with a
  `stopReason` (or a JSON-RPC error for protocol-level faults), never silence
  and never an unhandled exception that kills the long-lived process.
- **Test style** (upstream `AGENTS.md`): pytest, no mocking unless required, real
  files via `tmp_path`, non-trivial assertions. Reuse `build_mock_model` for
  deterministic, offline, free integration tests.

## SDK facts (verified against PyPI + the python-sdk repo)

- `pip install agent-client-protocol` → imports as `acp`. `requires_python
  >=3.10,<3.15` (our venv is 3.11 ✓).
- Subclass `acp.Agent`. Implement async methods:
  `initialize(self, protocol_version, client_capabilities=None, client_info=None, **kw) -> InitializeResponse`,
  `new_session(self, cwd, additional_directories=None, mcp_servers=None, **kw) -> NewSessionResponse`,
  `prompt(self, prompt, session_id, **kw) -> PromptResponse`,
  `load_session(self, cwd, session_id, ...) -> LoadSessionResponse | None`,
  `authenticate(...)` (optional), `cancel`/cancellation handling.
- `on_connect(self, conn: Client)` gives the agent a handle to call BACK into the
  client (`await self._conn.session_update(session_id, update)`, and—Layer 2+—
  permission / fs / terminal requests).
- Helpers: `run_agent(agent)` (the stdio main loop), `text_block(text)`,
  `update_agent_message(content_block)`; `acp.helpers` builds tool calls and
  session updates; `acp.schema` holds the Pydantic models (`AgentCapabilities`,
  `ClientCapabilities`, `AgentMessageChunk`, content blocks, `Implementation`,
  `PROTOCOL_VERSION`, etc.).
- `_meta` is carried via the model's `field_meta` attribute (seen in
  `echo_agent.py`: `chunk.field_meta = {...}`).

## Architecture

> **Design correction (post-Codex review):** the ACP layer is NOT an
> event-stream translator. The existing Phase-0 events are *lossy* — `llm.return`
> carries only a 120-char `content_preview` (tracing_agent.py:88) and
> `action.done` carries only `returncode`/`output_bytes`, not the command output
> (tracing_agent.py:109). Translating those into ACP would stream truncated,
> wrong content. Worse, `action` is emitted and the command executed in the same
> breath (tracing_agent.py:100-102), so an event consumer can never gate
> permission *before* execution; and the Phase-1 runner has no cooperative
> cancellation (runner.py:14-16). Therefore the ACP layer reaches the engine's
> REAL data by **wrapping the engine's interfaces** (the `Environment`, and the
> agent's own messages), not by reading the lossy event stream. Phases 0–3 stay
> byte-for-byte unchanged; `events.jsonl` still flows as a separate sink.

```
  ACP client (Zed / our smoke client / later Toad)
        │  launches subprocess; JSON-RPC 2.0 over stdio (stdin/stdout)
        ▼
  ┌──────────────────────────────────────────────────────┐
  │  harness ACP agent (NEW)                               │
  │   trace/acp_agent.py   — HarnessAgent(acp.Agent)       │
  │   trace/acp_env.py     — AcpEnvironment(LocalEnvironment)│
  │                          the seam: full output +        │
  │                          pre-exec permission + cancel    │
  │   trace/acp_emit.py    — build ACP session/update objects│
  │   trace/acp_session.py — SessionStore / SessionState     │
  │   built on the `acp` SDK (stdio JSON-RPC + models)       │
  └──────────────────────────────────────────────────────┘
        │  reuses UNCHANGED:
        ▼  Router (P2) · skills.compose (P3) · MiniSweAgentRunner→TracingAgent (P1)
           · LocalEnvironment (subclassed, not edited) · Emitter→events.jsonl
```

The ACP agent is an **interface-wrapping adapter**, not an event translator. The
single most important seam is `AcpEnvironment`, a subclass of `LocalEnvironment`
whose `execute()` (a) checks the cancel flag, (b) — Layer 2 — requests permission
BEFORE calling `super().execute()`, and (c) returns the FULL output dict (which
it then surfaces as ACP `tool_call`/`tool_call_update`). Model text comes from
the agent's actual messages (full content), not the preview event.
`events.jsonl` keeps flowing underneath (local observability); ACP carries the
client-facing stream. `run_traced.py` remains the existing standalone/demo
entrypoint (not deleted).

### Layered delivery
1. **Minimal agent + the seam** — `initialize`/`new_session`/`prompt`; stream
   `agent_message_chunk` (full text) + shell actions as `tool_call`/
   `tool_call_update` (full output) via `AcpEnvironment`; Router + ChatHandler run
   in an executor (don't block the loop); skills injected; `_meta` carries
   task.classified + skill.load; **best-effort cancel** (see Cancellation).
   `AcpEnvironment` exists from Layer 1 (permission hook added in L2). Prove a
   client (smoke client; ideally Zed) drives it.
2. **Permissions** — `AcpEnvironment.execute` calls `session/request_permission`
   BEFORE `super().execute()`; reject → skip + `tool_call_update{failed}`.
3. **fs/terminal delegation** — capability-gated `fs/*` and `terminal/*` inside
   `AcpEnvironment`, with `super().execute()` (LocalEnvironment) fallback.
4. **Session resume** — `session/load` replays stored history as
   `session/update`s (history recorded from Layer 1).

## Components

### 1. `trace/acp_agent.py` (NEW — the agent/server)

`HarnessAgent(acp.Agent)`. Owns per-turn orchestration:
```python
class HarnessAgent(acp.Agent):
    def __init__(self, *, model_factory, agent_cfg, skills_dir, router): ...
    def on_connect(self, conn): self._conn = conn
    async def initialize(self, protocol_version, client_capabilities=None,
                         client_info=None, **kw) -> InitializeResponse: ...
    async def new_session(self, cwd, additional_directories=None,
                          mcp_servers=None, **kw) -> NewSessionResponse: ...
    async def prompt(self, prompt, session_id, **kw) -> PromptResponse:
        # 1 turn: classify → _meta → dispatch (chat/ambiguous/agent) → stopReason
    async def cancel(self, session_id, **kw) -> None: ...      # cooperative; confirm exact
                                                               # cancel hook name against the SDK
                                                               # (session/cancel is a notification)
    async def load_session(self, cwd, session_id, **kw): ...   # Layer 4
```
`prompt()` runs the Router (in an executor — see §5), emits
`_meta(task.classified)`, then branches:
- **chat_question** → `ChatHandler.answer` (in an executor) → `agent_message_chunk`
  (full text) → `stopReason:end_turn`.
- **ambiguous** → clarifying question as `agent_message_chunk` →
  `stopReason:end_turn` (the user's next prompt is the clarification — ACP makes
  this naturally multi-turn, replacing today's blocking `input()`).
- **code_*/ops** → `skills.compose` → `_meta(skill.load)` → build the agent with
  an `AcpEnvironment` (the seam) and run it; the env surfaces each command as
  `tool_call`/`tool_call_update` with FULL output, and the agent's final
  message text becomes `agent_message_chunk`s; `stopReason:end_turn`.

### 2. `trace/acp_env.py` (NEW — THE SEAM: `AcpEnvironment(LocalEnvironment)`)

The load-bearing component. A subclass of upstream `LocalEnvironment` (subclass,
not edit — honors zero-upstream-edits) whose `execute()` wraps the real
execution so the ACP layer gets what the lossy events cannot give:
```python
class AcpEnvironment(LocalEnvironment):
    def __init__(self, *, on_command, request_permission, cancel_flag, **kw):
        super().__init__(**kw)
        self._on_command = on_command          # callback: stream tool_call/_update to ACP
        self._request_permission = request_permission  # Layer 2: None in L1 (auto-allow)
        self._cancel = cancel_flag             # threading.Event checked here
    def execute(self, action, cwd="", *, timeout=None) -> dict:
        if self._cancel.is_set():              # cooperative cancel checkpoint (see Cancellation)
            return {"output": "", "returncode": -1, "exception_info": "cancelled"}
        cmd = action.get("command", "")
        self._on_command("start", cmd)         # → tool_call {kind:execute, pending}
        if self._request_permission and not self._request_permission(cmd):  # Layer 2
            self._on_command("rejected", cmd)  # → tool_call_update {failed}
            return {"output": "permission denied", "returncode": -1, "exception_info": "rejected"}
        out = super().execute(action, cwd, timeout=timeout)   # REAL run; FULL output dict
        self._on_command("done", cmd, out)     # → tool_call_update {completed|failed, FULL output}
        return out
```
This single seam solves all three Codex CRITICALs: full command output (the
return dict, not `output_bytes`), pre-exec permission (before `super().execute()`),
and a cancel checkpoint. `_on_command`/`_request_permission` bridge to the async
ACP loop via a thread-safe call (the engine runs on Phase 1's worker thread; these
callbacks marshal to the event loop — see §4). The `Submitted` exception
super().execute() may raise propagates unchanged (preserves the submit flow).

Full MODEL text: taken from the agent's `messages` (the real content), not the
120-char `llm.return` preview event. Layer 1 surfaces the agent's final/assistant
text as `agent_message_chunk`(s).

### 3. `trace/acp_emit.py` (NEW — ACP update builders)

Small pure builders (unit-testable without a JSON-RPC loop) wrapping
`acp.helpers`: `tool_call_start(cmd) -> ToolCall{kind:execute,status:pending}`,
`tool_call_done(cmd, output_dict) -> ToolCallUpdate{status:completed|failed (rc≠0),
content:[full output as terminal/text]}`, `message_chunk(text) ->
update_agent_message(text_block(text))`, and `with_meta(update, harness_meta)` to
attach `task.classified`/`skill.load` via `field_meta`. (This is the only place
that knows ACP's update shapes; `acp_env`/`acp_agent` call these.)

### 4. `trace/acp_session.py` (NEW — session state)

`SessionStore` maps `session_id -> SessionState`. `SessionState` holds `cwd`,
the conversation `history` (turn records, recorded from Layer 1 so Layer 4 resume
works), a per-session `cancel_flag` (`threading.Event`), and engine wiring
(model, agent_cfg). Minimal in Layer 1; `history` is what Layer 4's
`session/load` replays.

### 4b. Async↔sync bridge & the executor (Layer 1, inside `acp_agent.py`)

`prompt()` is async; the engine is synchronous and blocking. Run the WHOLE engine
turn (agent.run with the `AcpEnvironment`) in a thread via
`loop.run_in_executor`/`asyncio.to_thread`. The env's `_on_command` callback,
fired on that worker thread, marshals updates back to the loop with
`asyncio.run_coroutine_threadsafe(self._conn.session_update(...), loop)` and
`.result()` (so a permission round-trip can block the worker until the client
answers). **Router.classify and ChatHandler.answer also run via the executor** —
they call synchronous `litellm.completion` (router.py:39, chat_handler.py:24) and
would otherwise block the event loop (Codex IMPORTANT #2). This keeps the JSON-RPC
loop responsive (e.g. to receive `session/cancel`).

### 5. Permission gate (Layer 2 — extends `AcpEnvironment`)

`AcpEnvironment._request_permission` (None/auto-allow in L1) is wired to call the
client `session/request_permission` BEFORE `super().execute()`, with options
(`allow_once`/`allow_always`/`reject_once`/`reject_always`). Reject → skip the
command, `tool_call_update{failed}`. If the client did not advertise permission
support: auto-allow (preserves standalone path). Because the gate lives inside
`execute()` (before the real run), it actually prevents execution — which an
event-stream consumer could not (Codex CRITICAL #2).

### 6. fs/terminal delegation (Layer 3 — extends `AcpEnvironment`)

Capability-gated, inside `AcpEnvironment`. If `clientCapabilities` advertised
`fs`/`terminal`, route file I/O via `fs/read_text_file`/`fs/write_text_file` and
command execution via `terminal/*`; otherwise `super().execute()`
(LocalEnvironment) — the working fallback. Additive to the same seam.

### 7. `trace/acp_main.py` (NEW — agent entrypoint)

Thin launcher. CRITICAL ordering (Codex IMPORTANT #3): set
`os.environ["MSWEA_SILENT_STARTUP"] = "1"` BEFORE any `minisweagent` import
(upstream prints a banner to stdout on import — `__init__.py:30` — which would
corrupt the JSON-RPC wire). Construct the `Emitter` with `console=False` (its
`_print_console` writes to stdout — events.py:78); the JSONL file sink still
records the run. Never pass `echo=print`. Then wire the model factory
(mock/vibeproxy via env), agent_cfg, skills_dir, Router, and
`asyncio.run(run_agent(HarnessAgent(...)))`. Note: `LocalEnvironment` already
pipes subprocess stdout (`stdout=PIPE, stderr=STDOUT` — local.py:82-83), so shell
command output does NOT reach the agent's stdout — verified, not a corruption
vector.

### 8. `trace/acp_client_smoke.py` (NEW — tiny test client)

A minimal ACP client that launches the agent subprocess and drives
`initialize`→`new_session`→`prompt`, collecting the `session/update` stream for
assertions. Enables CI testing without Zed.

### 9. Reused unchanged
`router.py`, `skills.py`, `runner.py`, `tracing_agent.py`, `events.py`,
`chat_handler.py`, `models_mock.py`, `run_traced.py`. `LocalEnvironment` is
*subclassed* by `AcpEnvironment` (not edited). Router/ChatHandler call sites are
unchanged — they're just invoked via an executor from `acp_agent`.

## Data flow — one `session/prompt` turn (agent path, with permission)

```
client → session/prompt {sessionId, prompt:[text]}
  acp_agent.prompt()  (async):
    load SessionState
    classify = await loop.run_in_executor(Router.classify, text)   [router.py, off-loop]
    await session_update(_meta: task.classified)
    branch:
      chat      → await run_in_executor(ChatHandler.answer)
                  → session_update(agent_message_chunk, full text) → return end_turn
      ambiguous → session_update(agent_message_chunk: clarifying Q) → return end_turn
      code_*    → load = skills.compose [skills.py]
                  await session_update(_meta: skill.load)
                  build agent with AcpEnvironment(on_command, request_permission,
                                                  cancel_flag, cwd, skill_block)
                  await run_in_executor( agent.run )            [runs on worker thread]
                    AcpEnvironment.execute(cmd):   (on the worker thread)
                      if cancel_flag set → return cancelled dict
                      on_command("start") ── run_coroutine_threadsafe ──►
                        session_update(tool_call {execute, pending})
                      [L2] request_permission(cmd) ── threadsafe ──►
                        session/request_permission ; reject → failed, skip
                      out = super().execute(cmd)   # REAL run, FULL output dict
                      on_command("done", out) ── threadsafe ──►
                        session_update(tool_call_update {completed|failed, FULL output})
                    agent's assistant text → session_update(agent_message_chunk, full text)
                  record turn in SessionState.history
                  return PromptResponse{stopReason: end_turn}
client ← (stream of session/update) … ← PromptResponse{stopReason}
```

**Ordering guarantee:** `_meta(task.classified)` is sent right after classify,
before any dispatch content. Because each `await session_update(...)` completes
before the next, and the final `PromptResponse` returns only after
`run_in_executor` resolves, updates are flushed before the response (the smoke
test asserts raw receive order — Codex MINOR #2).

**Cancellation (HONEST, post-Codex):** Phase-1's runner has NO cooperative
cancellation (runner.py:14-16: blocking `gen.close()`), so we cannot interrupt a
worker stuck inside `litellm` or a long shell command. Phase-4 cancel is
therefore **best-effort at command boundaries**: `session/cancel` sets the
session's `cancel_flag` (`threading.Event`); `AcpEnvironment.execute` checks it at
the TOP of each command and, if set, returns a cancelled dict instead of running
— so the agent loop unwinds at the next command boundary and `prompt()` resolves
`stopReason:"cancelled"`. An in-flight LLM call or a single already-running
command still completes first. This is documented as a known limitation; true
mid-call cancellation would need a cancellable model/runner seam (a candidate for
a later phase). The spec does NOT claim instant cancellation.

## Error handling

| Failure | Behavior |
|---|---|
| Engine raises mid-turn | catch in `prompt()`; `agent_message_chunk` with the error; resolve `stopReason:"refusal"`. Process stays alive. |
| `session/cancel` mid-turn | set cancel_flag; best-effort cancel at next command boundary; resolve `stopReason:"cancelled"`. In-flight LLM call / running command completes first (documented limitation). Never an exception to the client. |
| Router unreachable (VibeProxy down) | `agent_message_chunk` explaining it; `stopReason:"refusal"`. |
| Unknown `sessionId` | JSON-RPC error (invalid params) via SDK; agent keeps running. |
| Client lacks fs/terminal/permission capability | `AcpEnvironment` falls back to `super().execute()` / auto-allow. Never call an unadvertised client method. |
| Non-text prompt block (image/audio) we don't handle | extract text blocks; note unsupported types in an `agent_message_chunk`; don't crash. |
| `cancel` for an already-finished turn | no-op. |
| Stray stdout write | PREVENTED: `MSWEA_SILENT_STARTUP=1` before imports, `Emitter(console=False)`, no `echo=print`. LocalEnvironment pipes subprocess stdout (local.py:82-83), so command output is not a vector. |

## Testing

**Task 0 / Layer-1 first step — SDK contract test `tests/test_acp_sdk_contract.py`**
(Codex MINOR #1: the SDK is not yet installed/verified in this workspace).
Install `agent-client-protocol` into `.venv`; a tiny test imports `acp` and
asserts the API the spec relies on exists: `acp.Agent`, `run_agent`, `text_block`,
`update_agent_message`, `PROTOCOL_VERSION`, and that an `AgentMessageChunk`
exposes a `field_meta` attribute. This pins the real API before adapter work and
fails loudly if a version bump moves things.

**Unit — `tests/test_acp_emit.py`** (no protocol, pure builders):
`tool_call_start(cmd)` → `ToolCall{kind:execute,status:pending}`;
`tool_call_done(cmd, {output, returncode:0})` → `{completed, full output content}`;
returncode≠0 → `{failed}`; `message_chunk(text)` → agent_message_chunk carrying
the FULL text; `with_meta(update, {...})` attaches the harness meta to
`field_meta`.

**Unit — `tests/test_acp_env.py`** (the seam, real files via tmp_path, mock
callbacks — NOT mocking the SDK): `AcpEnvironment.execute` runs a real command and
returns the FULL output dict; fires `on_command("start")` then `("done", out)`;
with `cancel_flag` set, returns the cancelled dict WITHOUT running; (Layer 2) with
a rejecting `request_permission`, skips `super().execute()` and fires
`("rejected")`. This proves the three Codex CRITICALs are actually fixed at the
seam.

**Unit — `tests/test_acp_session.py`**: `SessionStore` create/get/unknown-id;
per-turn history recorded (sets up Layer 4).

**Integration — `tests/test_acp_smoke.py`** (real protocol via the smoke client,
mock model, temp repo):
- `initialize` → assert negotiated `protocol_version` + advertised capabilities.
- `new_session` → assert a `session_id`.
- `prompt "what is 1+1"` → assert `_meta` task.classified `chat_question`, an
  `agent_message_chunk`, `stopReason:end_turn`, and **NO tool_call** (the
  Phase-2 guarantee, now over ACP).
- `prompt` a code-fix task → assert `_meta` skill.load, `tool_call`/
  `tool_call_update`, `stopReason:end_turn`, fix applied.
- `session/cancel` mid-turn → turn resolves `cancelled` (best-effort, at a command
  boundary); a follow-up prompt still succeeds (process alive).
- **raw order:** the smoke client asserts the RECEIVE order of updates (not just
  collected content) — `_meta(task.classified)` before content; updates before
  the `PromptResponse` (Codex MINOR #2).
- **stdout purity:** subprocess stdout contains ONLY valid JSON-RPC lines (guards
  the wire-corruption traps: banner, console sink, echo=print).

**Per-layer gates:** L2 — client rejects permission → command skipped,
`tool_call_update{failed}`. L3 — client advertises fs/terminal → calls routed to
client; absent → `LocalEnvironment` fallback. L4 — `session/load` → history
replayed as `session/update`s.

All Phase 0–3 tests stay green (the ACP layer is purely additive).

## Out of scope (deferred)

- A real TUI (Phase 5) — this phase makes the engine drivable; rendering is next.
- Non-stdio transports (sockets/HTTP) — ACP stable v1 is stdio only.
- `authenticate` beyond a no-op (we advertise no auth methods).
- MCP-server launching (`mcpServers` arg accepted but not acted on in Phase 4).
- Image/audio/resource prompt handling beyond text extraction.

## Phase-5 hand-off note

After Phase 4 the engine is a full ACP agent. Phase 5 ("TUI") may reduce to
"point Zed/Toad at it" plus, if desired, a thin Textual ACP client — because the
hard runner↔client contract is now the standard protocol, not bespoke code. The
`_meta` channel carries our routing/skills observability for any client that
wants to render it.
