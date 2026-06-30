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


def test_rebuild_one_returns_error_on_model_failure(tmp_path):
    # A model/network failure (e.g. the proxy is down) must NOT crash the CLI.
    # rebuild_one returns a single-line "error: <reason>" status and writes no
    # sibling, so `dn compress` can print it cleanly instead of a stack trace.
    src = tmp_path / "AGENTS.md"
    src.write_text("some prose")

    def boom(prompt):
        raise RuntimeError("Connection refused")   # stand-in for litellm InternalServerError

    out = compress_cli.rebuild_one(src, call_model=boom, today="2026-06-29")
    assert out.startswith("error:")
    assert "Connection refused" in out
    assert not sibling.sibling_path(src).exists()   # no sibling written on failure


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


def test_rebuild_skill_cache_builds_entries(tmp_path, monkeypatch):
    from harness import compress_cli, skills, paths
    from harness.compress import skill_cache
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path / "cfg")
    root = tmp_path / "skills"
    d = root / "foo"; d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: foo\ndescription: d\n---\nYou should really read https://x.io")
    monkeypatch.setattr(compress_cli, "_skill_roots_for_rebuild", lambda: [root], raising=False)
    res = compress_cli.rebuild_skill_cache(call_model=lambda p: "read https://x.io")
    assert res["built"] == 1
    # the cached body is now retrievable for foo's source body
    _, body = skills._parse_skill_md(d / "SKILL.md")
    assert skill_cache.cached_body(body) == "read https://x.io"


def test_rebuild_skill_cache_counts_failed_on_compression_error(tmp_path, monkeypatch):
    from harness import compress_cli, paths
    from harness.compress import engine
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path / "cfg")
    root = tmp_path / "skills"; d = root / "foo"; d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: foo\ndescription: keep https://x.io\n---\nbody https://x.io")
    monkeypatch.setattr(compress_cli, "_skill_roots_for_rebuild", lambda: [root], raising=False)
    # Mock engine.compress_text to raise CompressionError when URL validation fails
    def mock_compress_text(body, call_model):
        result = call_model(body)
        if "https://" not in result:  # URL was dropped
            raise engine.CompressionError("validation failed: URL not preserved")
        return result
    monkeypatch.setattr(engine, "compress_text", mock_compress_text, raising=False)
    res = compress_cli.rebuild_skill_cache(call_model=lambda p: "dropped the url entirely")
    assert res["failed"] == 1 and res["built"] == 0


def test_rebuild_skill_cache_counts_skipped_on_parse_error(tmp_path, monkeypatch):
    from harness import compress_cli, paths
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path / "cfg")
    root = tmp_path / "skills"; d = root / "bad"; d.mkdir(parents=True)
    (d / "SKILL.md").write_text("no frontmatter fence here")   # _parse_skill_md raises
    monkeypatch.setattr(compress_cli, "_skill_roots_for_rebuild", lambda: [root], raising=False)
    res = compress_cli.rebuild_skill_cache(call_model=lambda p: "x")
    assert res["skipped"] == 1 and res["built"] == 0


def test_rebuild_skill_cache_counts_failed_on_model_error(tmp_path, monkeypatch):
    from harness import compress_cli, paths
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path / "cfg")
    root = tmp_path / "skills"; d = root / "foo"; d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: foo\ndescription: d\n---\nbody")
    monkeypatch.setattr(compress_cli, "_skill_roots_for_rebuild", lambda: [root], raising=False)
    def boom(prompt):
        raise RuntimeError("network down")   # NOT a CompressionError
    res = compress_cli.rebuild_skill_cache(call_model=boom)
    assert res["failed"] == 1 and res["built"] == 0   # counted, not crashed


def test_run_skills_no_model_returns_message(tmp_path, monkeypatch, capsys):
    from harness import compress_cli
    _isolate_conf(monkeypatch, tmp_path, 'schema_version = 1\n')  # empty config
    monkeypatch.delenv("COMPRESS_MODEL", raising=False)
    monkeypatch.delenv("VIBEPROXY_MODEL", raising=False)
    monkeypatch.setattr(compress_cli, "_build_call_model", lambda: None, raising=False)
    rc = compress_cli.run(["--skills"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "compression unavailable" in out.lower()


def test_default_targets_includes_persona_voice_files(monkeypatch, tmp_path):
    from harness import compress_cli, paths, config, persona_select
    cfg = tmp_path / "cfg"; (cfg / "agents" / "default").mkdir(parents=True)
    monkeypatch.setattr(paths, "config_dir", lambda: cfg)
    monkeypatch.setattr(paths, "default_workspace_dir", lambda: cfg / "agents" / "default")
    monkeypatch.setattr(config, "compress_aware_pinned", lambda pid="default": True)
    soul = cfg / "agents" / "default" / "SOUL.md"; soul.write_text("s", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    targets = compress_cli._default_targets()
    assert soul in [p.resolve() for p in targets] or soul in targets
