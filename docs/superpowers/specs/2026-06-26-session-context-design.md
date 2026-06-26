# Session conversation context — design

**Date:** 2026-06-26
**Branch:** `session-context`
**Status:** approved design, revised post-Codex review (GO-WITH-FIXES) to track the
streaming-chat merge now on `main`. Ready for implementation plan.

> **Revision note (2026-06-26):** This spec was first written against a
> non-streaming `ChatHandler.answer()`. `main` has since merged streamed chat
> (`ChatHandler.answer_stream() -> Iterator[str]`). §4, §6, the agent-capture
> seam (§2/§3), and Backward compatibility are updated below to match current
> `main`. The plain-text-transcript core decision is unchanged.

## Problem

The harness keeps **no** conversation context across turns. Each ACP `prompt`
rebuilds a fresh `TracingAgent` and calls `agent.run(text)`; upstream
`DefaultAgent.run()` does `self.messages = []` and prepends a fresh
system + instance message (`upstream/src/minisweagent/agents/default.py:88-95`).
The chat path (`ChatHandler.answer_stream`) sends a single user message, and the
router (`Router.classify`) classifies each prompt in isolation. So a follow-up
like "now fix it" has zero knowledge of the prior turn — across **all three**
dispatch paths.

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

`SessionStore` gains a helper that stores **fresh, validated** dicts (so a caller
can't mutate stored state later or insert a malformed role):

```python
def extend(self, session_id: str, msgs: list[dict]) -> None:
    for m in msgs:
        assert m["role"] in ("user", "assistant")  # plain-text invariant
        self._sessions[session_id].transcript.append(
            {"role": m["role"], "content": m["content"]})  # copy, not alias
```

### 2. Agent capture — the flatten rule + the capture seam (`harness/acp_agent.py`)

After the agent run completes, derive **two** plain messages and
`store.extend(...)` them:

- a `user` turn = the task text (`text`);
- an `assistant` turn = the agent's narration, built by:
  - walking `agent.messages` in chronological order, collecting **every**
    non-`None` assistant `content` string (a run loops until `role:"exit"` —
    `default.py:96-122` — so multiple assistant turns are normal; skip the
    tool-only turns whose `content` is `None`);
  - appending the terminal `exit` message's `extra` answer/status
    (`exit_status` / `submission`) when present;
  - joining the collected strings into one prose block, in order.

Everything structural (`tool_calls`, `role:"tool"`, `role:"exit"`, `extra`, the
fresh system + instance) is dropped.

**Capture seam (corrects the original boundary).** `agent.messages` is **not**
reachable from `prompt()` today: the agent is constructed and run *inside*
`run_engine()` (`acp_agent.py:225-238`), which returns only a status string, and
`_run_agent_turn()` only sees that string (`acp_agent.py:242-249`). So the flatten
must happen **where the agent object lives** — `run_engine()` is changed to return
a **structured result**:

```python
{"stop_reason": "end_turn" | "refusal",
 "assistant": "<flattened prose>",   # may be "" if the run produced none
 "exit_status": "<raw status>"}
```

`_run_agent_turn()` returns this structured result up to `prompt()`, which writes
the `{user, assistant}` pair. The flatten itself (a small helper,
`flatten_agent_messages(messages) -> str`) is the only translation in the design
(~10-15 lines) and is unit-tested in isolation against synthetic `messages`.

**Fallbacks (never an empty/half pair on the agent path):**
- assistant prose is `""` → use a short status string (e.g. the `exit_status`).
- `agent.messages == []` (construction failed before any message) → assistant
  turn = a status fallback; the user turn is still written.

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

**Seam note (settled against the real code).** The *current* `TracingAgent.run()`
delegates the loop to `super().run()` (`tracing_agent.py:53-55`); upstream seeds
inline — `self.messages = []` then `add_messages(system, instance)` — with **no
hook between the reset and the loop** (`default.py:90-95`). Codex review confirmed
there is **no clean interception point** that both seeds `prior` and keeps the
loop delegated.

Therefore the implementation **reimplements `DefaultAgent.run()` inside
`TracingAgent`** as a **new, intentional divergence**, pinned to upstream v2.4.2
and documented as such in the module docstring. This is explicitly *not* "the
existing pattern" (which delegates `run`); it is a new seam. It honors the HARD
zero-upstream-edits constraint because the override lives entirely in
`harness/tracing_agent.py` — `upstream/` is never touched. The reimplemented loop
must reproduce upstream's branches verbatim (`FormatError` /
`InterruptAgentFlow` / `RepeatedFormatError` / `handle_uncaught_exception` /
`save()`), changing **only** the seed line to
`self.messages = [system] + (prior or []) + [instance]`.

