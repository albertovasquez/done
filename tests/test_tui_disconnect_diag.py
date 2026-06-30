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
