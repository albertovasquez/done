"""Session-context tests: the transcript carried across turns, in-process.

These drive HarnessAgent.prompt() directly (no subprocess) with an injected fake
router and a fake connection that records session_update calls. This lets us
assert on state.transcript and on what `prior`/`history` each path received —
the user-visible "follow-ups are understood" behavior.
"""

import asyncio


import acp

from harness.acp_agent import build_harness_agent
from harness.router import Classification
from harness.transcript import flatten_agent_messages


# --------------------------------------------------------------------------
# Task 2/5 contract smoke: flatten excludes tool/exit structure
# --------------------------------------------------------------------------

def test_flatten_used_for_agent_capture_smoke():
    messages = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "TASK"},
        {"role": "assistant", "content": "Working on it."},
        {"role": "tool", "content": "<returncode>0</returncode>"},
        {"role": "exit", "content": "", "extra": {"exit_status": "Submitted", "submission": "done"}},
    ]
    assert flatten_agent_messages(messages) == "Working on it.\n\ndone"


# --------------------------------------------------------------------------
# In-process harness scaffolding
# --------------------------------------------------------------------------

class _FakeConn:
    """Records session_update calls; services request_permission as allow."""

    def __init__(self):
        self.updates = []

    async def session_update(self, session_id, update, **kw):
        self.updates.append(update)


class _ScriptedRouter:
    """Returns a queued Classification per classify() call, recording the
    `history` it was handed each time."""

    def __init__(self, classifications):
        self._queue = list(classifications)
        self.history_seen = []   # the history arg of each classify call, in order
        self.catalog = []        # mirrors Router.catalog (chat path reads it)

    def classify(self, prompt, history=None):
        self.history_seen.append(list(history) if history else [])
        return self._queue.pop(0)


def _chat(cls_skills=None):
    return Classification(task_type="chat_question", skills=cls_skills or [], confidence=1.0)


def _agent_fix():
    # code_fix routes to the agent path; skills empty so no skill compose noise
    return Classification(task_type="code_fix", skills=[], confidence=1.0)


def _ambiguous():
    return Classification(task_type="ambiguous", confidence=0.2, needs_clarification=True,
                          clarifying_question="Could you clarify?")


def _build(router, worker_model_id=None):
    """Mock-mode harness (no real worker model). worker_model_id=None => chat
    path yields the honest mock line; agent path uses the deterministic mock model."""
    from harness import acp_main
    from pathlib import Path
    agent = build_harness_agent(
        model_factory=acp_main._model_factory("mock"),
        agent_cfg=acp_main._agent_config() if hasattr(acp_main, "_agent_config") else _agent_cfg(),
        skills_dir=Path("skills"),
        router=router,
        worker_model_id=worker_model_id,
        backend="mock",   # consistent with the mock factory: session-model resolution
                          # returns None (no real worker model), so the chat path never
                          # makes a live litellm call to the proxy (hermetic test).
    )
    agent._conn = _FakeConn()
    agent._client_caps = None    # standalone: auto-allow, no terminal delegation
    return agent


def _agent_cfg():
    import yaml
    from pathlib import Path
    return yaml.safe_load(Path("upstream/src/minisweagent/config/mini.yaml").read_text())["agent"]


def _prompt(agent, sid, text):
    return asyncio.run(agent.prompt([acp.text_block(text)], sid))


# --------------------------------------------------------------------------
# Task 7: per-branch write rules
# --------------------------------------------------------------------------

def test_chat_turn_writes_user_and_assistant_with_chat_origin():
    router = _ScriptedRouter([_chat()])
    agent = _build(router)
    sid = agent._store.new(cwd=".")
    _prompt(agent, sid, "what is X")
    t = agent._store.get(sid).transcript
    assert [(m["role"], m["origin"]) for m in t] == [("user", "chat"), ("assistant", "chat")]
    assert t[0]["content"] == "what is X"
    assert t[1]["content"]                    # the mock honest line, non-empty


def test_clarify_turn_writes_only_user_turn():
    router = _ScriptedRouter([_ambiguous()])
    agent = _build(router)
    sid = agent._store.new(cwd=".")
    _prompt(agent, sid, "huh")
    t = agent._store.get(sid).transcript
    assert [(m["role"], m["origin"]) for m in t] == [("user", "clarify")]
    assert t[0]["content"] == "huh"


