"""Tests for the output_filter toggle decision in acp_agent.

Tests the TOGGLE DECISION (_resolve_output_filter helper), not the config API itself.
Monkeypatches harness.acp_agent.config (the `config` module imported as
`from harness import config` at the top of acp_agent.py) so harness_setting
returns controlled values without touching the real done.conf.
"""
import harness.acp_agent as acp_agent_mod
from harness.output_filters.dispatch import filter_output


class _MockConfig:
    """Minimal config stand-in: harness_setting returns a fixed value."""

    def __init__(self, return_value):
        self._value = return_value

    def harness_setting(self, key: str):
        return self._value


def test_resolve_output_filter_off_when_false(monkeypatch):
    """[harness] output_filter = "false"  → _resolve_output_filter() returns None."""
    monkeypatch.setattr(acp_agent_mod, "config", _MockConfig("false"))
    result = acp_agent_mod._resolve_output_filter()
    assert result is None


def test_resolve_output_filter_on_when_absent(monkeypatch):
    """output_filter key absent (None)  → _resolve_output_filter() returns the dispatcher."""
    monkeypatch.setattr(acp_agent_mod, "config", _MockConfig(None))
    result = acp_agent_mod._resolve_output_filter()
    assert result is filter_output


def test_resolve_output_filter_on_when_true(monkeypatch):
    """[harness] output_filter = "true"  → _resolve_output_filter() returns the dispatcher."""
    monkeypatch.setattr(acp_agent_mod, "config", _MockConfig("true"))
    result = acp_agent_mod._resolve_output_filter()
    assert result is filter_output


def test_resolve_output_filter_on_when_other_value(monkeypatch):
    """Any value other than "false" → _resolve_output_filter() returns the dispatcher (default-on)."""
    monkeypatch.setattr(acp_agent_mod, "config", _MockConfig("yes"))
    result = acp_agent_mod._resolve_output_filter()
    assert result is filter_output
