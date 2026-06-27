"""RelayEmitter forwards every TracingAgent event to a relay callback in the
{type, data} shape, while remaining a drop-in Emitter."""
from harness.relay_emitter import RelayEmitter


def test_relay_emitter_forwards_events():
    seen = []
    em = RelayEmitter("/dev/null", clock=lambda: 0.0, relay=lambda ev: seen.append(ev))
    em.emit("llm.call", n=1, n_messages=4)
    em.emit("action", command="pytest -q")
    assert [s["type"] for s in seen] == ["llm.call", "action"]
    assert seen[0]["data"] == {"n": 1, "n_messages": 4}
    assert seen[1]["data"] == {"command": "pytest -q"}


def test_relay_emitter_is_an_emitter():
    from harness.events import Emitter
    em = RelayEmitter("/dev/null", clock=lambda: 0.0, relay=lambda ev: None)
    assert isinstance(em, Emitter)


def test_relay_failure_never_aborts_emit():
    def boom(ev):
        raise RuntimeError("sink down")
    em = RelayEmitter("/dev/null", clock=lambda: 0.0, relay=boom)
    em.emit("llm.call", n=1)   # must not raise — observation can't abort the observed