def test_router_unavailable_writes_nothing():
    class _Boom:
        def classify(self, prompt, history=None):
            raise RuntimeError("vibeproxy down")
    agent = _build(_Boom())
    sid = agent._store.new(cwd=".")
    resp = _prompt(agent, sid, "x")
    assert resp.stop_reason == "refusal"
    assert agent._store.get(sid).transcript == []


def test_persona_chip_not_emitted_on_router_down_then_emitted_next_turn(tmp_path):
    # DOCUMENTED behavior (decided after a Codex review): the persona chip emits
    # AFTER classification, so a router-down turn shows no chip — but the
    # once-per-session flag is NOT set, so the chip appears on the next turn that
    # classifies. The indicator tracks a functioning session, not a dead turn.
    ws = tmp_path / "agents" / "fred"
    ws.mkdir(parents=True)

    class _BoomThenChat:
        def __init__(self):
            self._calls = 0
            self.catalog = []          # chat path reads router.catalog

        def classify(self, prompt, history=None):
            self._calls += 1
            if self._calls == 1:
                raise RuntimeError("vibeproxy down")
            return _chat()             # a fresh chat classification on the retry

    conn, agent, sid = _build_with_workspace(ws, router=_BoomThenChat())
    _prompt(agent, sid, "first (router down)")
    assert _persona_ids(conn) == []                 # nothing on the failed turn
    _prompt(agent, sid, "second (router back)")
    assert _persona_ids(conn) == ["fred"]           # appears on the next working turn


def test_agent_engine_construction_failure_is_refusal_not_unbound():
    # If TracingAgent construction raises, run_engine's except must not itself
    # raise UnboundLocalError on `agent`. Force it via a model factory that throws.
    router = _ScriptedRouter([_agent_fix()])
    agent = _build(router)
    agent._model_factory = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    sid = agent._store.new(cwd=".")
    resp = _prompt(agent, sid, "fix it")
    assert resp.stop_reason == "refusal"
    # the turn is still recorded (user + a non-empty assistant fallback)
    t = agent._store.get(sid).transcript
    assert [(m["role"], m["origin"]) for m in t] == [("user", "agent"), ("assistant", "agent")]
    assert t[1]["content"]                    # fallback (exit_status/stop_reason), never empty


def test_second_turn_passes_prior_transcript_to_classify():
    router = _ScriptedRouter([_chat(), _chat()])
    agent = _build(router)
    sid = agent._store.new(cwd=".")
    _prompt(agent, sid, "first")
    _prompt(agent, sid, "second")
    # classify on turn 2 must have received turn-1's user+assistant in history
    assert router.history_seen[0] == []                       # turn 1: empty
    assert any(m["content"] == "first" for m in router.history_seen[1])


# --------------------------------------------------------------------------
# Task 8: end-to-end cross-path context
# --------------------------------------------------------------------------

def test_chat_then_agent_sees_prior_chat_in_classify_history(tmp_path):
    # turn 1 chat, turn 2 agent. The agent-turn classify must see the turn-1 chat.
    import shutil
    repo = tmp_path / "sample-repo"
    shutil.copytree("examples/sample-repo", repo)   # never mutate the checked-in fixture
    router = _ScriptedRouter([_chat(), _agent_fix()])
    agent = _build(router)
    sid = agent._store.new(cwd=str(repo))
    _prompt(agent, sid, "what does add do?")
    _prompt(agent, sid, "now fix it")
    # router.history_seen[1] is the context the agent turn was classified with
    hist = router.history_seen[1]
    assert any("what does add do?" in m["content"] for m in hist)
    # and the agent turn itself was written to the transcript
    t = agent._store.get(sid).transcript
    assert [(m["role"], m["origin"]) for m in t][-2:] == [("user", "agent"), ("assistant", "agent")]


