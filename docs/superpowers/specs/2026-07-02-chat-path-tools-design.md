# Chat Path Gains Tools — Design Spec

**Date:** 2026-07-02
**Status:** Approved design (A1, review-hardened) — pending user spec review
**Author:** Claude (brainstormed with Alberto)

## Problem

A `chat_question` turn is answered by `ChatHandler.answer_stream`
(`harness/chat_handler.py:177`), which makes its own `litellm.completion(...)`
call **with no `tools=`** and only streams `delta.content`. But it sends the
**full agent system prompt** (`base_block + persona_block`,
`chat_handler.py:212`), which frames the persona as a coding agent that *can
list files and run commands*.

When a tool-eager worker model (GLM-5.2) takes that framing literally on a chat
turn, it tries to call `bash`. There is no tool channel on the chat path, so the
model emits the tool call as **raw chat-template text** into the prose stream —
`<tool_call>bash command: ls …<arg_value>…</arg_value>`. That text:

1. renders verbatim in the transcript (the visible garbage),
2. never executes (no executor on the chat path), so
3. the model gets no observation back, re-narrates, and retries — producing the
   duplicated preamble and three stacked `<tool_call>` blocks seen in the bug
   report.

### Root cause

Tool-capable *framing* on a tool-less *path*. The chat path advertises no tools
but tells the model it is an agent. Verified against live code + a live proxy
probe: with tools advertised (the agent path, `harness/streaming_model.py:112`),
GLM-5.2 returns **clean native `tool_calls`**. The model is not at fault; the
harness asks a tool-capable agent to answer with no tools attached.

This is **not** a StreamPainter bug (#81/#138/#217 family) — the painter
faithfully rendered the text it was handed.

## Goal

A `chat_question` turn where the model wants to inspect the project actually
runs the tool through the **existing gated agent loop**, instead of leaking a
dead `<tool_call>` into the transcript. Purely social turns ("how are you") stay
fast and prose-only.

## Non-goals

- No second tool-execution loop inside `ChatHandler`.
- No new router `task_type` and no two-tier "social vs inspective" router signal
  (rejected: reintroduces the brittle hidden-second-classifier pattern the
  codebase deliberately removed — see `chat_handler.py:63-82`).
- No change to `decide_permission` / the permission gate itself.

## Approach — A1: throwaway tool probe, hand off to the agent loop

The chat branch in `acp_agent.prompt` (`acp_agent.py:535`) gains a **tool-probe
step** before the prose pump.

### Data flow

```
chat_question turn (fall-through to model; deterministic tools/skills answers unchanged, run FIRST)
  │
  ├─ interactive (has_elicitation == True)?
  │     │
  │     ├─ probe: ONE non-stream litellm.completion(tools=registry.schemas, max_tokens small)
  │     │     read ONLY: is response.choices[0].message.tool_calls non-empty?
  │     │        ├─ tool_calls present → fall through to existing agent path   # gate+env+loop reused
  │     │        └─ tool_calls empty   → run existing streaming chat pump UNCHANGED
  │     │
  │     └─ (probe never blocks longer than a short call; on probe error → fall back to prose pump, fail-open)
  │
  └─ headless (has_elicitation == False: cron/CLI)
        └─ skip probe entirely → existing streaming chat pump UNCHANGED
           (a chat_question never escalates to command execution unattended)
```

### Why a **throwaway** probe (not reuse of probe content)

Caveman-review finding (🔴): reusing the probe's `.content` as the prose answer
can **re-render the very leak we are fixing** — a model that emits prose *and* a
text-format `<tool_call>` (exactly the GLM screenshot case) would have that
leaked text in `.content`. So the probe result is used **only** as a boolean:
"did the model return native `tool_calls`?" The prose answer is then produced by
the existing streaming pump, whose completion call has **no tools**, so it cannot
leak. Cost: one extra short model call on prose-only interactive chat turns.
Accepted trade for correctness + preserved token-streaming.

### Why hand off to the agent path (not seed the agent loop from the probe)

The probe's `tool_calls` are discarded; the agent path (`acp_agent.py:600+`,
`_run_agent_turn` at `:631`) re-infers the tool call from scratch. This keeps
the chat probe fully decoupled from engine seeding. Cost: a tool-bound chat turn
pays probe + agent loop. Tool turns are rare and already expensive (they run
commands); the extra short probe is negligible against a full gated tool run.
Accepted.

### Headless policy (security boundary)

Escalation is gated on `has_elicitation` (`acp_agent.py:747`): the existing
signal that the client can show a permission modal. Headless/cron/CLI has no
elicitation (`decide_permission` returns `deny`, fail-closed), so:

