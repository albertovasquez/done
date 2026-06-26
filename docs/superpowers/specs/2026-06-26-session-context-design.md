# Session conversation context — design

**Date:** 2026-06-26
**Branch:** `session-context`
**Status:** approved design, ready for implementation plan

## Problem

The harness keeps **no** conversation context across turns. Each ACP `prompt`
rebuilds a fresh `TracingAgent` and calls `agent.run(text)`; upstream
`DefaultAgent.run()` does `self.messages = []` and prepends a fresh
system + instance message (`upstream/src/minisweagent/agents/default.py:88-95`).
The chat path (`ChatHandler.answer`) sends a single user message, and the router
(`Router.classify`) classifies each prompt in isolation. So a follow-up like
"now fix it" has zero knowledge of the prior turn — across **all three** dispatch
paths.

Today `SessionState.history` exists and `prompt()` records to it, but each record
is only `{prompt, stop_reason, kind}` — the user's prompt string and a status
tag. It never stores the assistant's reply or tool output, and nothing reads it
back into a new turn. It is consumed solely by `load_session` to echo
`[resumed] …` lines (display only). **History is never passed to the model.**

## Goal

A session carries **one canonical conversation** across turns, shared by all
three dispatch paths (router classify, chat answer, agent loop). A turn-1
chat answer must be visible to a turn-2 agent run, and vice versa.

## Key decision: the transcript is plain text

The canonical transcript is **not** the raw mini-swe-agent message list. The real
(and mock) model emits messages the chat-completion API and a clean replay cannot
consume:

- assistant turns carry `tool_calls` and `content` can be **`None`**
  (`upstream/src/minisweagent/models/test_models.py:34-41`,
  `models/litellm_model.py:98`);
- observation turns are `role:"tool"` with `tool_call_id`
  (`models/utils/actions_toolcall.py:104-106`);
- terminal turns are `role:"exit"` (`default.py` break condition;
  `LimitsExceeded`/`TimeExceeded`/`RepeatedFormatError` all emit `exit`);
- assistant messages carry an `extra.response` = the **full raw API response
  dump** (`litellm_model.py:101`), which is large and not meant to be persisted.

Carrying those verbatim would (a) 400 the chat completion API on `tool`/`exit`
roles and dangling `tool_calls`, (b) require first-message-must-be-user and
tool-call-pairing invariants, and (c) bloat session state with raw API dumps.

So the transcript holds **only**:

```python
list[{"role": "user" | "assistant", "content": str}]
```

Plain conversational text. One shape, every path consumes it directly, no
structural invariants to maintain, no `extra`. This single decision dissolves the
tool-call / exit-role / dangling-pair / state-bloat failure modes by
construction.

## Design

### 1. Data model (`harness/acp_session.py`)

`SessionState` gains:

```python
transcript: list[dict] = field(default_factory=list)  # [{role, content}], plain text
```

`history` stays unchanged (feeds `load_session`'s `[resumed]` display; locked by
`tests/test_acp_session.py`). It is no longer the memory mechanism — `transcript`
is.

`SessionStore` gains a helper:

```python
def extend(self, session_id: str, msgs: list[dict]) -> None:
    self._sessions[session_id].transcript.extend(msgs)
```

### 2. Agent capture — the flatten rule (`harness/acp_agent.py`)

After `agent.run()` completes, derive **two** plain messages from the agent's run
and `store.extend(...)` them:

- a `user` turn = the task text (`text`);
- an `assistant` turn = the agent's final narration, built by:
  - walking `agent.messages`, collecting assistant `content` strings while
    **skipping `None`** (tool-only turns);
  - appending the final submission / answer from the terminal `exit` message's
    `extra` (`exit_status` / `submission`) when present;
  - joining into one prose block.

Everything structural (`tool_calls`, `role:"tool"`, `role:"exit"`, `extra`, the
fresh system + instance) is dropped. This flatten is the only translation in the
design and is ~10 lines. If the run produced no assistant prose at all, the
assistant turn falls back to a short status string (e.g. the exit status) so the
pair is never empty.

### 3. Agent seed (`harness/tracing_agent.py`)

`TracingAgent.run()` accepts `prior: list[dict] | None = None` and seeds:

```
self.messages = [fresh_system] + (prior or []) + [fresh_instance(task)]
```

Because `prior` is already plain user/assistant text, the result is a valid
message sequence: user-led after the system message, no `tool` roles, no dangling
`tool_calls`. The fresh system + instance are re-rendered every turn so the
current per-turn skill block and hot-swapped worker model apply (the router
re-selects skills per turn; `harness/set_model` hot-swaps the model — both reasons
`acp_agent` rebuilds the agent each prompt).

**Seam note (corrects an earlier assumption).** The *current* `TracingAgent.run()`
is a thin wrapper that **delegates the loop to `super().run()`**
(`tracing_agent.py:45`); only `query()` and `execute_actions()` are reimplemented.
Seeding `prior` requires intercepting the `self.messages = []` reset that
`super().run()` performs at the top. The implementation will use the **smallest**
override that achieves this and avoids copying upstream's four-branch
`while True` loop (`FormatError` / `InterruptAgentFlow` / `RepeatedFormatError` /
`handle_uncaught_exception` / `save()`):