def test_agent_then_chat_transcript_carries_agent_narration():
    # turn 1 agent (mock fixes the bug), turn 2 chat. The transcript after turn 1
    # must hold the agent assistant narration so turn-2 chat has context.
    import shutil, tempfile, os
    repo = tempfile.mkdtemp()
    shutil.copytree("examples/sample-repo", repo, dirs_exist_ok=True)
    router = _ScriptedRouter([_agent_fix(), _chat()])
    agent = _build(router)
    sid = agent._store.new(cwd=repo)
    _prompt(agent, sid, "fix the add bug")
    t_after_agent = agent._store.get(sid).transcript
    agent_assistant = [m for m in t_after_agent if m["origin"] == "agent" and m["role"] == "assistant"]
    assert agent_assistant, "agent turn must write an assistant narration"
    assert agent_assistant[0]["content"].strip()        # non-empty prose

    _prompt(agent, sid, "what did you change?")
    # turn-2 chat classify saw the agent turn in history
    hist = router.history_seen[1]
    assert any(m["origin"] == "agent" for m in hist)


# --------------------------------------------------------------------------
# Task 7 (integration): persona wiring into HarnessAgent
# --------------------------------------------------------------------------

def test_persona_reaches_chat_path(tmp_path, monkeypatch):
    # workspace with a SOUL.md -> chat path must carry it as a system message
    ws = tmp_path / "ws"; ws.mkdir()
    (ws / "SOUL.md").write_text("BE TERSE", encoding="utf-8")

    captured = {}
    def fake_completion(**kwargs):
        captured.update(kwargs)
        return iter([])                       # empty stream -> empty answer, fine
    import litellm
    monkeypatch.setattr(litellm, "completion", fake_completion)

    agent = _build(_ScriptedRouter([_chat()]), worker_model_id="gpt-5.4")
    agent._workspace_dir = ws                 # inject the test workspace
    sid = asyncio.run(agent.new_session(cwd=".")).session_id
    _prompt(agent, sid, "hi")                 # _prompt already runs asyncio.run
    sysmsg = captured["messages"][0]
    assert sysmsg["role"] == "system"
    assert "BE TERSE" in sysmsg["content"]
    # content is base_block + persona_block (base prepended); verify persona present
    assert agent._store.get(sid).persona_block in sysmsg["content"]


def test_empty_workspace_is_byte_identical_chat(tmp_path, monkeypatch):
    captured = {}
    def fake_completion(**kwargs):
        captured.update(kwargs)
        return iter([])
    import litellm
    monkeypatch.setattr(litellm, "completion", fake_completion)

    agent = _build(_ScriptedRouter([_chat()]), worker_model_id="gpt-5.4")
    agent._workspace_dir = tmp_path / "absent"    # no persona (absent dir)
    sid = asyncio.run(agent.new_session(cwd=".")).session_id
    _prompt(agent, sid, "hi")
    # base_block is always rendered on the ACP chat path (non-empty); a system
    # message is present even without a persona. The user turn comes last.
    assert captured["messages"][-1] == {"role": "user", "content": "hi"}
    assert captured["messages"][0]["role"] == "system"  # base_block present


def test_persona_load_event_gated_off_for_empty_workspace(tmp_path):
    # empty/absent workspace -> NO persona_load field_meta event on the conn
    agent = _build(_ScriptedRouter([_chat()]), worker_model_id=None)  # mock chat line
    agent._workspace_dir = tmp_path / "absent"
    sid = asyncio.run(agent.new_session(cwd=".")).session_id
    _prompt(agent, sid, "hi")
    # with_meta sets update.field_meta; assert no update has persona_load in it
    assert not any(
        isinstance(getattr(u, "field_meta", None), dict)
        and "persona_load" in getattr(u, "field_meta", {}).get("harness", {})
        for u in agent._conn.updates
    )


def _meta_keys_in_order(agent):
    """The _meta payload keys (task_classified, skill_load, persona_load, …) in
    the order they were emitted on the fake conn — for asserting stream order."""
    keys = []
    for u in agent._conn.updates:
        fm = getattr(u, "field_meta", None)
        if isinstance(fm, dict):
            keys += list(fm.get("harness", {}).keys())
    return keys


def test_persona_load_emits_after_task_classified_on_agent_turn(tmp_path):
    # populated workspace + agent dispatch -> persona_load fires, AFTER
    # task_classified (the Codex-found ordering fix), once on the first turn.
    ws = tmp_path / "ws"; ws.mkdir()
    (ws / "SOUL.md").write_text("BE TERSE", encoding="utf-8")
    agent = _build(_ScriptedRouter([_agent_fix()]), worker_model_id=None)
    agent._workspace_dir = ws
    sid = asyncio.run(agent.new_session(cwd=".")).session_id
    _prompt(agent, sid, "fix the bug")
    keys = _meta_keys_in_order(agent)
    assert "persona_load" in keys
    assert keys.index("task_classified") < keys.index("persona_load")  # ordered after


