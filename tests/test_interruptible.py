"""Tests for harness.interruptible.run_interruptible — the watchdog helper that
runs a blocking fn() on a daemon worker and aborts (raising UserInterruption)
when a cancel_flag sets before fn() returns. See
docs/superpowers/specs/2026-07-01-esc-cancel-cleanup-design.md."""

from __future__ import annotations

import threading
import time

import pytest

from minisweagent.exceptions import UserInterruption
from harness.interruptible import run_interruptible


def test_returns_result_when_fn_finishes_first():
    flag = threading.Event()
    assert run_interruptible(lambda: 42, flag) == 42


def test_none_flag_runs_inline_no_worker():
    # cancel_flag=None must run fn() on THIS thread (byte-identical to today for
    # CLI/mock/reviewer paths): capture the running thread ident and compare.
    caller = threading.current_thread().ident
    ran_on = {}

    def fn():
        ran_on["ident"] = threading.current_thread().ident
        return "ok"

    assert run_interruptible(fn, None) == "ok"
    assert ran_on["ident"] == caller


def test_reraises_fn_exception():
    class Boom(RuntimeError):
        pass

    def fn():
        raise Boom("kaboom")

    with pytest.raises(Boom, match="kaboom"):
        run_interruptible(fn, threading.Event())


def test_raises_userinterruption_when_flag_sets_mid_flight():
    flag = threading.Event()
    started = threading.Event()
    release = threading.Event()

    def fn():
        started.set()
        release.wait(timeout=5)          # block until the test releases it
        return "should-not-be-returned"

    # Set the flag shortly after fn() starts blocking.
    def canceller():
        started.wait(timeout=5)
        flag.set()

    t = threading.Thread(target=canceller, daemon=True)
    t.start()
    with pytest.raises(UserInterruption):
        run_interruptible(fn, flag, poll_s=0.01)
    release.set()                        # let the abandoned worker unwind


def test_fast_poll_aborts_promptly():
    flag = threading.Event()
    flag.set()                           # already cancelled before we call
    t0 = time.monotonic()
    with pytest.raises(UserInterruption):
        run_interruptible(lambda: time.sleep(5), flag, poll_s=0.01)
    # Must not wait for the 5s fn(): abort within a small multiple of poll_s.
    assert time.monotonic() - t0 < 1.0
