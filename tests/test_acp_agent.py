import asyncio

import pytest

from harness.acp_agent import HarnessAgent
from harness import config


def _make_agent(backend="vibeproxy", workspace_dir=None, cwd=None):
    """A HarnessAgent with cheap stand-ins; only set_model behavior is exercised."""
    return HarnessAgent(
        model_factory=lambda *a, **k: None,
        agent_cfg={},
        skills_dir=[],
        router=object(),
        worker_model_id="gpt-5.4",
        yolo=False,
        backend=backend,
        workspace_dir=workspace_dir,
        cwd=cwd,
    )


@pytest.fixture
def agent(isolated_config):
    """A bare HarnessAgent (no persona workspace)."""
    return _make_agent(backend="mock")


@pytest.fixture
def agent_with_persona(isolated_config):
    """A HarnessAgent with an 'ana' persona workspace pre-seeded on disk."""
    from harness import paths, config as cfg
    ws = paths.config_dir() / "agents" / "ana"
    ws.mkdir(parents=True)
    cfg.save_agent("ana", cfg.AgentConfig(backend="mock", model=None))
    return _make_agent(backend="mock", cwd="/tmp")


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path


def test_explain_turn_gets_answer_only_instance_template():
    """A code_explain turn must run with the answer-only instance template, not
    the engine's 'solve this issue / edit the source' default — otherwise the
    every-turn framing overrides clarify-before-acting and the agent edits files
    when the user only asked it to look. Pure-helper guard (the seam in
    _run_agent_turn passes cls.task_type through to this)."""
    from harness.acp_agent import _instance_template_for, ANSWER_ONLY_INSTANCE

    default = "Please solve this issue: {{task}}\nEdit the source code to resolve it."
    assert _instance_template_for("code_explain", default) is ANSWER_ONLY_INSTANCE


def test_work_order_turn_keeps_engine_instance_template():
    """A real work order (code_fix/feature/refactor/ops) keeps the engine default
    template unchanged — the gate must not handicap turns the user DID ask to act
    on."""
    from harness.acp_agent import _instance_template_for

    default = "Please solve this issue: {{task}}"
    for tt in ("code_fix", "code_feature", "code_refactor", "ops_task"):
        assert _instance_template_for(tt, default) == default


def test_answer_only_template_keeps_task_placeholder_and_forbids_edits():
    """The answer-only template must still surface {{task}} (so the agent knows
    what was asked) and must explicitly forbid editing to answer."""
    from harness.acp_agent import ANSWER_ONLY_INSTANCE

    assert "{{task}}" in ANSWER_ONLY_INSTANCE
    low = ANSWER_ONLY_INSTANCE.lower()
    assert "do not" in low or "don't" in low
    assert "edit" in low


def test_set_model_persists_under_default_when_no_workspace():
    agent = _make_agent(backend="vibeproxy")          # workspace_dir=None -> "default"
    result = asyncio.run(agent.ext_method("harness/set_model", {"model": "claude-opus-4-8"}))
    assert result == {"ok": True, "model": "claude-opus-4-8"}
    assert config.load_default() == config.AgentConfig(
        backend="vibeproxy", model="claude-opus-4-8")


def test_set_model_persists_under_named_persona(tmp_path):
    ws = tmp_path / "agents" / "fred"
    ws.mkdir(parents=True)
    agent = _make_agent(backend="vibeproxy", workspace_dir=ws)
    asyncio.run(agent.ext_method("harness/set_model", {"model": "m-fred"}))
    assert config.load_agent("fred") == config.AgentConfig(backend="vibeproxy", model="m-fred")
    assert config.load_default() is None               # default table untouched


def test_set_model_empty_model_does_not_persist():
    agent = _make_agent()
    asyncio.run(agent.ext_method("harness/set_model", {"model": ""}))
    assert config.load_default() is None  # nothing written for a no-op swap


def test_set_model_reports_failure(monkeypatch, caplog):
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(config, "save_agent", boom)
    agent = _make_agent()
    with caplog.at_level("ERROR", logger="harness.acp_agent"):
        result = asyncio.run(agent.ext_method("harness/set_model", {"model": "x"}))
    # swap still applies in-session, but the response reports it did NOT persist
    assert result["model"] == "x"
    assert result["ok"] is False
    # the REASON must be logged, not just signalled via ok=False (silent persist
    # failure = the pin won't stick next launch, with no diagnostic)
    assert any("persist model pin" in r.message for r in caplog.records), \
        f"persist failure must be logged; got {[r.message for r in caplog.records]}"


