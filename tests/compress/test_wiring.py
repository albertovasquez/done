"""Integration tests: compress-aware loader wired into persona/agents/memory reads."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from harness import persona, agents, memory
from harness.compress import sibling


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TODAY = "2026-06-29"


# ---------------------------------------------------------------------------
# persona — compose_persona
# ---------------------------------------------------------------------------


def test_compose_persona_uses_fresh_sibling_when_on(tmp_path, monkeypatch):
    ws = tmp_path
    (ws / "SOUL.md").write_text("I am a verbose detailed soul with much prose.")
    sibling.write_sibling(ws / "SOUL.md", "terse soul", today=TODAY)
    monkeypatch.setattr(persona, "_compress_on", lambda *_a, **_k: True, raising=False)
    load = persona.compose_persona(ws)
    assert "terse soul" in load.block


def test_compose_persona_uses_original_when_off(tmp_path, monkeypatch):
    ws = tmp_path
    (ws / "SOUL.md").write_text("verbose soul")
    sibling.write_sibling(ws / "SOUL.md", "terse soul", today=TODAY)
    monkeypatch.setattr(persona, "_compress_on", lambda *_a, **_k: False, raising=False)
    load = persona.compose_persona(ws)
    assert "verbose soul" in load.block


# ---------------------------------------------------------------------------
# agents — _read_tier
# ---------------------------------------------------------------------------


def test_read_tier_uses_fresh_sibling_when_on(tmp_path, monkeypatch):
    tier_dir = tmp_path
    (tier_dir / "AGENTS.md").write_text("verbose global standing instructions here.")
    sibling.write_sibling(tier_dir / "AGENTS.md", "terse agents", today=TODAY)
    monkeypatch.setattr(agents, "_compress_on_dir", lambda *_a, **_k: True, raising=False)
    load = agents.AgentsLoad()
    result = agents._read_tier(tier_dir, "Global", load)
    assert result is not None
    assert "terse agents" in result


def test_read_tier_uses_original_when_off(tmp_path, monkeypatch):
    tier_dir = tmp_path
    (tier_dir / "AGENTS.md").write_text("verbose global standing instructions here.")
    sibling.write_sibling(tier_dir / "AGENTS.md", "terse agents", today=TODAY)
    monkeypatch.setattr(agents, "_compress_on_dir", lambda *_a, **_k: False, raising=False)
    load = agents.AgentsLoad()
    result = agents._read_tier(tier_dir, "Global", load)
    assert result is not None
    assert "verbose global" in result


# ---------------------------------------------------------------------------
# memory — MEMORY.md read only
# ---------------------------------------------------------------------------


def test_resolve_memory_uses_fresh_sibling_when_on(tmp_path, monkeypatch):
    ws = tmp_path
    (ws / "MEMORY.md").write_text("verbose memory with lots of detail and history.")
    sibling.write_sibling(ws / "MEMORY.md", "terse memory", today=TODAY)
    monkeypatch.setattr(memory, "_compress_on", lambda *_a, **_k: True, raising=False)
    load = memory.resolve_memory(ws, today=date(2026, 6, 29))
    assert "terse memory" in load.block


def test_resolve_memory_uses_original_when_off(tmp_path, monkeypatch):
    ws = tmp_path
    (ws / "MEMORY.md").write_text("verbose memory with lots of detail and history.")
    sibling.write_sibling(ws / "MEMORY.md", "terse memory", today=TODAY)
    monkeypatch.setattr(memory, "_compress_on", lambda *_a, **_k: False, raising=False)
    load = memory.resolve_memory(ws, today=date(2026, 6, 29))
    assert "verbose memory" in load.block


# ---------------------------------------------------------------------------
# Degradation: mode_on=True but NO sibling present -> returns ORIGINAL
# ---------------------------------------------------------------------------


def test_compose_persona_degrades_to_original_when_no_sibling(tmp_path, monkeypatch):
    ws = tmp_path
    (ws / "SOUL.md").write_text("original soul content without sibling")
    # no sibling written — degradation path
    monkeypatch.setattr(persona, "_compress_on", lambda *_a, **_k: True, raising=False)
    load = persona.compose_persona(ws)
    assert "original soul content without sibling" in load.block


def test_read_tier_degrades_to_original_when_no_sibling(tmp_path, monkeypatch):
    tier_dir = tmp_path
    (tier_dir / "AGENTS.md").write_text("original agents content without sibling")
    # no sibling written — degradation path
    monkeypatch.setattr(agents, "_compress_on_dir", lambda *_a, **_k: True, raising=False)
    load = agents.AgentsLoad()
    result = agents._read_tier(tier_dir, "Global", load)
    assert result is not None
    assert "original agents content without sibling" in result


def test_resolve_memory_degrades_to_original_when_no_sibling(tmp_path, monkeypatch):
    ws = tmp_path
    (ws / "MEMORY.md").write_text("original memory content without sibling")
    # no sibling written — degradation path
    monkeypatch.setattr(memory, "_compress_on", lambda *_a, **_k: True, raising=False)
    load = memory.resolve_memory(ws, today=date(2026, 6, 29))
    assert "original memory content without sibling" in load.block