- **Interactive TUI** (`has_elicitation == True`): chat can escalate; the
  escalated tool call goes through `decide_permission` normally (the user sees
  the permission modal, as for any agent command).
- **Headless (cron/YOLO/CLI)** (`has_elicitation == False`): the probe is
  **skipped**; a `chat_question` stays prose-only and can never execute a
  command. This keeps the authorization surface for unattended runs **exactly
  as it is today** — a chat turn under cron gains no new power.

This is a deliberate decision: the point of the fix is interactive UX, and we do
not want an unattended "chat" turn silently running shell commands it could not
run before. The gate itself is unchanged and remains fail-closed.

## Components

### 1. `ChatHandler.wants_tool(prompt, history) -> bool` (new)

- New method on `harness/chat_handler.py`.
- Makes ONE non-stream `litellm.completion` with `tools=` (registry schemas) and
  a small `max_tokens`, same model/system prompt/history as `answer_stream`.
- Returns `True` iff `response.choices[0].message.tool_calls` is non-empty.
- **Mock mode (`self._model_id is None`) → returns `False` immediately** (no
  litellm call; preserves hermetic tests and the honest mock-mode prose answer).
- **Deterministic short-circuits run BEFORE any probe:** `is_tools_question` /
  the gated `is_capability_question` path already answer from data with no model
  (`chat_handler.py:199-204`); those stay first and are never probed.
- Needs the tool registry. `ChatHandler` gains a `registry` (or `tool_schemas`)
  constructor arg, built the same way the agent path builds it
  (`build_registry(skill_roots=…, memory_root=…)`), injected at construction in
  `acp_agent.py:540`.
- Fail-open: any exception in the probe → treat as "no tool" (return `False`),
  so a probe failure degrades to today's prose behavior, never a crash.

### 2. Chat dispatch branch (`acp_agent.py:535-598`)

- **Mechanism (single, unambiguous):** compute `wants_tool` **before** the
  `if cls.task_type == "chat_question":` block is entered — only when
  `has_elicitation` and not a deterministic short-circuit question. If it is
  `True`, **skip the chat block entirely and fall through to the existing agent
  path** (`acp_agent.py:600+`). The agent path's existing tail records
  `kind:"agent"`, extends the store, traces `run.finished`, and returns — reused
  verbatim. There is NO new record/extend/return site, so the 🔴 double-record
  finding cannot occur: exactly one path records the turn.

### 3. Transcript / history identity across hand-off

- `_run_agent_turn` treats `text` as the live turn prompt and `transcript` as
  strictly prior. The hand-off must NOT append `text` to `transcript` before
  delegating (else the current user message is injected twice). Verified: the
  chat branch appends to the store only at its tail (`:594-596`), which the
  hand-off skips — so no double-injection as long as hand-off happens before
  the chat block is entered. The Component 2 mechanism guarantees this.

## Error handling

- Probe raises / proxy unreachable → `wants_tool` returns `False` → prose pump
  (fail-open, same as today's chat behavior).
- ESC during probe → same `cancel_flag` discipline as `answer_stream`
  (`run_interruptible` wraps the probe's blocking `completion` open); a cancel
  returns a clean `cancelled` PromptResponse.
- Hand-off to the agent path inherits all of the agent path's existing error
  handling (refusal, cancel, gate deny).

## Testing

- **Unit — `wants_tool` mock mode:** `model_id=None` → `False`, no litellm call
  (hermetic).
- **Unit — deterministic short-circuit precedence:** a tools/skills question is
  answered from data and never probed.
- **Unit — probe boolean semantics:** monkeypatch `litellm.completion` to return
  a response with/without `tool_calls`; assert `True`/`False`.
- **Unit — fail-open:** probe raises → `wants_tool` returns `False`.
- **Integration — headless never escalates:** `has_elicitation == False` →
  probe skipped, prose pump runs, no tool executed (assert the agent path is
  not entered).
- **Integration — interactive escalation:** `has_elicitation == True` + probe
  returns `tool_calls` → the agent loop runs, the command goes through the gate,
  the transcript records `kind:"agent"` exactly once (no double-record).
- **Regression — no leak:** a chat turn whose prose pump runs (no tools in that
  call) can never emit `<tool_call>`/`<arg_value>` text — assert the streamed
  answer contains no such residue.
- Test command (from worktree root):
  `.venv/bin/python -m pytest tests/ -q` (target `tests/` only). Note: the venv
  lives in the PRIMARY checkout; conftest (PR #94) prepends this worktree's src
  root, so use the primary's `.venv/bin/python`.

## Open questions

None blocking. The probe's `max_tokens` value and whether to reuse the
per-session registry vs. build fresh are implementation details for the plan.
