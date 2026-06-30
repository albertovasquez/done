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
