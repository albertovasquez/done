# tests/jobs/test_service.py
import platform
import pytest
from harness.jobs import service


def test_current_backend_matches_platform(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    assert service.current_backend() == "launchd"
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    assert service.current_backend() == "systemd"
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    assert service.current_backend() == "unsupported"


def test_install_on_unsupported_platform_is_clean(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    res = service.install()
    assert res.ok is False
    assert res.state == "unsupported"
    assert "Windows" in res.detail


def test_service_result_shape():
    res = service.ServiceResult(ok=True, backend="launchd", state="installed", detail="x")
    assert (res.ok, res.backend, res.state, res.detail) == (True, "launchd", "installed", "x")
