# Session Conversation Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Carry one canonical plain-text conversation across turns in an ACP session so follow-ups ("now fix it", "I meant Texable") are understood by the router, chat, and agent paths.

**Architecture:** A per-session `transcript: list[{role, content, origin}]` of plain conversational text (never raw agent messages). Each dispatch branch in `prompt()` reads it for context and writes its turn back. The agent loop is seeded with prior turns via a new `TracingAgent.run(prior=...)`; chat and router receive it via new optional `history=` params. Agent tool-call messages are flattened to prose at capture time — no tool/exit roles or `extra` ever enter the transcript.

**Tech Stack:** Python 3.11, mini-swe-agent (upstream v2.4.2, vendored under `upstream/`), `acp` SDK, litellm, pytest.

## Global Constraints

- **Zero upstream edits.** Never modify anything under `upstream/`. All divergence lives in `harness/`. (Project rule; the `TracingAgent` seam in Task 4 is the load-bearing case.)
- **Backward compatibility is byte-for-byte.** Every new parameter is optional and defaults to today's exact behavior. Locked tests that must stay green unchanged: `tests/test_router.py`, `tests/test_chat_handler.py` (esp. `messages == [{"role":"user","content":"hi"}]` and `stream is True`), `tests/test_acp_session.py`, `tests/test_tracing_agent.py`, `tests/test_runner.py`.
- **Transcript shape:** `list[dict]`, each `{"role": "user"|"assistant", "content": str, "origin": "chat"|"agent"|"clarify"}`. Plain text only — no `tool`/`exit` roles, no `tool_calls`, no `extra`.
- **Test invocation:** from the worktree root, `../../.venv/bin/python -m pytest <path> -v` (the repo venv lives at the primary checkout; `upstream/src` and `.` are added to `sys.path` at the top of each test file).
- **Pin note:** the `DefaultAgent.run()` reimplementation (Task 4) is pinned to upstream v2.4.2 (`upstream/src/minisweagent/agents/default.py:88-122`). Document it in the module docstring; re-verify on any upstream bump.

**Spec:** `docs/superpowers/specs/2026-06-26-session-context-design.md`

---

## File Structure

- `harness/acp_session.py` — `SessionState.transcript` field + `SessionStore.extend()` (Task 1)
- `harness/transcript.py` — **new** — `flatten_agent_messages()` and `router_preamble()` pure helpers (Tasks 2, 6)
- `harness/router.py` — `classify(prompt, history=None)` (Task 6)
- `harness/chat_handler.py` — `answer_stream(prompt, history=None)` (Task 3)
- `harness/tracing_agent.py` — `run(task, prior=None)` seam (Task 4)
- `harness/acp_agent.py` — orchestration: read transcript, dispatch with context, write back; `run_engine()` returns a structured result (Tasks 5, 7)
- Tests alongside each, plus `tests/test_acp_session_context.py` (ACP multi-turn, Task 8)

Order rationale: pure data + helpers first (Tasks 1–2), then the three path-level seams independently (Tasks 3, 4, 6), then wire orchestration (Tasks 5, 7), then the end-to-end multi-turn test (Task 8).

---

## Task 1: Transcript storage on the session

**Files:**
- Modify: `harness/acp_session.py`
- Test: `tests/test_acp_session.py`

**Interfaces:**
- Produces: `SessionState.transcript: list[dict]`; `SessionStore.extend(session_id: str, msgs: list[dict]) -> None` — appends validated `{role, content, origin}` **copies**.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_acp_session.py`:

```python
def test_transcript_starts_empty(tmp_path):
    store = SessionStore(); sid = store.new(cwd=str(tmp_path))
    assert store.get(sid).transcript == []


def test_extend_appends_copies(tmp_path):
    store = SessionStore(); sid = store.new(cwd=str(tmp_path))
    msg = {"role": "user", "content": "hi", "origin": "chat"}
    store.extend(sid, [msg])
    msg["content"] = "mutated"                      # mutate the input after storing
    assert store.get(sid).transcript == [{"role": "user", "content": "hi", "origin": "chat"}]


def test_extend_rejects_bad_role_or_origin(tmp_path):
    store = SessionStore(); sid = store.new(cwd=str(tmp_path))
    with pytest.raises(AssertionError):
        store.extend(sid, [{"role": "system", "content": "x", "origin": "chat"}])
    with pytest.raises(AssertionError):
        store.extend(sid, [{"role": "user", "content": "x", "origin": "tool"}])


def test_extend_does_not_touch_history(tmp_path):
    store = SessionStore(); sid = store.new(cwd=str(tmp_path))
    store.extend(sid, [{"role": "user", "content": "hi", "origin": "agent"}])
    assert store.get(sid).history == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `../../.venv/bin/python -m pytest tests/test_acp_session.py -v`
Expected: FAIL — `AttributeError: 'SessionState' object has no attribute 'transcript'` / `SessionStore` has no `extend`.

