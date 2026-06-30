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


def test_login_autostarts_then_runs(monkeypatch):
    seq = []
    monkeypatch.setattr(lifecycle.management, "is_ready",
                        lambda pw: (seq.append("check") or len(seq) > 1))  # False first, True after start
    monkeypatch.setattr(lifecycle, "start", lambda: seq.append("start") or "started")
    import harness.proxy_service.login as login_mod
    monkeypatch.setattr(login_mod, "run_cli_login", lambda *a, **k: seq.append("login") or True)
    out = lifecycle.login("codex")
    assert "start" in seq and "login" in seq
    assert "codex" in out.lower() or "authenticated" in out.lower()


def test_login_rejects_unknown_provider():
    out = lifecycle.login("banana")
    assert "unknown" in out.lower() or "choose" in out.lower()


def test_login_bails_cleanly_when_proxy_never_starts(monkeypatch):
    """Regression: `dn proxy login` before `dn proxy install` must NOT crash with
    a ConnectionRefused traceback — it should return a clear instruction and never
    reach the auth-url network call."""
    seq = []
    # is_ready always False (proxy not installed/running); start() is a no-op string.
    monkeypatch.setattr(lifecycle.management, "is_ready", lambda pw: False)
    monkeypatch.setattr(lifecycle, "start", lambda: "start failed: no service")
    monkeypatch.setattr(lifecycle, "time", type("T", (), {"sleep": staticmethod(lambda s: None)}))
    import harness.proxy_service.login as login_mod
    monkeypatch.setattr(login_mod, "run_cli_login",
                        lambda *a, **k: seq.append("login") or True)
    out = lifecycle.login("claude")
    assert "login" not in seq                     # never reached the network call
    assert "dn proxy install" in out              # clear instruction
    assert "not running" in out.lower()