Behavioral contract: `run()` with no `prior` is byte-identical to today
(verified by `tests/test_tracing_agent.py`, `tests/test_runner.py`), and
`run(prior=[...])` seeds `[system, *prior, instance]`.

### 4. Chat path (`harness/chat_handler.py`) — streamed

`main` streams chat: `answer_stream(self, prompt) -> Iterator[str]` with
`litellm.completion(..., stream=True)` (`chat_handler.py:23-38`). Add `history`:

```python
def answer_stream(self, prompt: str,
                  history: list[dict] | None = None) -> Iterator[str]:
    ...
    messages = (history or []) + [{"role": "user", "content": prompt}]
    stream = litellm.completion(..., messages=messages, stream=True)
    ...
```

`history` is already chat-safe plain text, so it drops straight into the
completion call; `stream=True` is preserved. With `history=None` the outgoing
`messages` is byte-identical to today, keeping `tests/test_chat_handler.py:55`
(`captured["messages"] == [{"role": "user", "content": "hi"}]`) and the
`stream is True` assertion (`:53`) green.

**Transcript write-back requires accumulation.** Streaming yields pieces and
returns no full string. The chat dispatch's worker-thread `pump()`
(`acp_agent.py:118-125`) must **accumulate** each emitted piece into a buffer
(while still emitting it as a `message_chunk` for live rendering) and surface the
joined full string to the async branch, which then writes
`{user: text, assistant: full}` to the transcript. The mock-mode single piece
accumulates to itself — no special case.

### 5. Router path (`harness/router.py`)

```python
def classify(self, prompt: str, history: list[dict] | None = None) -> Classification:
```

When `history` is present, the **user** message handed to the cheap model becomes
a short preamble + the new prompt. The preamble is built from **user turns only**
(the last few), never assistant prose and never tool output — passing raw agent
observations (pytest failures, tracebacks) into the triage model would skew
classification (a "now fix it" after a wall of test output would be forced to
`code_fix` regardless of intent).

The classifier requires JSON-only output and currently sends exactly one
system + one user message (`router.py:47-48`, `:56-59`). The preamble must be
**clearly delimited** from the current request and the current prompt must remain
the explicit classification target, so prior user turns provide context without
dominating — e.g.:

```
Recent context (for reference only):
- <prior user turn>
- <prior user turn>

Classify THIS request: <new prompt>
```

This keeps the API shape (one system, one user) and the JSON contract intact.
`history=None` keeps `classify("x")` byte-identical to today (locked by
`tests/test_router.py`).

The router never writes to the transcript.

### 6. Orchestration (`harness/acp_agent.py prompt()`)

Read `state.transcript` once at the top of the turn. **Invariant: every dispatch
branch owns its own transcript writes** (there is no "only chat/agent write"
rule). Walking the branches in source order (`acp_agent.py` `prompt()`), each is
explicit about what it writes — including the early-return and metadata cases the
first draft missed:

- **router-unavailable early return** (`acp_agent.py:96-101`, classify raises →
  emit "router unavailable", `stop_reason="refusal"`) → **write nothing.** No
  classification happened; persisting an orphan user turn with no response would
  mislead the next turn's context. The transcript is unchanged on this path.