- [ ] **Step 3: Implement** — edit `harness/acp_session.py`:

Add to `SessionState` (after `history`):

```python
    transcript: list[dict] = field(default_factory=list)
```

Add to `SessionStore` (after `record`):

```python
    def extend(self, session_id: str, msgs: list[dict]) -> None:
        transcript = self._sessions[session_id].transcript
        for m in msgs:
            assert m["role"] in ("user", "assistant")
            assert m["origin"] in ("chat", "agent", "clarify")
            transcript.append({"role": m["role"], "content": m["content"],
                               "origin": m["origin"]})  # fresh copy, not alias
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `../../.venv/bin/python -m pytest tests/test_acp_session.py -v`
Expected: PASS (all, including the 4 pre-existing tests).

- [ ] **Step 5: Commit**

```bash
git add harness/acp_session.py tests/test_acp_session.py
git commit -m "feat(session): add plain-text transcript store with validated extend"
```

---

## Task 2: `flatten_agent_messages` helper

Flattens a finished agent's `messages` to one prose string. Verified message shape (probed against the mock agent): `[system, user, (assistant, tool)×N, exit]`; assistant `content` may be `None` (tool-only turns, real litellm); terminal is `role:"exit"` with `extra={exit_status, submission}` where `submission` is often `""`.

**Files:**
- Create: `harness/transcript.py`
- Test: `tests/test_transcript.py`

**Interfaces:**
- Produces: `flatten_agent_messages(messages: list[dict]) -> str` — joins assistant prose in order, skips `None` content, appends a non-empty `exit.extra.submission` if present, returns `""` if nothing usable.

- [ ] **Step 1: Write the failing tests** — create `tests/test_transcript.py`:

```python
import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

from harness.transcript import flatten_agent_messages


def _agent_messages():
    # mirrors the verified real shape: system, user, (assistant, tool)*, exit
    return [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "TASK"},
        {"role": "assistant", "content": "Let me reproduce the failure first."},
        {"role": "tool", "content": "<returncode>1</returncode>"},
        {"role": "assistant", "content": None},                       # tool-only turn
        {"role": "tool", "content": "<returncode>0</returncode>"},
        {"role": "assistant", "content": "Fixed it."},
        {"role": "exit", "content": "", "extra": {"exit_status": "Submitted",
                                                  "submission": "Bug fixed in calculator.py"}},
    ]


def test_flatten_joins_assistant_prose_skips_none_and_appends_submission():
    out = flatten_agent_messages(_agent_messages())
    assert "Let me reproduce the failure first." in out
    assert "Fixed it." in out
    assert "Bug fixed in calculator.py" in out          # submission appended
    assert "None" not in out                             # None content skipped, not stringified
    assert "<returncode>" not in out                     # tool/exit structure never leaks
    assert out.index("reproduce") < out.index("Fixed")   # chronological order


def test_flatten_empty_submission_uses_only_prose():
    msgs = _agent_messages()
    msgs[-1]["extra"]["submission"] = ""
    out = flatten_agent_messages(msgs)
    assert out.strip().endswith("Fixed it.")             # no trailing empty submission


def test_flatten_no_messages_returns_empty():
    assert flatten_agent_messages([]) == ""


def test_flatten_only_tool_turns_returns_empty():
    msgs = [{"role": "assistant", "content": None},
            {"role": "exit", "content": "", "extra": {"exit_status": "Submitted", "submission": ""}}]
    assert flatten_agent_messages(msgs) == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `../../.venv/bin/python -m pytest tests/test_transcript.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'harness.transcript'`.

- [ ] **Step 3: Implement** — create `harness/transcript.py`:

```python
"""Pure helpers for the session transcript (no I/O, no agent/model deps).

flatten_agent_messages: collapse a finished agent's message list into one prose
string for the plain-text transcript. The transcript never holds tool/exit roles
or `extra`, so this is the single translation from agent-shape to transcript-shape.
"""

from __future__ import annotations


def flatten_agent_messages(messages: list[dict]) -> str:
    """Join assistant prose (chronological), skip None content, append a
    non-empty terminal submission. Returns "" when nothing usable was produced."""
    parts: list[str] = []
    for m in messages:
        if m.get("role") == "assistant":
            content = m.get("content")
            if isinstance(content, str) and content.strip():
                parts.append(content.strip())
        elif m.get("role") == "exit":
            submission = m.get("extra", {}).get("submission")
            if isinstance(submission, str) and submission.strip():
                parts.append(submission.strip())
    return "\n\n".join(parts)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `../../.venv/bin/python -m pytest tests/test_transcript.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add harness/transcript.py tests/test_transcript.py
