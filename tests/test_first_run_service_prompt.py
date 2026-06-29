import pytest
from harness.jobs.service import ServiceResult


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr("harness.paths.config_dir", lambda: tmp_path)


def _decide(monkeypatch, *, backend, installed, asked):
    """Drive HarnessTui._decide_cron_autostart with stubbed environment."""
    from harness.tui.app import HarnessTui
    import harness.jobs.service as svc
    import harness.jobs.prompt_state as ps
    import harness.jobs.supervisor as sup
    monkeypatch.setattr(svc, "current_backend", lambda: backend)
    monkeypatch.setattr(svc, "service_status",
                        lambda: ServiceResult(True, backend,
                                              "installed" if installed else "not-installed", ""))
    monkeypatch.setattr(ps, "has_been_asked", lambda: asked)
    monkeypatch.setattr(sup, "ensure_daemon_running", lambda **k: "spawned")
    # Build a bare instance without running the full app:
    app = HarnessTui.__new__(HarnessTui)
    return app._decide_cron_autostart(show_prompt=lambda: None)


def test_os_service_present_does_nothing(monkeypatch):
    assert _decide(monkeypatch, backend="launchd", installed=True, asked=True) == "os-service-present"


def test_first_run_supported_platform_prompts(monkeypatch):
    assert _decide(monkeypatch, backend="launchd", installed=False, asked=False) == "prompted"


def test_declined_before_falls_back_to_spawn(monkeypatch):
    assert _decide(monkeypatch, backend="launchd", installed=False, asked=True) == "fallback-spawn"


def test_unsupported_platform_falls_back_to_spawn(monkeypatch):
    assert _decide(monkeypatch, backend="unsupported", installed=False, asked=False) == "fallback-spawn"
