from harness import hooks


def teardown_function():
    hooks.clear()


def test_dispatch_calls_handlers_in_registration_order():
    calls = []
    hooks.register("session_end", lambda **kw: calls.append("a"), label="a")
    hooks.register("session_end", lambda **kw: calls.append("b"), label="b")
    hooks.dispatch("session_end")
    assert calls == ["a", "b"]


def test_dispatch_passes_payload_to_handlers():
    seen = {}
    hooks.register("session_start", lambda **kw: seen.update(kw))
    hooks.dispatch("session_start", cwd="/x", persona_id="default")
    assert seen == {"cwd": "/x", "persona_id": "default"}


def test_unknown_event_is_noop():
    hooks.dispatch("nonexistent")   # must not raise


def test_raising_handler_is_isolated_and_others_still_run():
    calls = []
    hooks.register("session_end", lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")), label="bad")
    hooks.register("session_end", lambda **kw: calls.append("good"), label="good")
    hooks.dispatch("session_end")          # must not raise
    assert calls == ["good"]               # later handler still ran


def test_handler_error_is_logged_via_tracer():
    events = []

    class FakeTracer:
        def emit(self, source, name, **kw):
            events.append((source, name, kw))

    hooks.register("session_end", lambda **kw: (_ for _ in ()).throw(ValueError("x")), label="bad")
    hooks.dispatch("session_end", tracer=FakeTracer())
    assert events and events[0][0] == "dn" and events[0][1] == "hook.error"
    assert events[0][2]["event"] == "session_end"
    assert events[0][2]["label"] == "bad"
    assert "x" in events[0][2]["error"]


def test_dispatch_forwards_tracer_to_handler():
    """Finding 3: tracer passed to dispatch must be forwarded to the handler."""
    received = {}

    def handler(tracer=None, **kw):
        received["tracer"] = tracer

    hooks.register("session_end", handler)

    class FakeTracer:
        pass

    fake = FakeTracer()
    hooks.dispatch("session_end", tracer=fake)
    assert received.get("tracer") is fake, "handler should receive the tracer passed to dispatch"


def test_dispatch_no_tracer_does_not_inject_tracer_kwarg():
    """When dispatch is called without tracer, handler must NOT receive a tracer kwarg."""
    received = {"tracer": "UNSET"}

    def handler(**kw):
        received["tracer"] = kw.get("tracer", "UNSET")

    hooks.register("session_end", handler)
    hooks.dispatch("session_end")
    assert received["tracer"] == "UNSET", "no tracer kwarg should be injected when tracer is None"


def test_on_decorator_registers_and_returns_handler():
    calls = []

    @hooks.on("session_start", label="deco")
    def handler(**kw):
        calls.append(1)

    hooks.dispatch("session_start")
    assert calls == [1]
    assert callable(handler)               # decorator returns the function


def test_clear_one_event_then_all():
    hooks.register("a", lambda **kw: None)
    hooks.register("b", lambda **kw: None)
    hooks.clear("a")
    hooks.dispatch("a")                     # no handlers, no raise
    hooks.clear()                           # clears everything
