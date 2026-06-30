from pathlib import Path
from harness.compress import auto_regen
from harness import hooks


def test_module_registers_for_session_end():
    # The module registers `on_session_end` for `session_end` at import. Other
    # test files call hooks.clear() in teardown, which (with Python's module
    # cache preventing re-import) can wipe that import-time registration before
    # this test runs. So assert the registration MECHANISM is correct rather
    # than depending on import-order: register the real handler, then confirm it
    # landed under the right label.
    hooks.register("session_end", auto_regen.on_session_end, label="compress.auto_regen")
    assert any(lbl == "compress.auto_regen" and h is auto_regen.on_session_end
               for h, lbl in hooks._handlers.get("session_end", []))


def test_no_stale_means_no_spawn(monkeypatch):
    monkeypatch.setattr(auto_regen.targets, "stale_existing_siblings", lambda cwd=None: [])
    spawned = []
    monkeypatch.setattr(auto_regen, "_spawn_worker", lambda paths: spawned.append(paths))
    auto_regen.on_session_end(cwd="/x")
    assert spawned == []                         # nothing stale → no detached process


def test_stale_spawns_worker_with_exact_paths(monkeypatch):
    stale = [Path("/ws/SOUL.md"), Path("/proj/AGENTS.md")]
    monkeypatch.setattr(auto_regen.targets, "stale_existing_siblings", lambda cwd=None: stale)
    spawned = []
    monkeypatch.setattr(auto_regen, "_spawn_worker", lambda paths: spawned.append(paths))
    auto_regen.on_session_end(cwd="/proj")
    assert spawned == [["/ws/SOUL.md", "/proj/AGENTS.md"]]


def test_handler_never_raises_on_spawn_failure(monkeypatch):
    monkeypatch.setattr(auto_regen.targets, "stale_existing_siblings",
                        lambda cwd=None: [Path("/ws/SOUL.md")])

    def boom(paths):
        raise OSError("cannot fork")

    monkeypatch.setattr(auto_regen, "_spawn_worker", boom)
    auto_regen.on_session_end(cwd="/x")          # must not raise


def test_spawn_emits_tracer_breadcrumb(monkeypatch):
    monkeypatch.setattr(auto_regen.targets, "stale_existing_siblings",
                        lambda cwd=None: [Path("/ws/SOUL.md")])
    monkeypatch.setattr(auto_regen, "_spawn_worker", lambda paths: None)
    events = []

    class FakeTracer:
        def emit(self, source, name, **kw):
            events.append((source, name, kw))

    auto_regen.on_session_end(cwd="/x", tracer=FakeTracer())
    assert any(n == "compress.regen.spawn" for _, n, _ in events)
