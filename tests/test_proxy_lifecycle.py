from harness.proxy_service import lifecycle, binary


def test_start_uses_service_manager(monkeypatch):
    calls = []
    monkeypatch.setattr(lifecycle, "_run", lambda argv: (calls.append(argv) or (0, "")))
    monkeypatch.setattr(lifecycle.platform, "system", lambda: "Darwin")
    out = lifecycle.start()
    assert calls and any("launchctl" in a[0] for a in calls)
    assert "start" in out.lower() or "started" in out.lower()


def test_stop_reports_failure_gracefully(monkeypatch):
    monkeypatch.setattr(lifecycle, "_run", lambda argv: (1, "boom"))
    monkeypatch.setattr(lifecycle.platform, "system", lambda: "Linux")
    out = lifecycle.stop()
    assert "boom" in out or "fail" in out.lower()


def test_install_downloads_then_registers_and_starts(monkeypatch, tmp_path):
    seq = []
    monkeypatch.setattr(lifecycle.download, "download_and_install",
                        lambda v: (seq.append("download") or tmp_path / "cli-proxy-api"))
    (tmp_path / "cli-proxy-api").write_text("x")
    monkeypatch.setattr(lifecycle, "_register_os_service",
                        lambda *a: (seq.append("register") or "registered"))
    monkeypatch.setattr(lifecycle, "start", lambda: (seq.append("start") or "started"))
    monkeypatch.setattr(lifecycle.management, "is_ready", lambda pw: True)
    monkeypatch.setattr(lifecycle.binary, "target_path", lambda: tmp_path / "cli-proxy-api")
    out = lifecycle.install()
    assert seq == ["download", "register", "start"]
    assert "running" in out.lower() or "started" in out.lower()


def test_uninstall_stops_and_removes_data_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(lifecycle, "stop", lambda: "stopped")
    monkeypatch.setattr(lifecycle, "_deregister_os_service", lambda: "deregistered")
    monkeypatch.setattr(lifecycle.paths, "data_dir", lambda: tmp_path)
    (tmp_path / "config.yaml").write_text("x")
    out = lifecycle.uninstall()
    assert not (tmp_path / "config.yaml").exists()
    assert "removed" in out.lower() or "uninstall" in out.lower()


def test_install_aborts_on_checksum_mismatch(monkeypatch):
    """Security invariant: a failed binary verification must abort install
    BEFORE the OS service is registered or started — never run an unverified binary."""
    from harness.proxy_service import lifecycle, download
    reached = []
    def _boom(version):
        raise download.ChecksumMismatch("bad sha")
    monkeypatch.setattr(lifecycle.download, "download_and_install", _boom)
    monkeypatch.setattr(lifecycle, "_register_os_service",
                        lambda *a: reached.append("register") or "registered")
    monkeypatch.setattr(lifecycle, "start", lambda: reached.append("start") or "started")
    monkeypatch.setattr(lifecycle.config_gen, "ensure_management_password", lambda: "pw")
    monkeypatch.setattr(lifecycle.config_gen, "generate", lambda: "host: x\n")
    out = lifecycle.install()
    assert reached == []                         # register + start NEVER ran
    assert "verif" in out.lower() or "checksum" in out.lower() or "fail" in out.lower()


def test_login_stops_runs_foreground_then_restarts(monkeypatch, tmp_path):
    """The proven flow: stop the service, run the binary's foreground -<provider>-login
    as the sole instance (so it owns its callback), then restart the service."""
    seq = []
    binp = tmp_path / "cli-proxy-api"; binp.write_text("x")
    monkeypatch.setattr(lifecycle.binary, "target_path", lambda: binp)
    monkeypatch.setattr(lifecycle.config_gen, "ensure_management_password", lambda: "pw")
    monkeypatch.setattr(lifecycle.paths, "config_path", lambda: tmp_path / "config.yaml")
    monkeypatch.setattr(lifecycle, "stop", lambda: seq.append("stop") or "stopped")
    monkeypatch.setattr(lifecycle, "start", lambda: seq.append("start") or "started")
    captured = {}
    def _fake_interactive(argv):
        seq.append("login"); captured["argv"] = argv; return (0, "")
    monkeypatch.setattr(lifecycle, "_run_interactive", _fake_interactive)
    out = lifecycle.login("claude")               # alias → anthropic → -claude-login
    assert seq == ["stop", "login", "start"]      # order matters
    assert "-claude-login" in captured["argv"]
    assert "authenticated" in out.lower()


def test_login_restarts_service_even_when_login_fails(monkeypatch, tmp_path):
    """A failed/aborted login must still bring the service back up — never leave
    the proxy down."""
    seq = []
    binp = tmp_path / "cli-proxy-api"; binp.write_text("x")
    monkeypatch.setattr(lifecycle.binary, "target_path", lambda: binp)
    monkeypatch.setattr(lifecycle.config_gen, "ensure_management_password", lambda: "pw")
    monkeypatch.setattr(lifecycle.paths, "config_path", lambda: tmp_path / "config.yaml")
    monkeypatch.setattr(lifecycle, "stop", lambda: seq.append("stop") or "stopped")
    monkeypatch.setattr(lifecycle, "start", lambda: seq.append("start") or "started")
    monkeypatch.setattr(lifecycle, "_run_interactive",
                        lambda argv: (seq.append("login") or (1, "user aborted")))
    out = lifecycle.login("codex")
    assert "start" in seq                         # service restarted despite failure
    assert "did not complete" in out.lower()