git commit -m "feat(transcript): flatten_agent_messages prose helper"
```

---

## Task 3: Chat path accepts history

**Files:**
- Modify: `harness/chat_handler.py`
- Test: `tests/test_chat_handler.py`

**Interfaces:**
- Consumes: nothing from prior tasks.
- Produces: `ChatHandler.answer_stream(prompt: str, history: list[dict] | None = None) -> Iterator[str]` — prepends `history` (already plain `{role, content}`) to the outgoing messages.

- [ ] **Step 1: Write the failing test** — append to `tests/test_chat_handler.py`:

```python
def test_history_is_prepended_to_messages(monkeypatch):
    captured = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return iter([_Chunk("ok")])

    import litellm
    monkeypatch.setattr(litellm, "completion", fake_completion)

    history = [{"role": "user", "content": "earlier q"},
               {"role": "assistant", "content": "earlier a"}]
    list(ChatHandler("gpt-5.4").answer_stream("follow up", history=history))

    assert captured["messages"] == [
        {"role": "user", "content": "earlier q"},
        {"role": "assistant", "content": "earlier a"},
        {"role": "user", "content": "follow up"},
    ]
```

> Note: the existing `test_real_mode_streams_pieces_in_order_with_stream_true` already locks `messages == [{"role":"user","content":"hi"}]` when `history` is omitted — that must stay green, proving the default is byte-for-byte unchanged.

- [ ] **Step 2: Run test to verify it fails**

Run: `../../.venv/bin/python -m pytest tests/test_chat_handler.py -v`
Expected: FAIL — `answer_stream()` got an unexpected keyword argument `history`.

- [ ] **Step 3: Implement** — edit `harness/chat_handler.py`. Change the signature and the `messages=` line only:

```python
    def answer_stream(self, prompt: str,
                      history: list[dict] | None = None) -> Iterator[str]:
```

In the real-mode body, replace the `messages=[{"role": "user", "content": prompt}]` argument with:

```python
            messages=(history or []) + [{"role": "user", "content": prompt}],
```

(The mock-mode early return is unchanged — it ignores `history`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `../../.venv/bin/python -m pytest tests/test_chat_handler.py -v`
Expected: PASS (all, including the unchanged `history`-omitted assertions).

- [ ] **Step 5: Commit**

```bash
git add harness/chat_handler.py tests/test_chat_handler.py
git commit -m "feat(chat): answer_stream accepts optional history"
```

---

## Task 4: Agent seam — `TracingAgent.run(prior=...)`

The current `TracingAgent.run()` (`harness/tracing_agent.py:45-70`) wraps `super().run()`. Upstream `DefaultAgent.run()` (`upstream/src/minisweagent/agents/default.py:88-122`) does `self.messages = []` then seeds system+instance with **no hook between the reset and the loop**. To inject `prior`, reimplement `DefaultAgent.run()` inside `TracingAgent` as a new, intentional divergence — changing **only** the seed line. This keeps zero upstream edits.

**Files:**
- Modify: `harness/tracing_agent.py`
- Test: `tests/test_tracing_agent.py`

**Interfaces:**
- Consumes: nothing from prior tasks.
- Produces: `TracingAgent.run(task: str = "", prior: list[dict] | None = None, **kwargs) -> dict` — seeds `self.messages = [system] + (prior or []) + [instance]`; with no `prior`, behavior is byte-identical to today.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_tracing_agent.py`. (Read the top of that file for the existing fixtures — it builds a `TracingAgent` with a deterministic model and templates; reuse that construction.)

```python
def test_run_with_prior_seeds_messages_between_system_and_instance():
    # Build an agent the same way the existing tests do, then run with prior.
    # After the run, the message list must begin: [system, *prior, instance, ...].
    agent = _build_agent()                     # reuse the file's existing helper/fixture
    prior = [{"role": "user", "content": "earlier"},
             {"role": "assistant", "content": "reply"}]
    agent.run("the task", prior=prior)
    assert agent.messages[0]["role"] == "system"
    assert agent.messages[1] == {"role": "user", "content": "earlier"}
    assert agent.messages[2] == {"role": "assistant", "content": "reply"}
    assert agent.messages[3]["role"] == "user"          # the fresh instance/task message
    assert "the task" in agent.messages[3]["content"]


def test_run_without_prior_unchanged():
    agent = _build_agent()
    agent.run("the task")
    assert agent.messages[0]["role"] == "system"
    assert agent.messages[1]["role"] == "user"          # instance directly after system
    assert "the task" in agent.messages[1]["content"]
```

> If `tests/test_tracing_agent.py` has no reusable `_build_agent()` helper, factor the agent construction from an existing test into one at the top of the file (a refactor, behavior-preserving) and use it in both new tests and at least one existing test to prove it's equivalent.

- [ ] **Step 2: Run tests to verify they fail**

Run: `../../.venv/bin/python -m pytest tests/test_tracing_agent.py -v`
Expected: FAIL — `run()` got an unexpected keyword argument `prior` (and the prior-seeding assertion).

- [ ] **Step 3: Implement** — edit `harness/tracing_agent.py`.