def test_router_failure_logs_and_traces(caplog):
    """When the router raises (VibeProxy unreachable), prompt() must: return a
    refusal, log the exception (always), and emit a router.failed trace event
    (under --debug) — not just flash a user message that scrolls away."""
    import acp

    class _BoomRouter:
        def classify(self, *a, **k):
            raise RuntimeError("vibeproxy down")

    class _FakeConn:
        def __init__(self):
            self.updates = []
        async def session_update(self, session_id, update, **kw):
            self.updates.append(update)

    agent = HarnessAgent(
        model_factory=lambda *a, **k: None, agent_cfg={}, skills_dir=[],
        router=_BoomRouter(), worker_model_id="m", backend="vibeproxy", debug=True)
    conn = _FakeConn()
    agent.on_connect(conn)
    sid = agent._store.new(cwd=".", workspace_dir=None)

    async def go():
        prompt = [acp.text_block("do a thing")]
        return await agent.prompt(prompt, sid)

    with caplog.at_level("ERROR", logger="harness.acp_agent"):
        resp = asyncio.run(go())

    assert resp.stop_reason == "refusal"
    assert any("router classify failed" in r.message for r in caplog.records), \
        f"router failure must be logged; got {[r.message for r in caplog.records]}"
    # the router.failed trace event was relayed (debug=True)
    traces = [getattr(u, "field_meta", None) for u in conn.updates]
    assert any(isinstance(m, dict)
               and (m.get("harness") or {}).get("trace", {}).get("type") == "router.failed"
               for m in traces), f"router.failed trace missing; got {traces}"


def test_engine_baseexception_yields_refusal_not_disconnect(monkeypatch, caplog):
    """A BaseException from the engine (asyncio.CancelledError / SystemExit /
    KeyboardInterrupt) must be caught and turned into a clean refusal — NOT escape
    the prompt handler. If it escapes, the ACP request task dies, the agent process
    exits, and the TUI shows 'agent disconnected (Connection closed)'. run_engine
    catching only `except Exception` (BaseException re-raised by tracing_agent) was
    the root cause of that intermittent disconnect."""
    import acp
    from harness import acp_agent as M
    from harness.router import Classification

    class _WorkOrderRouter:
        catalog = []
        def classify(self, *a, **k):
            # code_explain reaches the agent path (run_engine), high confidence so
            # it does NOT branch to clarify.
            return Classification(task_type="code_explain", skills=[], confidence=0.94)

    class _FakeConn:
        def __init__(self):
            self.updates = []
        async def session_update(self, session_id, update, **kw):
            self.updates.append(update)

    class _ExplodingAgent:
        """Stands in for TracingAgent: construction succeeds, run() raises a
        BaseException-only exception (the class that escapes `except Exception`)."""
        messages = []
        n_calls = 0
        def __init__(self, *a, **k):
            self.model = type("Mdl", (), {})()      # no on_delta attr -> mock-like
        def run(self, text, prior=None):
            raise asyncio.CancelledError("cancelled mid-turn")

    monkeypatch.setattr("harness.tracing_agent.TracingAgent", _ExplodingAgent)

    agent = HarnessAgent(
        model_factory=lambda *a, **k: type("Mdl", (), {"registry": None})(),
        agent_cfg={}, skills_dir=[], router=_WorkOrderRouter(),
        worker_model_id="m", backend="vibeproxy", yolo=True)
    conn = _FakeConn()
    agent.on_connect(conn)
    asyncio.run(agent.initialize(protocol_version=acp.PROTOCOL_VERSION))  # sets _client_caps
    sid = agent._store.new(cwd=".", workspace_dir=None)

    async def go():
        return await agent.prompt([acp.text_block("explain the router flow")], sid)

    with caplog.at_level("ERROR", logger="harness.acp_agent"):
        resp = asyncio.run(go())          # must NOT raise

    assert resp.stop_reason == "refusal"
    assert any("agent engine failed" in r.message for r in caplog.records), \
        f"engine failure must be logged; got {[r.message for r in caplog.records]}"


def test_set_yolo_active_true_sets_gate_no_persist():
    agent = _make_agent()
    agent._yolo = False
    result = asyncio.run(agent.ext_method("harness/set_yolo", {"active": True}))
    assert agent._yolo is True
    assert result["ok"] is True and result["active"] is True
    assert config.load_default() is None      # active alone never persists


def test_set_yolo_active_false_turns_gate_off():
    agent = _make_agent()
    agent._yolo = True
    asyncio.run(agent.ext_method("harness/set_yolo", {"active": False}))
    assert agent._yolo is False


def test_set_yolo_pin_true_persists():
    agent = _make_agent()
    asyncio.run(agent.ext_method("harness/set_yolo", {"active": True, "pin": True}))
    assert config.yolo_pinned() is True


def test_set_yolo_pin_false_unpins():
    config.update_default(backend="vibeproxy", model="gpt-5.4", yolo_pinned=True)
    agent = _make_agent()
    asyncio.run(agent.ext_method("harness/set_yolo", {"pin": False}))
    assert config.yolo_pinned() is False


