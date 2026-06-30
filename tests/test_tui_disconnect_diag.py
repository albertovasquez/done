"""Disconnect diagnostics: _format_exit rendering + the _send_prompt disconnect
handler surfacing exit cause and buffered stderr. Exercised directly on a bare
HarnessTui (no Textual app, no subprocess), mirroring test_tui_stderr_drain.py.
"""

import asyncio
import signal
import sys

sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

from harness.tui.app import HarnessTui


def _bare_app():
    return HarnessTui(agent_cmd=["x"], cwd=".", model="mock")


def test_format_exit_none_is_unknown():
    assert HarnessTui._format_exit(None) == "exit status unknown"


def test_format_exit_zero_is_clean_code():
    assert HarnessTui._format_exit(0) == "exited with code 0"


def test_format_exit_positive_code():
    assert HarnessTui._format_exit(3) == "exited with code 3"


def test_format_exit_signal_name():
    # -11 = killed by SIGSEGV
    assert HarnessTui._format_exit(-signal.SIGSEGV) == "killed by SIGSEGV"


def test_format_exit_unrecognized_signal_falls_back():
    # an absurd negative value with no Signals member must not raise
    out = HarnessTui._format_exit(-9999)
    assert out == "killed by signal 9999", out


class _Proc:
    """Minimal subprocess stand-in: a .stderr StreamReader and a returncode."""
    def __init__(self, reader, returncode=None):
        self.stderr = reader
        self.returncode = returncode


def _reader_with(lines):
    # MUST be built inside a running loop (StreamReader binds the loop at init).
    r = asyncio.StreamReader()
    for ln in lines:
        r.feed_data(ln)
    r.feed_eof()
    return r


def test_drain_appends_to_stderr_tail_without_tracer():
    """The ring buffer is populated even when no --debug tracer is set."""
    app = _bare_app()
    app._tracer = None  # no debug relay — buffer must still fill

    async def go():
        reader = _reader_with([b"alpha\n", b"beta\n"])
        await asyncio.wait_for(app._drain_stderr(_Proc(reader)), timeout=5.0)

    asyncio.run(go())
    assert list(app._stderr_tail) == ["alpha", "beta"], list(app._stderr_tail)


def test_stderr_tail_is_bounded_to_20():
    app = _bare_app()
    app._tracer = None

    async def go():
        reader = _reader_with([f"line{i}\n".encode() for i in range(30)])
        await asyncio.wait_for(app._drain_stderr(_Proc(reader)), timeout=5.0)

    asyncio.run(go())
    tail = list(app._stderr_tail)
    assert len(tail) == 20, len(tail)
    assert tail[0] == "line10" and tail[-1] == "line29", tail


def _drive_report(app, exc):
    """Run _report_disconnect on a bare app under a fresh loop, capturing
    every _append_line call into app._captured_lines."""
    app._captured_lines = []
    app._append_line = lambda s: app._captured_lines.append(s)

    async def go():
        await asyncio.wait_for(app._report_disconnect(exc), timeout=5.0)

    asyncio.run(go())
    return "\n".join(app._captured_lines)


def test_report_disconnect_shows_signal_and_stderr_tail():
    app = _bare_app()
    app._stderr_task = None                       # nothing to flush
    app._proc = _Proc(None, returncode=-signal.SIGSEGV)
    app._stderr_tail.extend(["Fatal Python error: Segmentation fault", "  frame 0"])

    out = _drive_report(app, ConnectionError("Connection closed"))

    assert "agent disconnected" in out
    assert "killed by SIGSEGV" in out
    assert "Fatal Python error: Segmentation fault" in out
    assert "frame 0" in out
    assert "Connection closed" in out             # original exception text retained


def test_report_disconnect_flushes_drain_before_reading_tail():
    """The handler must await the running drain so the LAST stderr line (the
    crash cause), which arrives after a delay, is included — not raced past."""
    app = _bare_app()
    app._proc = _Proc(None, returncode=-signal.SIGSEGV)

    async def slow_drain():
        await asyncio.sleep(0.05)                 # final line lands late
        app._stderr_tail.append("LATE crash line")

    async def go():
        app._captured_lines = []
        app._append_line = lambda s: app._captured_lines.append(s)
        app._stderr_task = asyncio.create_task(slow_drain())
        await asyncio.wait_for(app._report_disconnect(ConnectionError("x")), timeout=5.0)
        return "\n".join(app._captured_lines)

    out = asyncio.run(go())
    assert "LATE crash line" in out, out          # proves we awaited the drain


def test_report_disconnect_unknown_when_returncode_none():
    app = _bare_app()
    app._stderr_task = None
    app._proc = _Proc(None, returncode=None)      # not reaped; wait() also yields None below

    # _Proc has no wait(); give it one that returns immediately with no code
    async def _wait():
        return None
    app._proc.wait = _wait

    out = _drive_report(app, ConnectionError("x"))
    assert "exit status unknown" in out, out


def test_report_disconnect_propagates_outer_cancellation():
    """Cancelling the WORKER running _report_disconnect (e.g. workers.cancel_all()
    during teardown) must actually stop it — not be swallowed by the flush guard.
    The shielded drain task itself must be left running (shield's whole point)."""
    app = _bare_app()
    app._proc = _Proc(None, returncode=-signal.SIGSEGV)

    async def go():
        app._captured_lines = []
        app._append_line = lambda s: app._captured_lines.append(s)
        drain_task = asyncio.create_task(asyncio.sleep(10))
        app._stderr_task = drain_task

        report_task = asyncio.create_task(app._report_disconnect(ConnectionError("x")))
        await asyncio.sleep(0.05)          # let the handler park at the shielded wait_for
        report_task.cancel()

        raised = None
        try:
            await report_task
        except asyncio.CancelledError as e:
            raised = e

        was_cancelled = drain_task.cancelled()
        was_done = drain_task.done()

        drain_task.cancel()                # cleanup: avoid "task was destroyed" warnings
        try:
            await drain_task
        except asyncio.CancelledError:
            pass

        return raised, was_cancelled, was_done

    raised, drain_was_cancelled, drain_was_done = asyncio.run(go())
    assert isinstance(raised, asyncio.CancelledError), raised   # not swallowed
    assert not drain_was_cancelled, "shield() must protect the drain task from the outer cancel"
    assert not drain_was_done, "drain task should still be pending (shielded)"
