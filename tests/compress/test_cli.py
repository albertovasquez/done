"""Tests for compress_cli: rebuild_one and status_line with injected fake call_model."""
from pathlib import Path
from harness import compress_cli
from harness.compress import sibling


def _isolate_conf(monkeypatch, tmp_path, body: str | None = None):
    """Point config at an empty/temp done.conf so tests never read the real one."""
    from harness import config
    cfgdir = tmp_path / "cfg"; cfgdir.mkdir()
    monkeypatch.setattr(config.paths, "config_dir", lambda: cfgdir)
    if body is not None:
        (cfgdir / "done.conf").write_text(body)


def test_compress_model_name_env_override_wins(monkeypatch, tmp_path):
    # COMPRESS_MODEL env beats everything, including config
    _isolate_conf(monkeypatch, tmp_path,
                  'schema_version = 1\n\n[harness]\ncompress_model = "from-conf"\n')
    monkeypatch.setenv("VIBEPROXY_MODEL", "gpt-5.4")
    monkeypatch.setenv("COMPRESS_MODEL", "claude-haiku-4-5")
    assert compress_cli._compress_model_name() == "claude-haiku-4-5"


def test_compress_model_name_reads_done_conf_harness_section(monkeypatch, tmp_path):
    # no env override -> [harness] compress_model from done.conf
    _isolate_conf(monkeypatch, tmp_path,
                  'schema_version = 1\n\n[harness]\ncompress_model = "claude-haiku-4-5"\n')
    monkeypatch.delenv("COMPRESS_MODEL", raising=False)
    monkeypatch.setenv("VIBEPROXY_MODEL", "gpt-5.4")   # config beats VIBEPROXY_MODEL
    assert compress_cli._compress_model_name() == "claude-haiku-4-5"


def test_compress_model_name_falls_back_to_vibeproxy_then_default_agent(monkeypatch, tmp_path):
    # no override, no [harness] -> VIBEPROXY_MODEL env
    _isolate_conf(monkeypatch, tmp_path,
                  'schema_version = 1\n\n[agents.default]\nbackend = "vibeproxy"\nmodel = "agent-model"\n')
    monkeypatch.delenv("COMPRESS_MODEL", raising=False)
    monkeypatch.setenv("VIBEPROXY_MODEL", "gpt-5.4")
    assert compress_cli._compress_model_name() == "gpt-5.4"
    # ... and with no VIBEPROXY_MODEL either, fall back to the default agent's model
    monkeypatch.delenv("VIBEPROXY_MODEL", raising=False)
    assert compress_cli._compress_model_name() == "agent-model"


def test_compress_model_name_none_when_nothing_configured(monkeypatch, tmp_path):
    _isolate_conf(monkeypatch, tmp_path, 'schema_version = 1\n')   # empty conf
    monkeypatch.delenv("COMPRESS_MODEL", raising=False)
    monkeypatch.delenv("VIBEPROXY_MODEL", raising=False)
    assert compress_cli._compress_model_name() is None
    assert compress_cli._build_call_model() is None


def test_rebuild_one_writes_fresh_sibling(tmp_path):
    src = tmp_path / "AGENTS.md"
    src.write_text("You should really read https://x.io now")
    out = compress_cli.rebuild_one(
        src, call_model=lambda p: "read https://x.io now", today="2026-06-29"
    )
    assert out == "built"
    sib = sibling.sibling_path(src)
    assert sibling.freshness(src.read_text(), sib.read_text()) == "fresh"


def test_status_line_reports_stale(tmp_path):
    src = tmp_path / "AGENTS.md"
    src.write_text("original")
    compress_cli.rebuild_one(src, call_model=lambda p: "orig", today="2026-06-29")
    src.write_text("changed now")
    line = compress_cli.status_line(src)
    assert "stale" in line.lower()


def test_status_line_handles_missing_source(tmp_path):
    src = tmp_path / "AGENTS.md"
    # Create a sibling but not the source file
    sib = sibling.sibling_path(src)
    sib.parent.mkdir(parents=True, exist_ok=True)
    sib.write_text("compressed content")
    # Now call status_line with missing source (but existing sibling)
    line = compress_cli.status_line(src)
    assert "missing" in line.lower()   # returns a message, does not raise
