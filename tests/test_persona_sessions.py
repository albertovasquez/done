from harness import persona_sessions as ps


def test_resolve_session_model_mock_is_none():
    assert ps.resolve_session_model(
        "default", shell_set_model=False, shell_env=None, dotenv=None, backend="mock"
    ) is None


def test_resolve_session_model_persisted_wins_over_dotenv(monkeypatch):
    # done.conf[ana].model present; a .env value must NOT beat it.
    monkeypatch.setattr(ps.config, "load_agent",
                        lambda pid: ps.config.AgentConfig(backend="vibeproxy", model="m-ana")
                        if pid == "ana" else None)
    got = ps.resolve_session_model(
        "ana", shell_set_model=False, shell_env="m-dotenv", dotenv="m-dotenv",
        backend="vibeproxy")
    assert got == "m-ana"


def test_resolve_session_model_real_shell_env_forces_for_all(monkeypatch):
    monkeypatch.setattr(ps.config, "load_agent",
                        lambda pid: ps.config.AgentConfig(backend="vibeproxy", model="m-ana"))
    got = ps.resolve_session_model(
        "ana", shell_set_model=True, shell_env="m-shell", dotenv=None, backend="vibeproxy")
    assert got == "m-shell"


def test_resolve_session_model_engine_default_when_nothing(monkeypatch):
    monkeypatch.setattr(ps.config, "load_agent", lambda pid: None)
    got = ps.resolve_session_model(
        "bob", shell_set_model=False, shell_env=None, dotenv=None, backend="vibeproxy")
    assert got == ps.vibeproxy.DEFAULT_MODEL


class _FakeStore:
    def __init__(self): self.n = 0
    def new(self, *, cwd, workspace_dir):
        self.n += 1
        return f"sess-{self.n}"


def test_get_or_create_mints_then_resumes_same_seat():
    pses = ps.PersonaSessions()
    store = _FakeStore()
    rw = lambda pid: f"/ws/{pid}"
    rm = lambda pid: f"m-{pid}"
    a1 = pses.get_or_create("ana", cwd="/c", store=store, resolve_ws=rw, resolve_model=rm)
    a2 = pses.get_or_create("ana", cwd="/c", store=store, resolve_ws=rw, resolve_model=rm)
    assert a1 == a2                         # same session AND model on resume
    assert store.n == 1                     # not re-minted
    assert a1.session_id == "sess-1" and a1.model == "m-ana"


def test_distinct_personas_distinct_seats():
    pses = ps.PersonaSessions()
    store = _FakeStore()
    rw = lambda pid: f"/ws/{pid}"
    rm = lambda pid: f"m-{pid}"
    a = pses.get_or_create("ana", cwd="/c", store=store, resolve_ws=rw, resolve_model=rm)
    b = pses.get_or_create("bob", cwd="/c", store=store, resolve_ws=rw, resolve_model=rm)
    assert a.session_id != b.session_id
    assert a.model == "m-ana" and b.model == "m-bob"


def test_set_model_is_per_seat():
    pses = ps.PersonaSessions()
    store = _FakeStore()
    rw = lambda pid: f"/ws/{pid}"
    rm = lambda pid: f"m-{pid}"
    pses.get_or_create("ana", cwd="/c", store=store, resolve_ws=rw, resolve_model=rm)
    pses.get_or_create("bob", cwd="/c", store=store, resolve_ws=rw, resolve_model=rm)
    pses.set_model("ana", "m-ana-2")
    assert pses.model_of("ana") == "m-ana-2"
    assert pses.model_of("bob") == "m-bob"          # untouched


def test_split_brain_launch_persona_model_does_not_win_for_other_seat(monkeypatch):
    # default has m-default, ana has m-ana, NO real shell VIBEPROXY_MODEL.
    models = {"default": "m-default", "ana": "m-ana"}
    monkeypatch.setattr(ps.config, "load_agent",
                        lambda pid: ps.config.AgentConfig(backend="vibeproxy", model=models[pid])
                        if pid in models else None)
    # ana must resolve its OWN model even though default is the launch persona.
    got = ps.resolve_session_model(
        "ana", shell_set_model=False, shell_env=None, dotenv=None, backend="vibeproxy")
    assert got == "m-ana"