def test_persona_load_not_emitted_on_clarify_turn(tmp_path):
    # populated workspace but the turn is ambiguous -> NO persona_load (clarify and
    # ambiguous are unpersonalized; persona was composed but the event is skipped).
    ws = tmp_path / "ws"; ws.mkdir()
    (ws / "SOUL.md").write_text("BE TERSE", encoding="utf-8")
    agent = _build(_ScriptedRouter([_ambiguous()]), worker_model_id=None)
    agent._workspace_dir = ws
    sid = asyncio.run(agent.new_session(cwd=".")).session_id
    _prompt(agent, sid, "uh")
    assert "persona_load" not in _meta_keys_in_order(agent)
    # but persona WAS composed and cached on this turn (so a later agent turn in
    # the same session reuses it without re-reading disk) — compose is not skipped,
    # only the telemetry emit is.
    assert "BE TERSE" in agent._store.get(sid).persona_block


def test_persona_load_emits_on_first_personalized_turn_after_clarify(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    (ws / "SOUL.md").write_text("BE TERSE", encoding="utf-8")
    agent = _build(_ScriptedRouter([_ambiguous(), _agent_fix()]), worker_model_id=None)
    agent._workspace_dir = ws
    sid = asyncio.run(agent.new_session(cwd=".")).session_id

    _prompt(agent, sid, "uh")
    assert "persona_load" not in _meta_keys_in_order(agent)

    _prompt(agent, sid, "fix the bug")
    keys = _meta_keys_in_order(agent)
    assert keys.count("persona_load") == 1
    assert keys.index("task_classified", 1) < keys.index("persona_load")


# --------------------------------------------------------------------------
# Task 6 (integration): memory wiring into HarnessAgent
# --------------------------------------------------------------------------

def test_memory_reaches_agent_path(tmp_path, monkeypatch):
    # a workspace with MEMORY.md content -> the memory_block actually reaches the
    # TracingAgent (not just cached on SessionState). Spy on the TracingAgent ctor.
    ws = tmp_path / "ws"; ws.mkdir()
    (ws / "MEMORY.md").write_text("REMEMBER: prefers tabs", encoding="utf-8")
    # run_engine does `from harness.tracing_agent import TracingAgent` (lazy), so
    # patch the SOURCE module — that is the binding the lazy import resolves.
    import harness.tracing_agent as tamod
    captured = {}
    real_TA = tamod.TracingAgent
    def spy_tracing(*a, **k):
        captured.update(k)
        return real_TA(*a, **k)
    monkeypatch.setattr(tamod, "TracingAgent", spy_tracing)

    agent = _build(_ScriptedRouter([_agent_fix()]), worker_model_id=None)
    agent._workspace_dir = ws
    sid = asyncio.run(agent.new_session(cwd=str(tmp_path))).session_id
    _prompt(agent, sid, "do a thing")
    # cached on the session AND threaded into the TracingAgent
    assert "REMEMBER: prefers tabs" in (agent._store.get(sid).memory_block or "")
    assert "REMEMBER: prefers tabs" in captured.get("memory_block", "")


def test_base_block_reaches_agent_path(tmp_path, monkeypatch):
    # base_block must be rendered and threaded into the TracingAgent on the ACP
    # coding path — not just the chat path. Spy on the TracingAgent ctor.
    import harness.tracing_agent as tamod
    captured = {}
    real_TA = tamod.TracingAgent
    def spy_tracing(*a, **k):
        captured.update(k)
        return real_TA(*a, **k)
    monkeypatch.setattr(tamod, "TracingAgent", spy_tracing)

    agent = _build(_ScriptedRouter([_agent_fix()]), worker_model_id=None)
    sid = asyncio.run(agent.new_session(cwd=str(tmp_path))).session_id
    _prompt(agent, sid, "do a thing")
    base_block = captured.get("base_block", "")
    assert base_block, "base_block was empty — not threaded into TracingAgent"
    assert "authorized security testing" in base_block.lower()


def test_persona_and_memory_resolve_from_same_session_workspace(tmp_path):
    # Codex regression: persona must resolve from state.workspace_dir (per session),
    # not self._workspace_dir — so if the agent's workspace changes between
    # new_session and prompt, the session keeps ITS recorded workspace for BOTH
    # persona and memory (no mixed context).
    wsA = tmp_path / "A"; wsA.mkdir()
    (wsA / "SOUL.md").write_text("A-SOUL", encoding="utf-8")
    (wsA / "MEMORY.md").write_text("A-MEM", encoding="utf-8")
    wsB = tmp_path / "B"; wsB.mkdir()
    (wsB / "SOUL.md").write_text("B-SOUL", encoding="utf-8")

    agent = _build(_ScriptedRouter([_agent_fix()]), worker_model_id=None)
    agent._workspace_dir = wsA
    sid = asyncio.run(agent.new_session(cwd=str(tmp_path))).session_id  # records wsA
    agent._workspace_dir = wsB        # agent moves AFTER the session was created
    _prompt(agent, sid, "do a thing")
    st = agent._store.get(sid)
    # both persona and memory came from wsA (the session's recorded workspace)
    assert "A-SOUL" in (st.persona_block or "")
    assert "B-SOUL" not in (st.persona_block or "")
    assert "A-MEM" in (st.memory_block or "")


def test_memory_load_event_gated_off_for_empty_memory(tmp_path):
    # seeded-but-empty (no memory content) -> NO memory_load event (the no-op)
    ws = tmp_path / "ws"; ws.mkdir()
    (ws / "SOUL.md").write_text("BE TERSE", encoding="utf-8")   # persona but no memory
    agent = _build(_ScriptedRouter([_agent_fix()]), worker_model_id=None)
    agent._workspace_dir = ws
    sid = asyncio.run(agent.new_session(cwd=str(tmp_path))).session_id
    _prompt(agent, sid, "do a thing")
    assert "memory_load" not in _meta_keys_in_order(agent)


def test_memory_load_emits_after_task_classified(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    (ws / "MEMORY.md").write_text("durable fact", encoding="utf-8")
    agent = _build(_ScriptedRouter([_agent_fix()]), worker_model_id=None)
    agent._workspace_dir = ws
    sid = asyncio.run(agent.new_session(cwd=str(tmp_path))).session_id
    _prompt(agent, sid, "do a thing")
    keys = _meta_keys_in_order(agent)
    assert "memory_load" in keys
    assert keys.index("task_classified") < keys.index("memory_load")


def test_seeded_default_workspace_injects_bob_persona(monkeypatch, tmp_path):
    # the shipped default ships with the "Bob" soul -> the chat path renders a
    # system message carrying it AND a persona_load event fires.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from harness import persona, paths
    persona.seed_default_workspace()

    captured = {}
    def fake_completion(**kwargs):
        captured.update(kwargs)
        return iter([])
    import litellm
    monkeypatch.setattr(litellm, "completion", fake_completion)

    agent = _build(_ScriptedRouter([_chat()]), worker_model_id="gpt-5.4")
    agent._workspace_dir = paths.default_workspace_dir()   # the SEEDED dir
    sid = asyncio.run(agent.new_session(cwd=".")).session_id
    _prompt(agent, sid, "hi")
    assert captured["messages"][-1] == {"role": "user", "content": "hi"}
    assert captured["messages"][0]["role"] == "system"  # base_block present
    assert "You're Bob." in captured["messages"][0]["content"]  # persona injected
    assert "persona_load" in _meta_keys_in_order(agent)


def test_seeded_default_workspace_has_no_memory(monkeypatch, tmp_path):
    # the seeded default ships a persona (Bob) but NO memory content, so a
    # persona_load fires while memory_load does not.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from harness import persona, paths
    persona.seed_default_workspace()
    captured = {}
    def fake_completion(**kwargs):
        captured.update(kwargs); return iter([])
    import litellm
    monkeypatch.setattr(litellm, "completion", fake_completion)
    agent = _build(_ScriptedRouter([_chat()]), worker_model_id="gpt-5.4")
    agent._workspace_dir = paths.default_workspace_dir()
    sid = asyncio.run(agent.new_session(cwd=".")).session_id
    _prompt(agent, sid, "hi")
    # base_block is always rendered on the ACP chat path; system message present.
    assert captured["messages"][-1] == {"role": "user", "content": "hi"}
    assert captured["messages"][0]["role"] == "system"  # base_block present
    keys = _meta_keys_in_order(agent)
    assert "persona_load" in keys and "memory_load" not in keys


# --------------------------------------------------------------------------
# C2a: persona identity chip (engine-side)
# --------------------------------------------------------------------------

def _build_with_workspace(ws, router=None):
    """Build a mock-mode agent with a given workspace_dir (or None for default).
    Returns (conn, agent, sid) where sid is already registered in the store."""
    if router is None:
        router = _ScriptedRouter([_chat()])
    agent = _build(router)
    agent._workspace_dir = ws
    sid = asyncio.run(agent.new_session(cwd=".")).session_id
    conn = agent._conn
    return conn, agent, sid


def _persona_ids(conn):
    """The persona ids emitted on conn across all turns so far."""
    out = []
    for u in conn.updates:
        fm = getattr(u, "field_meta", None)
        h = fm.get("harness") if isinstance(fm, dict) else None
        p = h.get("persona") if isinstance(h, dict) else None
        if isinstance(p, dict) and isinstance(p.get("id"), str):
            out.append(p["id"])
    return out


def test_emits_persona_chip_once_with_resolved_id(tmp_path):
    ws = tmp_path / "agents" / "fred"; ws.mkdir(parents=True)
    conn, agent, sid = _build_with_workspace(ws)
    _prompt(agent, sid, "what is X")
    assert _persona_ids(conn) == ["fred"]


def test_persona_chip_not_re_emitted_second_turn(tmp_path):
    ws = tmp_path / "agents" / "fred"; ws.mkdir(parents=True)
    # two turns on the same session — chip must fire only once
    conn, agent, sid = _build_with_workspace(ws, router=_ScriptedRouter([_chat(), _chat()]))
    _prompt(agent, sid, "first")
    _prompt(agent, sid, "second")
    assert _persona_ids(conn) == ["fred"]          # once across both turns


def test_persona_chip_defaults_when_no_workspace():
    conn, agent, sid = _build_with_workspace(None)
    _prompt(agent, sid, "hi")
    assert _persona_ids(conn) == ["default"]


def test_persona_chip_fires_on_clarify_path(tmp_path):
    ws = tmp_path / "agents" / "fred"; ws.mkdir(parents=True)
    conn, agent, sid = _build_with_workspace(ws, router=_ScriptedRouter([_ambiguous()]))
    _prompt(agent, sid, "huh")
    assert _persona_ids(conn) == ["fred"]          # identity shows even on clarify


def test_persona_chip_not_written_to_session_record(tmp_path):
    ws = tmp_path / "agents" / "fred"; ws.mkdir(parents=True)
    conn, agent, sid = _build_with_workspace(ws)
    _prompt(agent, sid, "what is X")
    # The persona id reached the WIRE (conn) ...
    assert "fred" in _persona_ids(conn)
    # ... but the _meta chip is NOT in the session record (history/transcript).
    recorded = repr(agent._store.get(sid).transcript) + repr(agent._store.get(sid).history)
    assert "persona" not in recorded               # _meta is wire-only


# --------------------------------------------------------------------------
# persona-files-prompt: call site threads active session workspace
# --------------------------------------------------------------------------

def test_base_prompt_receives_active_persona_path(monkeypatch, tmp_path):
    """render_base_prompt must receive persona_id/persona_dir from the session's
    workspace_dir (state.workspace_dir), not the agent-level self._workspace_dir."""
    from harness import base_prompt

    captured = {}
    real = base_prompt.render_base_prompt
    def spy(**kw):
        captured.update(kw)
        return real(**kw)
    monkeypatch.setattr(base_prompt, "render_base_prompt", spy)

    ws = tmp_path / "agents" / "fred"
    ws.mkdir(parents=True)

    router = _ScriptedRouter([_chat()])
    agent = _build(router)
    # Use _store.new directly (same pattern as test_chat_turn_writes_user_and_assistant_with_chat_origin)
    # to avoid new_session's vibeproxy call; pass workspace_dir so state.workspace_dir == ws.
    sid = agent._store.new(cwd=".", workspace_dir=ws)
    _prompt(agent, sid, "what is X")

    assert captured.get("persona_id") == "fred"
    assert captured.get("persona_dir") == str(ws.resolve())   # call site passes an absolute path