def test_upgrade_regenerates_config_with_neuralwatt(monkeypatch, tmp_path):
    """upgrade() must rewrite config.yaml (not only re-download the binary), so a
    newly-set NEURALWATT_API_KEY is picked up on restart. Without this, the
    documented `set key then dn proxy upgrade` remedy silently does nothing."""
    seq = []
    cfg = tmp_path / "config.yaml"
    monkeypatch.setattr(lifecycle.download, "download_and_install",
                        lambda v: seq.append("download") or (tmp_path / "cli-proxy-api"))
    monkeypatch.setattr(lifecycle.config_gen, "ensure_management_password", lambda: "pw")
    monkeypatch.setattr(lifecycle.paths, "config_path", lambda: cfg)
    monkeypatch.setattr(lifecycle, "stop", lambda: seq.append("stop") or "stopped")
    monkeypatch.setattr(lifecycle, "start", lambda: seq.append("start") or "started")
    monkeypatch.setenv("NEURALWATT_API_KEY", "nw-xyz")

    out = lifecycle.upgrade()

    # config was regenerated AFTER download and BEFORE restart
    assert seq == ["download", "stop", "start"]
    written = cfg.read_text()
    assert "openai-compatibility" in written
    assert 'alias: "glm-5.2"' in written and 'alias: "qwen3.5-397b-fast"' in written
    assert "complete" in out.lower()


def test_upgrade_aborts_before_restart_on_config_write_failure(monkeypatch, tmp_path):
    """A config-write failure must abort before stop/start — never restart the
    service against a half-written config."""
    seq = []
    monkeypatch.setattr(lifecycle.download, "download_and_install",
                        lambda v: seq.append("download") or (tmp_path / "cli-proxy-api"))
    monkeypatch.setattr(lifecycle.config_gen, "ensure_management_password", lambda: "pw")
    monkeypatch.setattr(lifecycle.config_gen, "generate",
                        lambda: (_ for _ in ()).throw(OSError("disk full")))
    monkeypatch.setattr(lifecycle.paths, "config_path", lambda: tmp_path / "config.yaml")
    monkeypatch.setattr(lifecycle, "stop", lambda: seq.append("stop") or "stopped")
    monkeypatch.setattr(lifecycle, "start", lambda: seq.append("start") or "started")

    out = lifecycle.upgrade()
    assert "stop" not in seq and "start" not in seq      # never restarted
    assert "config write failed" in out.lower()


def test_login_rejects_unknown_provider():
    out = lifecycle.login("banana")
    assert "unknown" in out.lower() or "choose" in out.lower()


def test_login_requires_installed_binary(monkeypatch, tmp_path):
    """Before `dn proxy install` the binary is absent — guide the user, don't crash
    and don't stop a (nonexistent) service."""
    seq = []
    monkeypatch.setattr(lifecycle.binary, "target_path", lambda: tmp_path / "nope")
    monkeypatch.setattr(lifecycle, "stop", lambda: seq.append("stop") or "")
    out = lifecycle.login("claude")
    assert "stop" not in seq                       # didn't touch the service
    assert "dn proxy install" in out


def test_status_warns_when_config_drifted(monkeypatch):
    monkeypatch.setattr(lifecycle.config_gen, "ensure_management_password", lambda: "pw")
    monkeypatch.setattr(lifecycle.management, "is_ready", lambda pw: False)
    monkeypatch.setattr(lifecycle.config_gen, "config_drift", lambda: "drifted")
    out = lifecycle.status()
    assert "proxy config stale" in out.lower()
    assert "dn proxy upgrade" in out


def test_status_no_warning_when_config_ok(monkeypatch):
    monkeypatch.setattr(lifecycle.config_gen, "ensure_management_password", lambda: "pw")
    monkeypatch.setattr(lifecycle.management, "is_ready", lambda pw: False)
    monkeypatch.setattr(lifecycle.config_gen, "config_drift", lambda: "ok")
    out = lifecycle.status()
    assert "stale" not in out.lower()


def test_status_no_warning_when_config_missing(monkeypatch):
    # "missing" means never-installed — Task 2's auto_install handles this on
    # session_start. status() should not also nag about it as "stale".
    monkeypatch.setattr(lifecycle.config_gen, "ensure_management_password", lambda: "pw")
    monkeypatch.setattr(lifecycle.management, "is_ready", lambda pw: False)
    monkeypatch.setattr(lifecycle.config_gen, "config_drift", lambda: "missing")
    out = lifecycle.status()
    assert "stale" not in out.lower()


def test_install_reports_config_summary(monkeypatch, tmp_path):
    from harness.proxy_service import lifecycle, config_gen, paths
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(lifecycle.download, "download_and_install", lambda v: tmp_path / "bin")
    monkeypatch.setattr(lifecycle, "_register_os_service", lambda *a: "")
    monkeypatch.setattr(lifecycle, "start", lambda: "started")
    monkeypatch.setattr(lifecycle.management, "is_ready", lambda pw: True)
    monkeypatch.setattr(config_gen, "generate", lambda env=None: 'host: "x"\n')
    out = lifecycle.install()
    assert "NO upstream providers" in out


def test_upgrade_names_removed_provider(monkeypatch, tmp_path):
    from harness.proxy_service import lifecycle, config_gen, paths
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)
    # Capture real texts BEFORE stubbing: old on-disk = keyed, new generate = keyless.
    keyed = config_gen.generate(env={"NEURALWATT_API_KEY": "sk-x"})
    keyless = config_gen.generate(env={})
    paths.config_path().write_text(keyed)
    monkeypatch.setattr(lifecycle.download, "download_and_install", lambda v: tmp_path / "bin")
    monkeypatch.setattr(lifecycle, "stop", lambda: "stopped")
    monkeypatch.setattr(lifecycle, "start", lambda: "started")
    monkeypatch.setattr(config_gen, "generate", lambda env=None: keyless)
    out = lifecycle.upgrade()
    assert "removed: neuralwatt" in out