def test_set_yolo_omitted_pin_does_not_touch_persistence():
    config.update_default(backend="vibeproxy", model="gpt-5.4", yolo_pinned=True)
    agent = _make_agent()
    asyncio.run(agent.ext_method("harness/set_yolo", {"active": False}))
    assert config.yolo_pinned() is True       # pin untouched by a live-only toggle


def test_set_yolo_survives_persist_failure(monkeypatch):
    def boom(*a, **kw):
        raise OSError("disk full")
    monkeypatch.setattr(config, "update_agent", boom)
    agent = _make_agent()
    result = asyncio.run(agent.ext_method("harness/set_yolo", {"active": True, "pin": True}))
    # live toggle still succeeds, but ok=False surfaces the failed persist so the
    # TUI can reconcile rather than show a false "pinned".
    assert agent._yolo is True
    assert result["ok"] is False


def test_set_yolo_pin_pairs_backend_and_model_on_fresh_config():
    # Pinning on a fresh config writes a COMPLETE default (the agent supplies its
    # known backend+model), never backend=""/model="" (which would break launch).
    agent = _make_agent(backend="vibeproxy")   # worker_model_id="gpt-5.4"
    asyncio.run(agent.ext_method("harness/set_yolo", {"active": True, "pin": True}))
    assert config.load_default() == config.AgentConfig(
        backend="vibeproxy", model="gpt-5.4", yolo_pinned=True)


def test_set_yolo_non_bool_active_is_ignored():
    # ACP params are untyped; "false" is truthy. A non-bool must NOT flip the gate.
    agent = _make_agent()
    agent._yolo = False
    asyncio.run(agent.ext_method("harness/set_yolo", {"active": "false"}))
    assert agent._yolo is False                # not coerced on


def test_set_yolo_non_bool_pin_is_ignored():
    agent = _make_agent()
    asyncio.run(agent.ext_method("harness/set_yolo", {"pin": "false"}))
    assert config.yolo_pinned() is False       # "false" did not persist True


def test_acp_main_wires_default_workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("HARNESS_ROUTER_STUB", "1")
    from harness import acp_agent, paths

    captured = {}
    real_init = acp_agent.HarnessAgent.__init__
    def spy_init(self, **kw):
        captured.update(kw)
        real_init(self, **kw)
    monkeypatch.setattr(acp_agent.HarnessAgent, "__init__", spy_init)

    # run _main far enough to construct the agent, then stop at run_agent
    import acp
    monkeypatch.setattr(acp, "run_agent", lambda agent: asyncio.sleep(0))
    from harness import acp_main
    asyncio.run(acp_main._main(["--model", "mock"]))
    assert captured["workspace_dir"] == paths.default_workspace_dir()


def test_acp_main_seeds_default_workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("HARNESS_ROUTER_STUB", "1")
    import asyncio
    import acp
    from harness import persona

    called = {"n": 0}
    real = persona.seed_default_workspace
    def spy():
        called["n"] += 1
        real()
    monkeypatch.setattr(persona, "seed_default_workspace", spy)
    monkeypatch.setattr(acp, "run_agent", lambda agent: asyncio.sleep(0))

    from harness import acp_main
    asyncio.run(acp_main._main(["--model", "mock"]))
    assert called["n"] == 1
    # and it actually seeded
    assert (tmp_path / "harness" / "agents" / "default" / "SOUL.md").is_file()


# --- set_persona tests (Task 3) ---

def test_set_persona_unknown_keeps_active(agent, caplog):
    before = agent._active_persona
    with caplog.at_level("WARNING", logger="harness.acp_agent"):
        resp = asyncio.run(agent.ext_method("harness/set_persona", {"id": "nope-does-not-exist"}))
    assert resp["ok"] is False
    assert agent._active_persona == before          # unchanged on failure
    # the rejection must be logged (the error dict reaches the TUI, but a durable
    # log is the only post-hoc diagnosis of a failed persona switch)
    assert any("set_persona rejected" in r.message for r in caplog.records), \
        f"rejected persona switch must be logged; got {[r.message for r in caplog.records]}"


def test_set_persona_invalid_charset(agent):
    resp = asyncio.run(agent.ext_method("harness/set_persona", {"id": "bad.id"}))
    assert resp["ok"] is False


def test_set_persona_valid_switches_and_returns_seat(agent_with_persona):
    resp = asyncio.run(agent_with_persona.ext_method("harness/set_persona", {"id": "ana"}))
    assert resp["ok"] is True and resp["id"] == "ana"
    assert resp["session_id"]
    assert agent_with_persona._active_persona == "ana"


def test_set_model_persists_under_active_persona(agent_with_persona):
    asyncio.run(agent_with_persona.ext_method("harness/set_persona", {"id": "ana"}))
    asyncio.run(agent_with_persona.ext_method("harness/set_model", {"model": "m-ana-new"}))
    assert config.load_agent("ana").model == "m-ana-new"


