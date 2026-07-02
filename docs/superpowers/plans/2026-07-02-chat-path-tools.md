# Chat Path Gains Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `chat_question` turn where the worker model wants to run a tool escalates to the existing gated agent loop instead of leaking raw `<tool_call>` text into the transcript; purely social turns stay fast and prose-only.

**Architecture:** On the interactive chat path only, run one throwaway non-streaming `litellm.completion` **with tools** (`ChatHandler.wants_tool`). If the model returns native `tool_calls`, skip the chat block and fall through to the existing agent path (gate + env + engine reused, single record site). If not — or if headless (no elicitation) — run today's streaming prose pump unchanged. The probe result is used ONLY as a boolean; its content is never reused (that would re-render the leak).

**Tech Stack:** Python 3.11+, litellm (v2.4.2, pinned), pytest. No new dependencies.

## Global Constraints

- **Test command (from worktree root):** `.venv/bin/python -m pytest tests/ -q` (target `tests/` only). The venv lives in the PRIMARY checkout; conftest (PR #94) prepends this worktree's src root, so use the primary's `.venv/bin/python`. Never a bare `.venv/bin/python` — this worktree has none of its own.
- **Fail-open probe:** any exception in `wants_tool` → return `False` → today's prose behavior. A probe failure must never crash a chat turn.
- **Headless never escalates:** escalation is gated on `has_elicitation` (client can show a permission modal). Headless/cron/CLI (`has_elicitation == False`) → probe skipped → prose-only. Authorization surface for unattended runs is unchanged.
- **Hermetic tests:** mock mode (`model_id is None`) must make NO litellm call. Tests must not reach a real proxy.
- **Single record site:** the tool hand-off must NOT add a second `_store.record`/`_store.extend`. Exactly one path records the turn (the agent path's existing tail).
- **No leak regression:** the prose pump's completion call carries no tools, so it cannot emit `<tool_call>`/`<arg_value>` text.

---

### Task 1: `ChatHandler.wants_tool` — the throwaway tools-probe

**Files:**
- Modify: `harness/chat_handler.py` (add `tool_schemas` ctor arg + `wants_tool` method)
- Test: `tests/test_chat_handler.py` (create if absent; else append)

**Interfaces:**
- Consumes: `harness.vibeproxy.model_id`, `harness.vibeproxy.completion_kwargs` (already used by `answer_stream`); a list of OpenAI tool schemas passed in at construction.
- Produces: `ChatHandler(worker_model_id, *, ..., tool_schemas: list[dict] | None = None)` and `ChatHandler.wants_tool(prompt: str, history: list[dict] | None = None, cancel_flag=None) -> bool`.

- [ ] **Step 1: Write the failing test — mock mode returns False, no litellm call**

Add to `tests/test_chat_handler.py`:

```python
import harness.chat_handler as ch_mod
from harness.chat_handler import ChatHandler


def test_wants_tool_mock_mode_returns_false_no_call(monkeypatch):
    """model_id=None (mock) must return False WITHOUT importing/calling litellm."""
    called = {"n": 0}

    def _boom(*a, **k):
        called["n"] += 1
        raise AssertionError("litellm.completion must not be called in mock mode")

    # If litellm were imported and called, this would trip. Patch defensively.
    import litellm
    monkeypatch.setattr(litellm, "completion", _boom)

    handler = ChatHandler(None, tool_schemas=[{"type": "function",
                          "function": {"name": "bash", "parameters": {}}}])
    assert handler.wants_tool("what's my setup?") is False
    assert called["n"] == 0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_chat_handler.py::test_wants_tool_mock_mode_returns_false_no_call -q`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'tool_schemas'` (or `AttributeError: 'ChatHandler' object has no attribute 'wants_tool'`).

- [ ] **Step 3: Add the `tool_schemas` ctor arg**

In `harness/chat_handler.py`, extend `ChatHandler.__init__` signature and store the schemas. Add `tool_schemas` as a keyword-only arg (after the existing ones) so existing positional calls are unaffected:

```python
    def __init__(self, worker_model_id: str | None,
                 catalog: list[tuple[str, str]] | None = None,
                 persona_block: str = "", base_block: str = "",
                 skipped: "list[tuple[str, str]] | None" = None,
                 shadowed: "list[tuple[str, str]] | None" = None,
                 *, tool_schemas: "list[dict] | None" = None):
        # ... existing body unchanged ...
        # Tool schemas (OpenAI function-tool dicts) for the wants_tool probe. None
        # => no probe possible (treated as "no tool"): keeps mock/CLI byte-identical.
        self._tool_schemas = tool_schemas or []
```

Place the assignment at the end of `__init__` (after `self._base_block = base_block`).

- [ ] **Step 4: Implement `wants_tool` (minimal, mock-first)**

Add this method to `ChatHandler` (below `answer_stream`):

```python
    def wants_tool(self, prompt: str,
                   history: list[dict] | None = None,
                   cancel_flag=None) -> bool:
        """True iff a tools-enabled probe of this turn returns native tool_calls.

        A THROWAWAY classifier: the response content is never reused (reusing it
        could re-render a text-format <tool_call> leak — the very bug we fix). We
        read only whether the model emitted structured tool_calls.

        Mock mode (no model) or no tool_schemas => False, with NO litellm call
        (hermetic; byte-identical mock behavior). Fail-open: any exception => False,
        so a probe failure degrades to today's prose pump, never a crash.
        """
        if self._model_id is None or not self._tool_schemas:
            return False
        try:
            import litellm  # lazy: keep the ~1s import off startup
            from harness import vibeproxy
            system_content = self._base_block + self._persona_block
            resp = run_interruptible(
                lambda: litellm.completion(
                    model=vibeproxy.model_id(self._model_id),
                    **vibeproxy.completion_kwargs(),
                    messages=(([{"role": "system", "content": system_content}]
                               if system_content else [])
                              + (history or []) + [{"role": "user", "content": prompt}]),
                    tools=self._tool_schemas,
                    tool_choice="auto",
                    max_tokens=256,
                    stream=False,
                ),
                cancel_flag,
            )
            msg = resp.choices[0].message
            return bool(getattr(msg, "tool_calls", None))
        except Exception:
            return False
```

Note: `run_interruptible` is already imported at module top (`from harness.interruptible import run_interruptible`).

- [ ] **Step 5: Run the mock-mode test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_chat_handler.py::test_wants_tool_mock_mode_returns_false_no_call -q`
Expected: PASS.

- [ ] **Step 6: Write the probe-boolean + fail-open tests**

Add:

```python
class _FakeMsg:
    def __init__(self, tool_calls):
        self.tool_calls = tool_calls

class _FakeChoice:
    def __init__(self, tool_calls):
        self.message = _FakeMsg(tool_calls)

class _FakeResp:
    def __init__(self, tool_calls):
        self.choices = [_FakeChoice(tool_calls)]


def _handler_with_model():
    return ChatHandler("glm-5.2",
                       persona_block="", base_block="You are an agent.",
                       tool_schemas=[{"type": "function",
                                      "function": {"name": "bash", "parameters": {}}}])


def test_wants_tool_true_when_tool_calls_present(monkeypatch):
    import litellm
    monkeypatch.setattr(litellm, "completion",
                        lambda *a, **k: _FakeResp([{"id": "tc1"}]))
    assert _handler_with_model().wants_tool("inspect my setup") is True


def test_wants_tool_false_when_no_tool_calls(monkeypatch):
    import litellm
    monkeypatch.setattr(litellm, "completion",
                        lambda *a, **k: _FakeResp(None))
    assert _handler_with_model().wants_tool("how are you?") is False


def test_wants_tool_fail_open_on_exception(monkeypatch):
    import litellm
    def _raise(*a, **k):
        raise RuntimeError("proxy down")
    monkeypatch.setattr(litellm, "completion", _raise)
    assert _handler_with_model().wants_tool("anything") is False
```

- [ ] **Step 7: Run all `wants_tool` tests**

Run: `.venv/bin/python -m pytest tests/test_chat_handler.py -q -k wants_tool`
Expected: PASS (4 passed).

- [ ] **Step 8: Commit**

```bash
git add harness/chat_handler.py tests/test_chat_handler.py
git commit -m "feat(chat): ChatHandler.wants_tool throwaway tools-probe (mock-safe, fail-open)"
```

---

### Task 2: `has_elicitation` helper + build tool schemas at ChatHandler construction

**Files:**
- Modify: `harness/acp_agent.py` (add a `_has_elicitation()` helper; wire `tool_schemas` into the `ChatHandler(...)` construction at `:540`)
- Test: `tests/test_acp_agent_streaming.py` (append)

**Interfaces:**
- Consumes: `self._client_caps` (`acp_agent.py:320`), `harness.tools.registry.build_registry`.
- Produces: `HarnessAgent._has_elicitation() -> bool`; `ChatHandler` now constructed with `tool_schemas=<registry schemas>`.

- [ ] **Step 1: Write the failing test for `_has_elicitation`**

Add to `tests/test_acp_agent_streaming.py`:

```python
def test_has_elicitation_reflects_client_caps():
    from harness.acp_agent import build_harness_agent
    import pathlib
    agent = build_harness_agent(
        model_factory=lambda *a, **k: None, agent_cfg=_agent_cfg(),
        skills_dir=pathlib.Path("skills"), router=_ChatRouter(),
        worker_model_id=None)

    class _Caps:
        def __init__(self, elicit): self.elicitation = elicit

    agent._client_caps = None
    assert agent._has_elicitation() is False
    agent._client_caps = _Caps(None)
    assert agent._has_elicitation() is False
    agent._client_caps = _Caps(object())
    assert agent._has_elicitation() is True
```

(`_agent_cfg` and `_ChatRouter` already exist in this test module — reuse them.)

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_acp_agent_streaming.py::test_has_elicitation_reflects_client_caps -q`
Expected: FAIL — `AttributeError: 'HarnessAgent' object has no attribute '_has_elicitation'`.

- [ ] **Step 3: Extract `_has_elicitation`, reuse it in the gate (DRY)**

In `harness/acp_agent.py`, add a method on `HarnessAgent` (near the other small helpers, e.g. above `prompt`):

```python
    def _has_elicitation(self) -> bool:
        """True when the connected client can show a permission modal. False for
        headless/cron/CLI (no elicitation) — the signal the permission gate uses
        to fail closed, reused to decide whether a chat_question may escalate to
        the tool-running agent path."""
        return not (
            self._client_caps is None
            or getattr(self._client_caps, "elicitation", None) is None
        )
```

Then replace the inline computation inside `check_permission` (`:747-750`) with a call:

```python
            has_elicitation = self._has_elicitation()
```

- [ ] **Step 4: Run the helper test + confirm gate still works**

Run: `.venv/bin/python -m pytest tests/test_acp_agent_streaming.py::test_has_elicitation_reflects_client_caps tests/test_permcheck.py -q`
Expected: PASS (helper test passes; permcheck tests unchanged/green).

- [ ] **Step 5: Wire `tool_schemas` into `ChatHandler` construction**

At `acp_agent.py:540`, build the same registry the agent path uses and pass its schemas. Insert just before the `handler = ChatHandler(...)` line:

```python
            from harness.tools.registry import build_registry as _build_registry
            _chat_registry = _build_registry(skill_roots=_skill_roots,
                                             memory_root=(ws.resolve() if ws else None))
            _chat_tool_schemas = [t.schema for t in _chat_registry]
```

Then add `tool_schemas=_chat_tool_schemas` to the `ChatHandler(...)` kwargs:

```python
            handler = ChatHandler(model_id, catalog=_catalog_load.skills,
                                  persona_block=(state.persona_block or "") + (state.memory_block or ""),
                                  base_block=base_block,
                                  skipped=_catalog_load.skipped,
                                  shadowed=_catalog_load.shadowed,
                                  tool_schemas=_chat_tool_schemas)
```

(`_skill_roots` and `ws` are already in scope at this point — see `:503-511`.)

- [ ] **Step 6: Run the streaming suite to confirm no regression**

Run: `.venv/bin/python -m pytest tests/test_acp_agent_streaming.py -q`
Expected: PASS (existing chat/agent streaming tests still green; ChatHandler ctor change is additive).

- [ ] **Step 7: Commit**

```bash
git add harness/acp_agent.py tests/test_acp_agent_streaming.py
git commit -m "feat(chat): _has_elicitation helper (DRY with gate) + tool_schemas into ChatHandler"
```

---

### Task 3: Escalation — probe before the chat block, fall through to the agent path

**Files:**
- Modify: `harness/acp_agent.py` (the dispatch region `:535`, guarded escalation before entering the chat block)
- Test: `tests/test_acp_agent_streaming.py` (append)

**Interfaces:**
- Consumes: `ChatHandler.wants_tool` (Task 1), `HarnessAgent._has_elicitation` (Task 2), the existing agent path (`:600+`, `_run_agent_turn`).
- Produces: escalation control flow — no new public symbol.

- [ ] **Step 1: Write the failing test — interactive tool intent escalates, records once**

Add to `tests/test_acp_agent_streaming.py`:

```python
def test_chat_tool_intent_escalates_to_agent_path(tmp_path, monkeypatch):
    """Interactive chat turn whose probe returns tool intent must run the AGENT
    path (not the chat pump) and record the turn exactly once as kind='agent'."""
    conn = RecordingConn()
    agent = build_harness_agent(
        model_factory=lambda *a, **k: None, agent_cfg=_agent_cfg(),
        skills_dir=__import__("pathlib").Path("skills"), router=_ChatRouter(),
        worker_model_id="glm-5.2")
    agent._conn = conn

    class _Caps:
        elicitation = object()
    agent._client_caps = _Caps()          # interactive
    sid = agent._store.new(cwd=str(tmp_path))

    import harness.acp_agent as mod
    # Force tool intent.
    monkeypatch.setattr(mod.ChatHandler, "wants_tool", lambda self, *a, **k: True)
    # Stub the agent path so we assert it was entered without running a real engine.
    entered = {"n": 0}
    async def _fake_run_agent_turn(self, *a, **k):
        entered["n"] += 1
        return {"stop_reason": "end_turn", "assistant": "ran ls", "exit_status": "",
                "streamed": ""}
    monkeypatch.setattr(mod.HarnessAgent, "_run_agent_turn", _fake_run_agent_turn)

    resp = _prompt_with_timeout(agent, sid, "what's my setup?")
    assert resp.stop_reason == "end_turn"
    assert entered["n"] == 1                      # agent path ran
    # recorded exactly once as agent (no double-record from chat tail).
    # SessionStore.record appends to state.history (plain list of turn dicts).
    kinds = [r.get("kind") for r in agent._store.get(sid).history]
    assert kinds.count("agent") == 1
    assert "chat" not in kinds
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_acp_agent_streaming.py::test_chat_tool_intent_escalates_to_agent_path -q`
Expected: FAIL — the chat block runs the pump (no escalation yet), so `entered["n"] == 0`.

- [ ] **Step 3: Add the escalation guard before the chat block**

In `harness/acp_agent.py`, immediately BEFORE `if cls.task_type == "chat_question":` (`:535`), insert the escalation decision. It must run only for chat turns, only when interactive, and only when NOT a deterministic short-circuit question (those answer from data, no model, no tools):

```python
        # Chat turns that want a tool escalate to the tool-running agent path.
        # Interactive only (headless/cron never escalates — authorization surface
        # for unattended runs is unchanged). Deterministic capability/tools
        # questions answer from data below, so they are never probed. The probe is
        # a throwaway boolean classifier (ChatHandler.wants_tool); on True we fall
        # through to the agent path (single record site, gate + engine reused).
        if (cls.task_type == "chat_question" and self._has_elicitation()
                and not chat_handler.is_tools_question(text)):
            _probe_handler = ChatHandler(
                model_id, base_block=base_block,
                persona_block=(state.persona_block or "") + (state.memory_block or ""),
                tool_schemas=[t.schema for t in
                              __import__("harness.tools.registry", fromlist=["build_registry"])
                              .build_registry(skill_roots=_skill_roots,
                                              memory_root=(ws.resolve() if ws else None))])
            if state.cancel_flag.is_set():
                return _cancelled()
            wants = await loop.run_in_executor(
                None, lambda: _probe_handler.wants_tool(
                    text, history=transcript, cancel_flag=state.cancel_flag))
            if wants:
                cls.task_type = "code_feature"   # route to the agent path below
```

Then the existing `if cls.task_type == "chat_question":` block is skipped
(because we reassigned `task_type`), and control reaches the agent path at
`:600+` which records exactly once.

IMPLEMENTATION NOTES:
- `import harness.chat_handler as chat_handler` must be available in the module
  (it already imports `from harness.chat_handler import ChatHandler`; add
  `from harness.chat_handler import ChatHandler, is_tools_question` OR
  `import harness.chat_handler as chat_handler` — pick one and use it
  consistently; the test in Step 1 patches `mod.ChatHandler`, so keep the
  `ChatHandler` name importable at module scope).
- The `__import__(...)` inline is ugly; prefer a top-of-file
  `from harness.tools.registry import build_registry` and use it directly. Use
  whichever keeps the diff clean; the plan shows the inline form only to avoid
  assuming import placement.
- `cls.task_type = "code_feature"` is the minimal reroute. Confirm `code_feature`
  reaches the agent path (any non-`chat_question`, non-clarify, non-ambiguous
  value does — see `:479` and `:535`). If a more neutral value exists (e.g. the
  router's generic agent type), use it; `code_feature` is a safe default.

- [ ] **Step 4: Run the escalation test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_acp_agent_streaming.py::test_chat_tool_intent_escalates_to_agent_path -q`
Expected: PASS.

- [ ] **Step 5: Write the headless-never-escalates test**

```python
def test_chat_headless_never_escalates(tmp_path, monkeypatch):
    """Headless (no elicitation): even with tool intent, the chat pump runs and
    the agent path is never entered."""
    conn = RecordingConn()
    agent = build_harness_agent(
        model_factory=lambda *a, **k: None, agent_cfg=_agent_cfg(),
        skills_dir=__import__("pathlib").Path("skills"), router=_ChatRouter(),
        worker_model_id="glm-5.2")
    agent._conn = conn
    agent._client_caps = None             # headless
    sid = agent._store.new(cwd=str(tmp_path))

    import harness.acp_agent as mod
    monkeypatch.setattr(mod.ChatHandler, "wants_tool",
                        lambda self, *a, **k: (_ for _ in ()).throw(
                            AssertionError("probe must not run headless")))
    entered = {"n": 0}
    async def _fake_run_agent_turn(self, *a, **k):
        entered["n"] += 1
        return {"stop_reason": "end_turn", "assistant": "x", "exit_status": "", "streamed": ""}
    monkeypatch.setattr(mod.HarnessAgent, "_run_agent_turn", _fake_run_agent_turn)
    # Chat pump: force a short prose answer regardless of model.
    class _FakeHandler:
        def __init__(self, *a, **k): pass
        def answer_stream(self, text, history=None, cancel_flag=None):
            yield "hi there"
    monkeypatch.setattr(mod, "ChatHandler", _FakeHandler)

    resp = _prompt_with_timeout(agent, sid, "what's my setup?")
    assert resp.stop_reason == "end_turn"
    assert entered["n"] == 0               # agent path never entered
```

NOTE: patching `mod.ChatHandler` to `_FakeHandler` AFTER the escalation guard
constructs its own `ChatHandler` is fine here because the guard is skipped
entirely when headless (short-circuits on `_has_elicitation()` False before
constructing anything). That is exactly the behavior under test.

- [ ] **Step 6: Run the headless test**

Run: `.venv/bin/python -m pytest tests/test_acp_agent_streaming.py::test_chat_headless_never_escalates -q`
Expected: PASS.

- [ ] **Step 7: Write the no-leak regression test**

```python
def test_chat_prose_never_leaks_tool_call_text(tmp_path, monkeypatch):
    """A prose chat turn (no tool intent) streams an answer with no <tool_call>
    or <arg_value> residue."""
    conn = RecordingConn()
    agent = build_harness_agent(
        model_factory=lambda *a, **k: None, agent_cfg=_agent_cfg(),
        skills_dir=__import__("pathlib").Path("skills"), router=_ChatRouter(),
        worker_model_id="glm-5.2")
    agent._conn = conn
    class _Caps: elicitation = object()
    agent._client_caps = _Caps()
    sid = agent._store.new(cwd=str(tmp_path))

    import harness.acp_agent as mod
    monkeypatch.setattr(mod.ChatHandler, "wants_tool", lambda self, *a, **k: False)
    class _FakeHandler:
        def __init__(self, *a, **k): pass
        def answer_stream(self, text, history=None, cancel_flag=None):
            yield "Doing well, Alberto."
    monkeypatch.setattr(mod, "ChatHandler", _FakeHandler)

    resp = _prompt_with_timeout(agent, sid, "how are you?")
    assert resp.stop_reason == "end_turn"
    joined = "".join(conn.message_texts())
    assert "<tool_call>" not in joined and "arg_value" not in joined
    assert "Doing well" in joined
```

NOTE: the escalation guard constructs a real `ChatHandler` for the probe BEFORE
`mod.ChatHandler` is patched to `_FakeHandler`? No — monkeypatch runs before
`prompt`. But the guard references the module-level `ChatHandler` name. Ensure
the guard uses the same name the test patches (`mod.ChatHandler`). If the guard
imported `ChatHandler` into a local name at module load, patching `mod.ChatHandler`
still affects it only if the guard looks it up as `ChatHandler` at call time
(module global). Keep the guard referencing the module-global `ChatHandler` (do
NOT alias it to a local) so the test's patch applies. Since `wants_tool` is
patched to return False, the probe's ChatHandler identity doesn't matter here;
the pump's handler is what must be `_FakeHandler`.

- [ ] **Step 8: Run the regression test**

Run: `.venv/bin/python -m pytest tests/test_acp_agent_streaming.py::test_chat_prose_never_leaks_tool_call_text -q`
Expected: PASS.

- [ ] **Step 9: Run the full streaming + chat suites**

Run: `.venv/bin/python -m pytest tests/test_acp_agent_streaming.py tests/test_chat_handler.py -q`
Expected: PASS (all green).

- [ ] **Step 10: Commit**

```bash
git add harness/acp_agent.py tests/test_acp_agent_streaming.py
git commit -m "feat(chat): escalate tool-intent chat turns to the gated agent path"
```

---

### Task 4: Full-suite verification

**Files:** none (verification only).

- [ ] **Step 1: Run the whole test suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS. Any failure that also fails on `main` (baseline flake — see project memory: `test_system_skills` spine + tui snapshot ordering) is pre-existing; note it and do not attribute it to this change. Confirm by stashing the branch diff and re-running the failing test on `main` if in doubt.

- [ ] **Step 2: Manual end-to-end sanity (optional, if a live proxy is available)**

Drive a chat turn "what's my setup?" against `--model vibeproxy` with GLM-5.2 in the interactive TUI; confirm the permission modal appears for `ls` (escalation worked) and no `<tool_call>` text leaks. A social "how are you" streams prose with no modal.

- [ ] **Step 3: Commit any test-only adjustments** (if Step 1 required assertion fixes for the store accessor).

```bash
git add -A && git commit -m "test(chat): finalize store-accessor assertions for chat-tool escalation"
```

---

## Self-Review

**Spec coverage:**
- Component 1 (`wants_tool`, mock-safe, fail-open, deterministic short-circuit precedence) → Task 1 + the `is_tools_question` guard in Task 3 Step 3. ✅
- Component 2 (single-record escalation via reassign `task_type` before chat block) → Task 3. ✅
- Component 3 (no double-injection of `text`) → guaranteed by escalating before the chat tail; asserted by "no `chat` record" in Task 3 Step 1. ✅
- Headless policy (`has_elicitation`) → Task 2 (`_has_elicitation`) + Task 3 guard + Task 3 Step 5 test. ✅
- Error handling (fail-open, cancel) → Task 1 Step 6 (fail-open) + `run_interruptible` in `wants_tool` + `cancel_flag.is_set()` check in the guard. ✅
- Testing section → Tasks 1/2/3 tests map 1:1 to the spec's test list. ✅

**Placeholder scan:** No TBD/TODO. The two NOTES in Task 3 (store accessor name; module-global `ChatHandler` lookup) are explicit, bounded implementation cautions with the load-bearing assertion called out — not deferred work.

**Type consistency:** `wants_tool(prompt, history, cancel_flag) -> bool` used identically in Task 1 (def), Task 3 (call + patch). `_has_elicitation() -> bool` defined Task 2, used Task 3. `tool_schemas` kwarg name consistent across Task 1 (def), Task 2 (wire), Task 3 (probe construct). ✅
