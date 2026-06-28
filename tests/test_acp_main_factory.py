from harness.acp_main import _model_factory
from harness.streaming_model import StreamingLitellmModel


def test_vibeproxy_factory_builds_streaming_model(monkeypatch):
    monkeypatch.setenv("VIBEPROXY_MODEL", "gpt-5.4")
    make = _model_factory("vibeproxy")
    model = make("claude-opus-4-8")
    assert isinstance(model, StreamingLitellmModel)
    assert model.on_delta is None                 # unbound until a turn sets it
    assert model.config.model_name == "openai/claude-opus-4-8"


def test_mock_factory_unchanged():
    make = _model_factory("mock")
    model = make()
    assert not isinstance(model, StreamingLitellmModel)
