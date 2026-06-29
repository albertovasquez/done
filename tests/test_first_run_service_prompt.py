import pytest
from harness.jobs.service import ServiceResult


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr("harness.paths.config_dir", lambda: tmp_path)


def _decide(monkeypatch, *, backend, installed, asked, spawns=None):
    """Drive HarnessTui._decide_cron_autostart with stubbed environment.

    If `spawns` is a list, every ensure_daemon_running() call appends to it, so a
    test can assert whether the current session got a fallback daemon.
    """
    from harness.tui.app import HarnessTui
    import harness.jobs.service as svc
    import harness.jobs.prompt_state as ps
    import harness.jobs.supervisor as sup
    monkeypatch.setattr(svc, "current_backend", lambda: backend)
    monkeypatch.setattr(svc, "service_status",
                        lambda: ServiceResult(True, backend,
                                              "installed" if installed else "not-installed", ""))
    monkeypatch.setattr(ps, "has_been_asked", lambda: asked)

    def _spawn(**k):
        if spawns is not None:
            spawns.append(True)
        return "spawned"
    monkeypatch.setattr(sup, "ensure_daemon_running", _spawn)
    # Build a bare instance without running the full app:
    app = HarnessTui.__new__(HarnessTui)
    return app._decide_cron_autostart(show_prompt=lambda: None)


def test_os_service_present_does_nothing(monkeypatch):
    spawns = []
    assert _decide(monkeypatch, backend="launchd", installed=True, asked=True,
                   spawns=spawns) == "os-service-present"
    assert spawns == []                      # OS owns it → no fallback spawn


def test_first_run_supported_platform_prompts(monkeypatch):
    assert _decide(monkeypatch, backend="launchd", installed=False, asked=False) == "prompted"


def test_first_run_prompt_also_covers_this_session(monkeypatch):
    # #165: showing the opt-in prompt must ALSO start the fallback daemon for the
    # current session — the modal governs the durable OS service, not whether jobs
    # fire right now. Whatever the user clicks, jobs created this session fire.
    spawns = []
    assert _decide(monkeypatch, backend="launchd", installed=False, asked=False,
                   spawns=spawns) == "prompted"
    assert spawns == [True]                  # current session is covered


def test_declined_before_falls_back_to_spawn(monkeypatch):
    spawns = []
    assert _decide(monkeypatch, backend="launchd", installed=False, asked=True,
                   spawns=spawns) == "fallback-spawn"
    assert spawns == [True]


def test_unsupported_platform_falls_back_to_spawn(monkeypatch):
    spawns = []
    assert _decide(monkeypatch, backend="unsupported", installed=False, asked=False,
                   spawns=spawns) == "fallback-spawn"
    assert spawns == [True]
