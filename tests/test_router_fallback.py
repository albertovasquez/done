"""The router classifies every turn with a cheap model. If that model is
rate-limited / cooling down (e.g. a personal ChatGPT account hits its limit), it
must FALL BACK to another cheap model rather than refusing the whole turn."""
import sys
import types

import pytest

from harness import router


def _fake_litellm(behaviors):
    """A fake `litellm` module whose completion() consumes `behaviors` — a list of
    (model_expected_substr_or_None, action) where action is either an Exception to
    raise or a string to return as the message content."""
    calls = []

    def completion(*, model, messages, max_tokens, **kw):
        calls.append(model)
        action = behaviors[len(calls) - 1][1]
        if isinstance(action, Exception):
            raise action
        msg = types.SimpleNamespace(content=action)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])

    mod = types.ModuleType("litellm")
    mod.completion = completion
    return mod, calls


class _RateLimit(Exception):
    pass
_RateLimit.__name__ = "RateLimitError"


@pytest.fixture
def _stub_vibeproxy(monkeypatch):
    # complete() does `from harness import vibeproxy`; give it harmless kwargs.
    import harness.vibeproxy as vp
    monkeypatch.setattr(vp, "completion_kwargs", lambda: {"api_base": "x", "api_key": "y"})


def test_falls_back_to_second_model_on_rate_limit(monkeypatch, _stub_vibeproxy):
    fake, calls = _fake_litellm([
        (None, _RateLimit("All credentials for model gpt-5.4-mini are cooling down via provider codex")),
        (None, "chat_question"),
    ])
    monkeypatch.setitem(sys.modules, "litellm", fake)
    out = router.complete("sys", "user")
    assert out == "chat_question"
    assert len(calls) == 2                       # tried primary, then fallback
    assert calls[0] != calls[1]                  # different models


def test_primary_used_when_healthy(monkeypatch, _stub_vibeproxy):
    fake, calls = _fake_litellm([(None, "code_fix")])
    monkeypatch.setitem(sys.modules, "litellm", fake)
    out = router.complete("sys", "user")
    assert out == "code_fix"
    assert len(calls) == 1                        # no fallback needed


def test_non_rate_limit_error_propagates(monkeypatch, _stub_vibeproxy):
    fake, calls = _fake_litellm([(None, ValueError("malformed request body"))])
    monkeypatch.setitem(sys.modules, "litellm", fake)
    with pytest.raises(ValueError):
        router.complete("sys", "user")
    assert len(calls) == 1                        # did NOT try the fallback


def test_rate_limit_detection():
    assert router._is_rate_limit(_RateLimit("x"))
    assert router._is_rate_limit(Exception("All credentials are cooling down"))
    assert router._is_rate_limit(Exception("model_cooldown"))
    assert router._is_rate_limit(Exception("HTTP 429")) or router._is_rate_limit(Exception("status 429"))
    assert not router._is_rate_limit(ValueError("totally unrelated error"))


def test_env_overrides_router_models(monkeypatch):
    monkeypatch.setenv("ROUTER_MODEL", "openai/my-primary")
    monkeypatch.setenv("ROUTER_FALLBACK_MODEL", "openai/my-fallback")
    assert router._router_models() == ["openai/my-primary", "openai/my-fallback"]


def test_router_models_dedupes(monkeypatch):
    monkeypatch.setenv("ROUTER_MODEL", "openai/same")
    monkeypatch.setenv("ROUTER_FALLBACK_MODEL", "openai/same")
    assert router._router_models() == ["openai/same"]
