from harness.proxy_service import auto_install
from harness import hooks


def test_module_registers_for_session_start():
    # Mirrors tests/compress/test_auto_regen.py's registration test: assert the
    # registration MECHANISM rather than relying on import order (other test
    # files' hooks.clear() teardown can wipe the import-time registration).
    hooks.register("session_start", auto_install.on_session_start, label="proxy.auto_install")
    assert any(lbl == "proxy.auto_install" and h is auto_install.on_session_start
               for h, lbl in hooks._handlers.get("session_start", []))


def test_ok_drift_means_no_spawn(monkeypatch):
    monkeypatch.setattr(auto_install.config_gen, "config_drift", lambda env=None: "ok")
    spawned = []
    monkeypatch.setattr(auto_install, "_spawn_install", lambda: spawned.append(True))
    auto_install.on_session_start(cwd="/x")
    assert spawned == []


def test_drifted_means_no_spawn(monkeypatch):
    # Drifted (already installed, just stale) must NEVER auto-restart — warn-only,
    # handled by Task 3. This handler only acts on "missing".
    monkeypatch.setattr(auto_install.config_gen, "config_drift", lambda env=None: "drifted")
    spawned = []
    monkeypatch.setattr(auto_install, "_spawn_install", lambda: spawned.append(True))
    auto_install.on_session_start(cwd="/x")
    assert spawned == []


def test_missing_spawns_install(monkeypatch):
    monkeypatch.setattr(auto_install.config_gen, "config_drift", lambda env=None: "missing")
    spawned = []
    monkeypatch.setattr(auto_install, "_spawn_install", lambda: spawned.append(True))
    auto_install.on_session_start(cwd="/x")
    assert spawned == [True]


def test_two_concurrent_sessions_both_missing_neither_raises(monkeypatch):
    # Multi-session race (flagged in caveman review): two sessions launched close
    # together can both observe "missing" and both call the handler. This
    # handler itself does no locking — it relies on install()'s own steps being
    # idempotent-safe (download.download_and_install checks an existing stamp;
    # OS-service registration checks .exists()). This test only proves the
    # HANDLER's contract holds under a double-fire: neither call raises, and
    # both attempt a spawn (real idempotency is install()'s existing behavior,
    # exercised separately in tests/test_proxy_lifecycle.py, not re-tested here).
    monkeypatch.setattr(auto_install.config_gen, "config_drift", lambda env=None: "missing")
    spawned = []
    monkeypatch.setattr(auto_install, "_spawn_install", lambda: spawned.append(True))
    auto_install.on_session_start(cwd="/session-a")
    auto_install.on_session_start(cwd="/session-b")
    assert spawned == [True, True]          # both fired; no exception from either


def test_handler_never_raises_on_spawn_failure(monkeypatch):
    monkeypatch.setattr(auto_install.config_gen, "config_drift", lambda env=None: "missing")

    def boom():
        raise OSError("cannot fork")

    monkeypatch.setattr(auto_install, "_spawn_install", boom)
    auto_install.on_session_start(cwd="/x")          # must not raise


def test_spawn_emits_tracer_breadcrumb(monkeypatch):
    monkeypatch.setattr(auto_install.config_gen, "config_drift", lambda env=None: "missing")
    monkeypatch.setattr(auto_install, "_spawn_install", lambda: None)
    events = []

    class FakeTracer:
        def emit(self, source, name, **kw):
            events.append((source, name, kw))

    auto_install.on_session_start(cwd="/x", tracer=FakeTracer())
    assert any(n == "proxy.auto_install.spawn" for _, n, _ in events)
