from pathlib import Path
from harness.compress import auto_regen
from harness import hooks


def test_module_registers_for_session_end():
    # importing the module registered the handler
    assert any(lbl == "compress.auto_regen"
               for _, lbl in hooks._handlers.get("session_end", []))


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