def test_new_session_registers_launch_seat(isolated_config):
    """new_session must register the launch persona's seat so switch-back resumes
    the SAME session instead of minting a fresh one (Defect B fix)."""
    agent = _make_agent(backend="mock", cwd="/x")
    agent._cwd = "/x"
    resp = asyncio.run(agent.new_session(cwd="/x"))
    launch_session_id = resp.session_id

    # Switch back to "default" — must resume the original session, not re-mint.
    back = asyncio.run(agent.ext_method("harness/set_persona", {"id": "default"}))
    assert back["ok"] is True
    assert back["session_id"] == launch_session_id


def test_prompt_uses_session_model_not_global(isolated_config, tmp_path):
    """The worker model must be session-bound (Defect A fix): switching to 'ana'
    must NOT mutate the default session's worker_model; each seat carries its own."""
    from harness import paths, config as cfg
    # Seed the 'ana' persona dir + done.conf with model "m-ana"
    ws = paths.config_dir() / "agents" / "ana"
    ws.mkdir(parents=True)
    cfg.save_agent("ana", cfg.AgentConfig(backend="vibeproxy", model="m-ana"))

    agent = _make_agent(backend="vibeproxy", cwd="/x")
    agent._cwd = "/x"
    default_resp = asyncio.run(agent.new_session(cwd="/x"))
    default_sid = default_resp.session_id
    default_model = agent._store.get(default_sid).worker_model

    # Switch to ana; get ana's session_id
    ana_resp = asyncio.run(agent.ext_method("harness/set_persona", {"id": "ana"}))
    assert ana_resp["ok"] is True
    ana_sid = ana_resp["session_id"]

    # Ana's session must have "m-ana" as its worker_model
    assert agent._store.get(ana_sid).worker_model == "m-ana"
    # Default session must be UNCHANGED — not overwritten with ana's model
    assert agent._store.get(default_sid).worker_model == default_model


def test_set_model_updates_active_session_state(isolated_config):
    """`/models` swap (set_model) must reach the active session's state.worker_model
    so that the very next prompt() picks up the new model, not the stale one."""
    agent = _make_agent(backend="mock")
    agent._cwd = "/x"
    # new_session registers the default seat and stamps state.worker_model
    resp = asyncio.run(agent.new_session(cwd="/x"))
    sid = resp.session_id

    # Hot-swap via the harness/set_model extension method
    asyncio.run(agent.ext_method("harness/set_model", {"model": "m-new"}))

    # The active session's state must reflect the new model so the next prompt uses it
    assert agent._store.get(sid).worker_model == "m-new"


# --- create_persona tests (Task 2) ---

@pytest.fixture
def agent_default(isolated_config):
    """A bare HarnessAgent with _active_persona='default' (no workspace)."""
    agent = _make_agent(backend="mock")
    agent._cwd = "/x"
    return agent


def test_create_persona_creates_and_activates(agent_default, isolated_config):
    resp = asyncio.run(agent_default.ext_method("harness/create_persona", {"id": "fred"}))
    assert resp["ok"] is True and resp["id"] == "fred"
    assert resp["session_id"]
    assert agent_default._active_persona == "fred"
    from harness import paths
    assert (paths.config_dir() / "agents" / "fred").is_dir()


def test_create_persona_duplicate_keeps_active(agent_default, isolated_config):
    asyncio.run(agent_default.ext_method("harness/create_persona", {"id": "fred"}))
    before = agent_default._active_persona   # "fred" (activated by first create)
    resp = asyncio.run(agent_default.ext_method("harness/create_persona", {"id": "fred"}))
    assert resp["ok"] is False
    assert agent_default._active_persona == before


def test_create_persona_invalid_keeps_active(agent_default, isolated_config):
    before = agent_default._active_persona   # "default"
    resp = asyncio.run(agent_default.ext_method("harness/create_persona", {"id": "default"}))
    assert resp["ok"] is False
    assert agent_default._active_persona == before


def test_create_persona_missing_id(agent_default, isolated_config):
    resp = asyncio.run(agent_default.ext_method("harness/create_persona", {}))
    assert resp["ok"] is False


def test_create_persona_forwards_display_name(agent_default, isolated_config):
    resp = asyncio.run(agent_default.ext_method(
        "harness/create_persona", {"id": "my-persona", "display_name": "My Persona"}))
    assert resp["ok"] is True
    from harness import paths, persona_config
    ws = paths.config_dir() / "agents" / "my-persona"
    assert persona_config.read_name(ws) == "My Persona"


def test_create_persona_without_display_name_still_works(agent_default, isolated_config):
    resp = asyncio.run(agent_default.ext_method(
        "harness/create_persona", {"id": "plain"}))
    assert resp["ok"] is True