First, extend the module docstring's "Why reimplement" list with a `run()` entry:

```
  - run():    parent re-raises on uncaught exceptions, so run.finished must be
              emitted in a finally. ADDITIONALLY (v2.4.2 divergence): parent seeds
              self.messages = [system, instance] with no hook between the reset and
              the step loop, so to carry a prior transcript we reimplement the loop
              here and change ONLY the seed line. Pinned to upstream v2.4.2 — verify
              against default.py on upgrade.
```

Replace the existing `run()` method body. Reproduce upstream's loop (`default.py:88-122`) verbatim except the seed line, wrapped in the existing run.started/run.finished emit + exception capture:

```python
    def run(self, task: str = "", prior: list[dict] | None = None, **kwargs) -> dict:
        self._run_start = time.time()
        self._emitter.set_clock(self._t)
        self._emitter.emit("run.started", task=task,
                           model_name=getattr(self.model.config, "model_name", "unknown"),
                           cwd=getattr(self.env.config, "cwd", ""))
        exc_type = exc_str = None
        try:
            # --- reimplemented DefaultAgent.run() body, pinned to upstream v2.4.2 ---
            # ONLY divergence from upstream: prior transcript injected between
            # the fresh system message and the fresh instance message.
            self.extra_template_vars |= {"task": task, **kwargs}
            self.messages = []
            self.add_messages(
                self.model.format_message(role="system",
                    content=self._render_template(self.config.system_template)))
            self.add_messages(*(prior or []))
            self.add_messages(
                self.model.format_message(role="user",
                    content=self._render_template(self.config.instance_template)))
            from minisweagent.exceptions import (FormatError, InterruptAgentFlow)
            while True:
                try:
                    self.step()
                    self.n_consecutive_format_errors = 0
                except FormatError as e:
                    self.n_consecutive_format_errors += 1
                    if 0 < self.config.max_consecutive_format_errors <= self.n_consecutive_format_errors:
                        self.add_messages(*e.messages, {"role": "exit", "content": "RepeatedFormatError",
                            "extra": {"exit_status": "RepeatedFormatError", "submission": ""}})
                    else:
                        self.add_messages(*e.messages)
                except InterruptAgentFlow as e:
                    self.add_messages(*e.messages)
                except Exception as e:
                    self.handle_uncaught_exception(e)
                    raise
                finally:
                    self.save(self.config.output_path)
                if self.messages[-1].get("role") == "exit":
                    break
            return self.messages[-1].get("extra", {})
            # --- end reimplemented body ---
        except BaseException as e:  # noqa: BLE001 — record then re-raise
            exc_type, exc_str = type(e).__name__, str(e)
            raise
        finally:
            last_extra = self.messages[-1].get("extra", {}) if self.messages else {}
            self._emitter.emit(
                "run.finished",
                ok=exc_type is None,
                exit_status=last_extra.get("exit_status", "") or (exc_type or ""),
                n_calls=self.n_calls,
                total_cost=round(self.cost, 6),
                elapsed_s=round(self._t(), 3),
                exception_type=exc_type,
                exception_str=exc_str,
            )
```

> **Exception ancestry (verified against `upstream/src/minisweagent/exceptions.py`):** `Submitted`, `LimitsExceeded`, `TimeExceeded`, and `FormatError` are ALL subclasses of `InterruptAgentFlow` (`TimeExceeded` ⊂ `LimitsExceeded` ⊂ `InterruptAgentFlow`; `FormatError` ⊂ `InterruptAgentFlow`). Two consequences for the reimplemented loop: (1) `except FormatError` MUST precede `except InterruptAgentFlow` — order is load-bearing, reproduce it exactly. (2) `LimitsExceeded`/`TimeExceeded`/`Submitted` are caught by `except InterruptAgentFlow` → `add_messages(*e.messages)` → the appended `role:"exit"` message ends the loop. No extra `except` clause is needed — do NOT add one.

- [ ] **Step 4: Run the full agent test suite to verify nothing regressed**

Run: `../../.venv/bin/python -m pytest tests/test_tracing_agent.py tests/test_tracing_agent_skills.py tests/test_runner.py -v`
Expected: PASS (existing + 2 new). The runner tests exercise `run()` end-to-end and are the regression guard for the reimplemented loop.

- [ ] **Step 5: Commit**

```bash
git add harness/tracing_agent.py tests/test_tracing_agent.py
git commit -m "feat(agent): TracingAgent.run(prior=...) seeds prior transcript (v2.4.2 divergence)"
```

---

## Task 5: `run_engine` returns a structured agent result

`agent.messages` is only reachable inside `run_engine()` (`acp_agent.py:225-238`). Make it flatten and return prose so `prompt()` can write the transcript.

**Files:**
- Modify: `harness/acp_agent.py` (`run_engine` inside `_run_agent_turn`, lines ~225-249)
- Test: `tests/test_acp_session_context.py` (created here, extended in Task 8)

