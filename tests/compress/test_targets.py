from pathlib import Path
import pytest
from harness.compress import targets, sibling


def _write_fresh_sibling(src: Path, body: str = "compressed"):
    """Build a genuinely-fresh sibling for src using the real writer."""
    from harness.compress_cli import rebuild_one
    src.write_text("verbose original text", encoding="utf-8")
    rebuild_one(src, call_model=lambda p: body, today="2026-06-30")


def _isolate(monkeypatch, tmp_path):
    """Point config + persona dirs at tmp so we read no real workspaces."""
    from harness import paths, persona_select, config
    cfg = tmp_path / "cfg"; (cfg / "agents").mkdir(parents=True)
    monkeypatch.setattr(paths, "config_dir", lambda: cfg)
    monkeypatch.setattr(paths, "default_workspace_dir", lambda: cfg / "agents" / "default")
    (cfg / "agents" / "default").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "compress_aware_pinned", lambda pid="default": True)
    return cfg


def test_stale_existing_sibling_is_selected(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    cwd = tmp_path / "proj"; cwd.mkdir()
    src = cwd / "AGENTS.md"
    _write_fresh_sibling(src)
    src.write_text("EDITED — now stale", encoding="utf-8")   # source changed → sibling stale
    result = targets.stale_existing_siblings(cwd=cwd)
    assert src in result


def test_source_without_sibling_is_ignored(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    cwd = tmp_path / "proj"; cwd.mkdir()
    (cwd / "AGENTS.md").write_text("no sibling here", encoding="utf-8")
    assert targets.stale_existing_siblings(cwd=cwd) == []


def test_fresh_sibling_is_skipped(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    cwd = tmp_path / "proj"; cwd.mkdir()
    src = cwd / "AGENTS.md"
    _write_fresh_sibling(src)                                # left fresh
    assert targets.stale_existing_siblings(cwd=cwd) == []


def test_corrupt_sibling_is_selected_and_does_not_crash(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    cwd = tmp_path / "proj"; cwd.mkdir()
    src = cwd / "AGENTS.md"; src.write_text("orig", encoding="utf-8")
    sibling.sibling_path(src).write_text("no valid header — corrupt", encoding="utf-8")
    result = targets.stale_existing_siblings(cwd=cwd)        # must not raise
    assert src in result


def test_compress_aware_off_skips_cwd_files(monkeypatch, tmp_path):
    """Finding 2: when default compress_aware is False, cwd AGENTS.md must not appear."""
    _isolate(monkeypatch, tmp_path)
    from harness import config
    monkeypatch.setattr(config, "compress_aware_pinned", lambda pid="default": False)
    cwd = tmp_path / "proj"; cwd.mkdir()
    agents_md = cwd / "AGENTS.md"
    agents_md.write_text("# agents", encoding="utf-8")
    result = targets.candidate_sources(cwd=cwd)
    assert agents_md not in result, "cwd files must be excluded when default compress_aware is off"


def test_compress_aware_off_skips_persona_files(monkeypatch, tmp_path):
    cfg = _isolate(monkeypatch, tmp_path)
    from harness import config
    monkeypatch.setattr(config, "compress_aware_pinned", lambda pid="default": False)
    soul = cfg / "agents" / "default" / "SOUL.md"
    from harness.compress_cli import rebuild_one
    soul.write_text("orig", encoding="utf-8")
    rebuild_one(soul, call_model=lambda p: "x", today="2026-06-30")
    soul.write_text("EDITED", encoding="utf-8")              # stale, but persona compress is OFF
    assert soul not in targets.stale_existing_siblings()
