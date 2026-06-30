from harness.proxy_service import lifecycle


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