- preferred: stash `self._prior = prior` and override only the seeding, keeping
  the loop delegated to `super().run()` if a clean interception point exists
  (e.g. a small helper that builds the seed messages, leaving the loop untouched);
- if no clean interception exists, the loop is reimplemented as a **new,
  intentional** divergence — pinned to upstream v2.4.2 and documented as such in
  the module docstring. This is explicitly *not* claimed to be "consistent with
  the existing pattern" (the existing pattern delegates `run`); it is a new seam.

The exact mechanism is settled in the implementation plan against the real code;
either way the behavioral contract is: `run()` with no `prior` is byte-identical
to today, and `run(prior=[...])` seeds `[system, *prior, instance]`.

### 4. Chat path (`harness/chat_handler.py`)

```python
def answer(self, prompt: str, history: list[dict] | None = None) -> str:
    ...
    messages = (history or []) + [{"role": "user", "content": prompt}]
```

`history` is already chat-safe plain text, so it drops straight into the
completion call. `history=None` makes the outgoing `messages` byte-identical to
today, keeping `tests/test_chat_handler.py:55`
(`captured["messages"] == [{"role": "user", "content": "hi"}]`) green.

### 5. Router path (`harness/router.py`)

```python
def classify(self, prompt: str, history: list[dict] | None = None) -> Classification:
```

When `history` is present, the **user** message handed to the cheap model becomes
a short preamble + the new prompt. The preamble is built from **user turns only**
(the last few), never assistant prose and never tool output — passing raw agent
observations (pytest failures, tracebacks) into the triage model would skew
classification (a "now fix it" after a wall of test output would be forced to
`code_fix` regardless of intent). The system prompt is unchanged. `history=None`
keeps `classify("x")` byte-identical to today (locked by `tests/test_router.py`).

The router never writes to the transcript.

### 6. Orchestration (`harness/acp_agent.py prompt()`)

Read `state.transcript` once at the top of the turn. **Invariant: every dispatch
branch owns its own transcript writes** (there is no "only chat/agent write" rule):

- **classify** → `self._router.classify(text, history=transcript)`.
- **clarify / ambiguous branch** → write **only the user turn** to the transcript.
  The router's clarifying-question boilerplate is *not* model output and is
  excluded, so it never pollutes the worker model's later context.
- **chat branch** → `handler.answer(text, history=transcript)`, then write the
  user turn + the assistant answer.
- **agent branch** → seed `run(text, prior=transcript)` (via the agent ctor /
  run call), then write the user turn + the flattened assistant narration
  (§2).

### 7. Bounding

None in v1. The full plain-text transcript is carried; existing `cost_limit` /
`step_limit` remain the agent-run backstop. Because the transcript is plain text
with no `extra`, growth is just conversational prose — the raw-API-dump bloat is
gone by construction. The `prior` / `history` parameters are the **single seam**
where a future trim/summarize strategy slots in without redesign.

## Error handling

- A failed agent turn still writes what completed: the user turn plus whatever
  assistant prose was produced (or a status fallback). Because the transcript is
  plain text, there is no dangling-`tool_call` hazard on the failure path — the
  flatten never emits structural messages.
- Router / chat / agent failures keep today's behavior (`refusal` /
  honest mock message); a failed turn simply contributes less (or only the user
  turn) to the transcript.

## Backward compatibility

All new parameters are optional and default to today's behavior **byte-for-byte**:

- `classify(prompt)` — unchanged outgoing call (`tests/test_router.py`).
- `answer(prompt)` — outgoing `messages` unchanged (`tests/test_chat_handler.py:55`).
- `SessionState.history` / `record` — untouched (`tests/test_acp_session.py`).
- `TracingAgent.run(task)` with no `prior` — unchanged
  (`tests/test_tracing_agent.py`, `tests/test_runner.py`).

## Testing

- **`test_acp_session`** — `transcript` accumulates across `extend`; entries are
  plain `{role, content}`; `history` is left untouched.
- **`test_tracing_agent`** — `run(prior=[…])` seeds `messages` as
  `[system, *prior, instance]`; `run()` with no `prior` behaves as today.
- **`test_chat_handler`** — passing `history` prepends prior turns to outgoing
  `messages`; omitting it leaves `:55`'s assertion exact.
- **`test_router`** — passing `history` includes a user-only preamble in the
  classifier's user message; omitting it is unchanged.
- **agent-capture flatten test** — an agent turn whose assistant `content` is
  `None` (tool-only) flattens to a non-empty assistant prose turn without
  crashing; structural roles (`tool`, `exit`) and `extra` never appear in the
  transcript.
- **ACP multi-turn test** — turn-1 `chat_question` → turn-2 agent run sees the
  turn-1 exchange in its `prior`; and the reverse (agent → chat).

## Out of scope (follow-up)

- Transcript trimming / token-budget bounding / summarization.
- Persisting the transcript across process restarts (`load_session` still replays
  the display-only `history`).
- Richer agent-turn capture (structured tool-call replay) — deliberately rejected
  in favor of plain text for v1.
