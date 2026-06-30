"""The TUI fires session_start on mount and session_end on unmount.

These are unit tests on the dispatch wiring, not full Pilot runs: we call a thin
seam. Implementer: if app.py exposes on_mount/on_unmount as coroutines that need
a mounted app, instead assert the dispatch via monkeypatching hooks.dispatch and
driving the smallest path. The REQUIRED behavior to lock:
  - on_unmount dispatches 'session_end' with cwd + persona_id, BEFORE tracer.close().
"""
from harness import hooks


def test_session_end_dispatched_before_tracer_close(monkeypatch):
    from harness.tui import app as app_mod

    order = []
    monkeypatch.setattr(hooks, "dispatch",
                        lambda event, **kw: order.append(("dispatch", event)))

    class FakeTracer:
        def close(self):
            order.append(("tracer", "close"))
        def emit(self, *a, **k):
            pass

    # Build a minimal object exposing just what on_unmount touches.
    class Stub:
        _pending_perm = None
        _cm = None
        _tracer = FakeTracer()
        cwd = "/proj"
        def _current_persona(self):
            return "default"
        def log(self, *a, **k):
            pass

    stub = Stub()
    import asyncio
    asyncio.run(app_mod.HarnessTui.on_unmount(stub))     # call unbound with stub

    assert ("dispatch", "session_end") in order
    assert order.index(("dispatch", "session_end")) < order.index(("tracer", "close"))
