# tests/jobs/test_cron_main.py
"""Tests for harness.jobs.cron_main — the harness-cron console entrypoint."""
from __future__ import annotations

import asyncio


def test_once_calls_tick_and_returns_0(monkeypatch):
    """--once: tick(now) is called exactly once and main returns 0."""
    import time

    fired_at: list[float] = []

    def fake_tick(now, **kwargs):
        fired_at.append(now)
        return []

    monkeypatch.setattr("harness.jobs.cron_main.tick", fake_tick)
    monkeypatch.setattr("harness.jobs.cron_main.load_dotenv", lambda *a, **kw: None)

    from harness.jobs import cron_main

    result = cron_main.main(["--once"])
    assert result == 0
    assert len(fired_at) == 1
    # The timestamp passed to tick must be a plausible Unix epoch (> year 2000)
    assert fired_at[0] > 9.46e8


def test_once_does_not_call_run_forever(monkeypatch):
    """--once must not call run_forever."""
    run_forever_called = []

    async def fake_run_forever(**kwargs):
        run_forever_called.append(kwargs)

    monkeypatch.setattr("harness.jobs.cron_main.run_forever", fake_run_forever)
    monkeypatch.setattr("harness.jobs.cron_main.tick", lambda *a, **kw: [])
    monkeypatch.setattr("harness.jobs.cron_main.load_dotenv", lambda *a, **kw: None)

    from harness.jobs import cron_main

    cron_main.main(["--once"])
    assert run_forever_called == []


def test_default_calls_run_forever(monkeypatch):
    """Without --once, asyncio.run(run_forever(...)) is called with the default interval."""
    kwargs_captured: list[dict] = []

    async def fake_run_forever(**kwargs):
        kwargs_captured.append(kwargs)

    monkeypatch.setattr("harness.jobs.cron_main.run_forever", fake_run_forever)
    monkeypatch.setattr("harness.jobs.cron_main.tick", lambda *a, **kw: [])
    monkeypatch.setattr("harness.jobs.cron_main.load_dotenv", lambda *a, **kw: None)

    from harness.jobs import cron_main

    result = cron_main.main([])
    assert result == 0
    assert len(kwargs_captured) == 1
    kw = kwargs_captured[0]
    assert kw["interval"] == 30.0
    # clock must be time.time (or at least a callable producing float)
    import time
    assert callable(kw["clock"])
    assert kw["clock"]() > 9.46e8
    # sleep must be asyncio.sleep
    assert kw["sleep"] is asyncio.sleep


def test_custom_interval_forwarded(monkeypatch):
    """--interval overrides the default 30.0."""
    kwargs_captured: list[dict] = []

    async def fake_run_forever(**kwargs):
        kwargs_captured.append(kwargs)

    monkeypatch.setattr("harness.jobs.cron_main.run_forever", fake_run_forever)
    monkeypatch.setattr("harness.jobs.cron_main.tick", lambda *a, **kw: [])
    monkeypatch.setattr("harness.jobs.cron_main.load_dotenv", lambda *a, **kw: None)

    from harness.jobs import cron_main

    cron_main.main(["--interval", "60"])
    assert kwargs_captured[0]["interval"] == 60.0


def test_load_dotenv_called_on_once(monkeypatch):
    """CARRY-FORWARD: load_dotenv must be called even in --once mode."""
    load_dotenv_calls: list = []

    monkeypatch.setattr("harness.jobs.cron_main.load_dotenv", lambda *a, **kw: load_dotenv_calls.append((a, kw)))
    monkeypatch.setattr("harness.jobs.cron_main.tick", lambda *a, **kw: [])

    from harness.jobs import cron_main

    cron_main.main(["--once"])
    assert len(load_dotenv_calls) >= 1, "load_dotenv must be called during main() startup"


def test_load_dotenv_called_on_run_forever(monkeypatch):
    """CARRY-FORWARD: load_dotenv must be called even in continuous (no --once) mode."""
    load_dotenv_calls: list = []

    async def fake_run_forever(**kwargs):
        pass

    monkeypatch.setattr("harness.jobs.cron_main.load_dotenv", lambda *a, **kw: load_dotenv_calls.append((a, kw)))
    monkeypatch.setattr("harness.jobs.cron_main.run_forever", fake_run_forever)
    monkeypatch.setattr("harness.jobs.cron_main.tick", lambda *a, **kw: [])

    from harness.jobs import cron_main

    cron_main.main([])
    assert len(load_dotenv_calls) >= 1, "load_dotenv must be called during main() startup"