- **classify** (success) → `self._router.classify(text, history=transcript)`.
- **clarify / ambiguous branch** (`acp_agent.py:108-113`) → write **only the user
  turn**. The clarifying question is router boilerplate, *not* model output, so it
  is excluded. This is the one **intentional exception** to §2's "two messages per
  turn": a user turn with no assistant turn is valid here and downstream consumers
  must tolerate it.
- **skill_load metadata chunk** (`acp_agent.py:134-138`, `with_meta(message_chunk(
  ""), {...})`) → **never written** to the transcript. It is a metadata-only,
  empty-content chunk for the chips, not assistant prose.
- **chat branch** → `handler.answer_stream(text, history=transcript)`, accumulate
  the streamed pieces (§4), then write the user turn + the full assistant answer.
- **agent branch** → seed `run(text, prior=transcript)`; capture the flattened
  assistant narration from the structured `run_engine()` result (§2), then write
  the user turn + that assistant turn (with the status fallback so the pair is
  never half-empty).

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
- `answer_stream(prompt)` with no `history` — outgoing `messages` and `stream=True`
  unchanged (`tests/test_chat_handler.py:53-55`).
- `SessionState.history` / `record` — untouched (`tests/test_acp_session.py`).
  `load_session` still replays the **display-only `history`** (the `[resumed]`
  lines — `acp_agent.py:72-76`, locked by `tests/test_acp_smoke.py:423-455`); it
  does **not** read or replay `transcript`. The two are independent.
- `TracingAgent.run(task)` with no `prior` — unchanged
  (`tests/test_tracing_agent.py`, `tests/test_runner.py`).
- `run_traced.py` (dev CLI, no ACP session) — unaffected: it calls
  `answer_stream(prompt)` with no `history` (`run_traced.py:86-90`), which is
  byte-identical to today. Session context is an ACP-session concept;
  `run_traced` is **out of scope** (single-shot, no `SessionStore`).

## Testing

- **`test_acp_session`** — `transcript` accumulates across `extend`; entries are
  plain `{role, content}`; `history` is left untouched.
- **`test_tracing_agent`** — `run(prior=[…])` seeds `messages` as
  `[system, *prior, instance]`; `run()` with no `prior` behaves as today.
- **`test_chat_handler`** — `answer_stream(prompt, history=[…])` prepends prior
  turns to the outgoing `messages`; omitting `history` leaves `:55`'s assertion
  (`messages == [{"role": "user", "content": "hi"}]`) and `:53`'s `stream is True`
  exact.
- **`test_router`** — passing `history` includes a delimited user-only preamble in
  the classifier's user message AND keeps the current prompt as the explicit
  classification target; omitting it is unchanged.
- **flatten unit test** (`flatten_agent_messages`) — synthetic `messages` with
  multiple assistant turns + a `None` (tool-only) turn + a terminal `exit` flatten
  to one non-empty prose block joining the assistant turns in order; structural
  roles (`tool`, `exit`) and `extra` never appear; `messages == []` yields the
  status fallback (no crash).
- **chat-accumulation test** — the streamed chat dispatch writes a SINGLE
  `{user, assistant}` pair whose assistant content is the full joined answer (not
  per-piece), and still emits each piece as a `message_chunk`.
- **orchestration write-rules test** — router-unavailable writes nothing; the
  clarify branch writes only a user turn; the skill_load metadata chunk is never
  in the transcript.
- **ACP multi-turn test** — turn-1 `chat_question` → turn-2 agent run sees the
  turn-1 exchange in its `prior`; and the reverse (agent → chat).

## Out of scope (follow-up)

- Transcript trimming / token-budget bounding / summarization.
- Persisting the transcript across process restarts (`load_session` still replays
  the display-only `history`).
- Richer agent-turn capture (structured tool-call replay) — deliberately rejected
  in favor of plain text for v1.