**Interfaces:**
- Consumes: `flatten_agent_messages` (Task 2); `TracingAgent.run(prior=...)` (Task 4).
- Produces: `_run_agent_turn(...)` returns `dict` `{"stop_reason": str, "assistant": str, "exit_status": str}` instead of a bare `str`.

- [ ] **Step 1: Write the failing test** — create `tests/test_acp_session_context.py`:

```python
import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

from harness.transcript import flatten_agent_messages


def test_flatten_used_for_agent_capture_smoke():
    # Guards the contract Task 5 relies on: a realistic agent message list
    # flattens to non-empty prose that excludes tool/exit structure.
    messages = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "TASK"},
        {"role": "assistant", "content": "Working on it."},
        {"role": "tool", "content": "<returncode>0</returncode>"},
        {"role": "exit", "content": "", "extra": {"exit_status": "Submitted", "submission": "done"}},
    ]
    out = flatten_agent_messages(messages)
    assert out == "Working on it.\n\ndone"
```

> The full agent-path capture is asserted end-to-end in Task 8 (mock agent through ACP). This task's deliverable is the structured-return refactor; the smoke test above locks the helper contract it depends on.

- [ ] **Step 2: Run test to verify it fails or passes**

Run: `../../.venv/bin/python -m pytest tests/test_acp_session_context.py -v`
Expected: PASS (it only exercises Task 2's helper — this confirms the contract before the refactor). If Task 2 is complete this is green; treat a RED here as a Task-2 regression.

- [ ] **Step 3: Implement** — edit `run_engine` and `_run_agent_turn` in `harness/acp_agent.py`.

Add the import near the top of the file (with the other `from harness import`):

```python
from harness.transcript import flatten_agent_messages
```

Change `run_engine` to seed `prior` and return structured data. Replace the inner `run_engine` (currently returns a `str`) with:

```python
        def run_engine() -> dict:
            from harness.tracing_agent import TracingAgent
            from harness.events import Emitter
            emitter = Emitter("/dev/null", clock=lambda: 0.0, console=False)
            cfg = dict(self._agent_cfg)
            agent = TracingAgent(self._model_factory(self._worker_model_id), env,
                                 emitter=emitter, skill_block=skill_block, **cfg)
            try:
                result = agent.run(text, prior=prior)
                exit_status = result.get("exit_status", "end_turn")
                return {"stop_reason": "end_turn", "exit_status": exit_status,
                        "assistant": flatten_agent_messages(agent.messages)}
            except Exception:  # engine failure → refusal; capture whatever prose exists
                return {"stop_reason": "refusal", "exit_status": "refusal",
                        "assistant": flatten_agent_messages(getattr(agent, "messages", []))}
```

Update `_run_agent_turn`'s signature to accept `prior` and its tail to return the dict. Change the method signature:

```python
    async def _run_agent_turn(self, loop, session_id, state, text, skill_block, prior) -> dict:
```

Replace the cancel/return tail (currently returns a `str`):

```python
        if state.cancel_flag.is_set():
            return {"stop_reason": "cancelled", "exit_status": "cancelled", "assistant": ""}
        engine = await loop.run_in_executor(None, run_engine)
        if state.cancel_flag.is_set():
            return {"stop_reason": "cancelled", "exit_status": "cancelled", "assistant": ""}
        return engine
```

(The caller in `prompt()` is updated in Task 7 — until then the agent branch will not typecheck-call correctly; Tasks 5 and 7 land together behaviorally but commit separately for review granularity. Run the targeted test only in Step 4.)

- [ ] **Step 4: Run the helper test + agent unit tests**

Run: `../../.venv/bin/python -m pytest tests/test_acp_session_context.py tests/test_tracing_agent.py -v`
Expected: PASS. (ACP-level tests that call `prompt()` come in Task 7/8.)

- [ ] **Step 5: Commit**

```bash
git add harness/acp_agent.py tests/test_acp_session_context.py
git commit -m "refactor(acp): run_engine returns structured agent result with flattened prose"
```

---

## Task 6: Router accepts history + preamble helper

**Files:**
- Modify: `harness/router.py`; `harness/transcript.py` (add `router_preamble`)
- Test: `tests/test_router.py`; `tests/test_transcript.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `router_preamble(history: list[dict]) -> str` in `harness/transcript.py`; `Router.classify(prompt: str, history: list[dict] | None = None) -> Classification`.

- [ ] **Step 1: Write the failing tests.**

Append to `tests/test_transcript.py`:

```python
from harness.transcript import router_preamble


def test_router_preamble_includes_user_and_chat_assistant_excludes_agent():
    history = [
        {"role": "user", "content": "Flutter or React Native?", "origin": "chat"},
        {"role": "assistant", "content": "Which target — Flutter or RN?", "origin": "chat"},
        {"role": "user", "content": "fix the test", "origin": "agent"},
        {"role": "assistant", "content": "I ran pytest, 2 failed: ...", "origin": "agent"},
    ]
    pre = router_preamble(history)
    assert "Flutter or React Native?" in pre        # user turn (chat)
    assert "Which target" in pre                     # chat assistant answer included
    assert "fix the test" in pre                     # user turn (agent) included
    assert "I ran pytest" not in pre                  # agent assistant narration EXCLUDED


def test_router_preamble_empty_history_is_empty():
    assert router_preamble([]) == ""
```

Append to `tests/test_router.py` (read the top for `_stub` / `_CATALOG`):

```python
def test_classify_includes_preamble_in_user_message():
    seen = {}

    def stub(system, user):
        seen["user"] = user
        return '{"task_type": "code_fix", "skills": [], "confidence": 0.9, "reasoning": "x"}'

    history = [{"role": "user", "content": "earlier ask", "origin": "chat"},
               {"role": "assistant", "content": "chat reply", "origin": "chat"},
               {"role": "assistant", "content": "agent narration", "origin": "agent"}]
    Router(stub, catalog=_CATALOG).classify("the first one", history=history)
    assert "earlier ask" in seen["user"]
    assert "chat reply" in seen["user"]
    assert "agent narration" not in seen["user"]
    assert "the first one" in seen["user"]            # current prompt remains the target


def test_classify_without_history_passes_bare_prompt():
    seen = {}

    def stub(system, user):
        seen["user"] = user
        return '{"task_type": "code_fix", "skills": [], "confidence": 0.9, "reasoning": "x"}'

    Router(stub, catalog=_CATALOG).classify("just this", history=None)
    assert seen["user"] == "just this"                # byte-for-byte unchanged
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `../../.venv/bin/python -m pytest tests/test_transcript.py tests/test_router.py -v`
Expected: FAIL — no `router_preamble`; `classify()` got unexpected kwarg `history`.

- [ ] **Step 3: Implement.**

Add to `harness/transcript.py`:

```python
def router_preamble(history: list[dict]) -> str:
    """Build a triage preamble from prior USER turns and CHAT assistant answers.
    Excludes agent-origin assistant narration (tool/pytest prose) so triage stays
    clean. Returns "" for empty history."""
    lines: list[str] = []
    for m in history:
        role, origin = m.get("role"), m.get("origin")
        if role == "user":
            lines.append(f"- user: {m.get('content', '')}")
        elif role == "assistant" and origin == "chat":
            lines.append(f"- assistant: {m.get('content', '')}")
    return "\n".join(lines)
```

Edit `harness/router.py`. Add the import near the top:

```python
from harness.transcript import router_preamble
```

Change `classify`:

```python
    def classify(self, prompt: str, history: list[dict] | None = None) -> Classification:
        user = prompt
        if history:
            preamble = router_preamble(history)
            if preamble:
                user = ("Recent context (for reference only):\n" + preamble +
                        "\n\nClassify THIS request: " + prompt)
        raw = self._complete(_system_prompt(self._catalog), user)
        ...   # rest of the method unchanged (parse `raw` exactly as before)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `../../.venv/bin/python -m pytest tests/test_transcript.py tests/test_router.py -v`
Expected: PASS (existing router tests stay green — they call `classify("x")` with no history).

- [ ] **Step 5: Commit**

```bash
git add harness/router.py harness/transcript.py tests/test_router.py tests/test_transcript.py
git commit -m "feat(router): classify accepts history; user+chat-answer preamble"
```

---

## Task 7: Orchestration — read context, dispatch, write back

Wire the transcript through every branch of `prompt()`. Apply the per-branch write rules from spec §6.

**Files:**
- Modify: `harness/acp_agent.py` (`prompt`, lines ~85-141; chat `pump`; agent branch)
- Test: `tests/test_acp_session_context.py`

**Interfaces:**
- Consumes: `SessionStore.extend` (T1), `answer_stream(history=)` (T3), `classify(history=)` (T6), `_run_agent_turn(..., prior)` returning a dict (T5).
- Produces: `prompt()` reads `state.transcript` once and writes per branch.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_acp_session_context.py`. These drive `prompt()` with a fake connection + stub router/handler. (Read `tests/test_acp_smoke.py` for the established `HarnessAgent` construction + fake-conn pattern and reuse it.)

```python
import asyncio
from harness.acp_session import SessionStore


def test_chat_turn_writes_user_and_assistant_with_chat_origin():
    # Build a HarnessAgent whose router returns chat_question and whose chat
    # handler yields a canned answer; drive prompt() once; assert the transcript.
    agent, conn = _harness_with_router(task_type="chat_question")   # helper per smoke-test pattern
    sid = agent._store.new(cwd=".")
    asyncio.run(agent.prompt([_text_block("what is X")], sid))
    t = agent._store.get(sid).transcript
    assert [(m["role"], m["origin"]) for m in t] == [("user", "chat"), ("assistant", "chat")]
    assert t[0]["content"] == "what is X"


def test_clarify_turn_writes_only_user_turn():
    agent, conn = _harness_with_router(task_type="ambiguous")
    sid = agent._store.new(cwd=".")
    asyncio.run(agent.prompt([_text_block("huh")], sid))
    t = agent._store.get(sid).transcript
    assert [(m["role"], m["origin"]) for m in t] == [("user", "clarify")]


def test_router_unavailable_writes_nothing():
    agent, conn = _harness_with_router(raise_in_classify=True)
    sid = agent._store.new(cwd=".")
    asyncio.run(agent.prompt([_text_block("x")], sid))
    assert agent._store.get(sid).transcript == []


def test_second_turn_receives_prior_transcript_in_classify():
    agent, conn = _harness_with_router(task_type="chat_question", capture_history=True)
    sid = agent._store.new(cwd=".")
    asyncio.run(agent.prompt([_text_block("first")], sid))
    asyncio.run(agent.prompt([_text_block("second")], sid))
    # the router stub recorded the history arg it last received
    assert any(m["content"] == "first" for m in conn.last_classify_history)
```

> Implement the `_harness_with_router`, `_text_block`, and fake-conn helpers at the top of the test file modeled on `tests/test_acp_smoke.py`. The fake router stub stores the `history` kwarg it was called with so `test_second_turn_...` can assert on it. Keep helpers minimal — no mocking beyond the injected router/handler/conn the smoke test already fakes.

- [ ] **Step 2: Run tests to verify they fail**

Run: `../../.venv/bin/python -m pytest tests/test_acp_session_context.py -v`
Expected: FAIL — transcript empty / wrong origins (orchestration not wired yet).

- [ ] **Step 3: Implement** — edit `prompt()` in `harness/acp_agent.py`.

Read the transcript once, right after computing `text` (after line 93):

```python
        transcript = state.transcript
```

Pass it to classify (line 97):

```python
            cls: Classification = await loop.run_in_executor(
                None, lambda: self._router.classify(text, history=transcript))
```

Clarify branch (replace the `record` at lines 111-112 — keep `record` for display history, add a transcript write of the user turn only):

```python
            self._store.record(session_id, {"prompt": text, "stop_reason": "end_turn",
                                            "kind": "clarify"})
            self._store.extend(session_id, [{"role": "user", "content": text, "origin": "clarify"}])
```

Chat branch — accumulate streamed pieces, then write the pair. Replace the `pump()` block (lines 118-127) and the following `record`:

```python
            pieces: list[str] = []

            def pump() -> None:
                for piece in handler.answer_stream(text, history=transcript):
                    pieces.append(piece)
                    asyncio.run_coroutine_threadsafe(
                        self._conn.session_update(session_id, message_chunk(piece)),
                        loop).result()

            await loop.run_in_executor(None, pump)
            answer = "".join(pieces)
            self._store.record(session_id, {"prompt": text, "stop_reason": "end_turn",
                                            "kind": "chat"})
            self._store.extend(session_id, [
                {"role": "user", "content": text, "origin": "chat"},
                {"role": "assistant", "content": answer, "origin": "chat"}])
```

Agent branch — pass `prior`, unpack the dict, write the pair with status fallback. Replace lines 138-141:

```python
        engine = await self._run_agent_turn(loop, session_id, state, text, load.block, transcript)
        stop_reason = engine["stop_reason"]
        assistant = engine["assistant"] or engine["exit_status"] or stop_reason   # never empty
        self._store.record(session_id, {"prompt": text, "stop_reason": stop_reason,
                                        "kind": "agent"})
        self._store.extend(session_id, [
            {"role": "user", "content": text, "origin": "agent"},
            {"role": "assistant", "content": assistant, "origin": "agent"}])
        return acp.PromptResponse(stop_reason=stop_reason)
```

(The `skill_load` metadata chunk at lines 135-137 is left untouched and is never written to the transcript — it's an empty-content meta chunk.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `../../.venv/bin/python -m pytest tests/test_acp_session_context.py tests/test_acp_smoke.py -v`
Expected: PASS — new orchestration tests green, and the pre-existing ACP smoke test still green (it asserts a tool_call happened and `[resumed]` history replay — both unaffected).

- [ ] **Step 5: Commit**

```bash
git add harness/acp_agent.py tests/test_acp_session_context.py
git commit -m "feat(acp): thread session transcript through router/chat/agent branches"
```

---

## Task 8: End-to-end multi-turn cross-path test

Prove the user-visible fix: a turn-1 chat answer is visible to a turn-2 agent run, and a turn-1 agent run is visible to a turn-2 chat.

**Files:**
- Test: `tests/test_acp_session_context.py`

**Interfaces:**
- Consumes: the wired `prompt()` (T7), the mock agent (`harness/models_mock.py`).

- [ ] **Step 1: Write the failing tests** — append:

```python
def test_chat_then_agent_sees_prior_chat_in_prior():
    # turn 1: chat. turn 2: agent. The agent's TracingAgent.run must receive
    # prior containing the turn-1 exchange. Capture prior via a fake model factory
    # that records the messages it's queried with.
    agent, conn = _harness_chat_then_agent()      # helper: router returns chat then code_fix
    sid = agent._store.new(cwd="examples/sample-repo")
    asyncio.run(agent.prompt([_text_block("what does add do?")], sid))
    asyncio.run(agent.prompt([_text_block("now fix it")], sid))
    # the captured agent-query messages include the turn-1 chat turns as prior
    queried = conn.captured_agent_messages
    contents = [m.get("content") for m in queried]
    assert any("what does add do?" in (c or "") for c in contents)


def test_agent_then_chat_sees_prior_agent_narration():
    agent, conn = _harness_agent_then_chat()
    sid = agent._store.new(cwd="examples/sample-repo")
    asyncio.run(agent.prompt([_text_block("fix the add bug")], sid))
    asyncio.run(agent.prompt([_text_block("what did you change?")], sid))
    # turn-2 chat handler received history including the turn-1 agent assistant turn
    hist = conn.last_chat_history
    assert any(m["origin"] == "agent" and m["role"] == "assistant" for m in hist)
```

> These reuse the mock model (`build_mock_model`) for the agent turn. The fake conn/model factory records what `answer_stream(history=)` and `TracingAgent.run(prior=)` received. Model the harness construction on `tests/test_acp_smoke.py` (it already runs the mock agent end-to-end through ACP). If exposing `captured_agent_messages` requires a thin spy model wrapper, add it in the test file only — do not change production code for testability.

- [ ] **Step 2: Run tests to verify they fail (or guide construction)**

Run: `../../.venv/bin/python -m pytest tests/test_acp_session_context.py -v`
Expected: FAIL initially (helpers/spies not yet built); iterate until the cross-path assertions pass against the wired `prompt()`.

- [ ] **Step 3: Implement** — build the `_harness_chat_then_agent` / `_harness_agent_then_chat` helpers and the spy model/handler in the test file. No production code changes expected; if a test reveals a real wiring gap, fix it in `acp_agent.py` (that's the test doing its job).

- [ ] **Step 4: Run the full suite**

Run: `../../.venv/bin/python -m pytest tests/ -v`
Expected: PASS — entire suite green, proving no regression across router, chat, agent, runner, ACP smoke, and the new context tests.

- [ ] **Step 5: Commit**

```bash
git add tests/test_acp_session_context.py
git commit -m "test(acp): end-to-end multi-turn cross-path context"
```

---

## Self-Review

**Spec coverage:**
- §1 data model → Task 1 (transcript field, validated `extend`). ✓
- §2 flatten + capture seam → Task 2 (`flatten_agent_messages`) + Task 5 (`run_engine` structured return). ✓
- §3 agent seed → Task 4 (`run(prior=)`, v2.4.2 divergence). ✓
- §4 chat streamed + history + accumulation → Task 3 (`history` param) + Task 7 (piece accumulation). ✓
- §5 router preamble (user + chat-assistant, exclude agent) → Task 6. ✓
- §6 orchestration write rules (router-unavailable=nothing, clarify=user-only, skill-meta never, chat/agent=pair, origins) → Task 7. ✓
- §7 bounding (none, params are the seam) → satisfied by construction (no bounding code; `prior`/`history` are the seams). ✓
- Error handling (partial turn, fallbacks) → Task 5 (except path flattens what exists) + Task 7 (`assistant` fallback never empty). ✓
- Backward compat → Tasks 3/4/6 each keep a "no-arg unchanged" assertion; full suite in Task 8. ✓
- Testing list → Tasks 1,2,3,4,6,7,8 cover every bullet. ✓

**Placeholder scan:** No TBD/TODO. Two tasks (7, 8) reference building test helpers "modeled on `test_acp_smoke.py`" rather than pasting them — this is intentional (the smoke-test fake-conn harness is large and codebase-specific); the assertions themselves are concrete. Flagged for the executor to read that file first.

**Type consistency:** `flatten_agent_messages(list[dict]) -> str`, `router_preamble(list[dict]) -> str`, `classify(prompt, history=None)`, `answer_stream(prompt, history=None)`, `run(task, prior=None)`, `_run_agent_turn(...) -> dict {stop_reason, assistant, exit_status}`, `extend(session_id, msgs)` — names and shapes consistent across all tasks. ✓

**Confidence gate:** Task 4 (the upstream-loop reimplementation) is the highest-risk task. Before merging, run a Codex rescue review on `harness/tracing_agent.py`'s new `run()` against `upstream/.../default.py:88-122` to confirm the loop is reproduced faithfully (esp. the exception branches and the `LimitsExceeded`/`TimeExceeded` ancestry note).
