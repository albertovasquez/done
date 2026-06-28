"""The TUI must drain the agent subprocess's stderr.

spawn_agent_process pipes the agent's stderr (acp transports.py: stderr=PIPE) but
nothing reads it. On a chat turn the agent (via litellm etc.) writes to stderr;
once the ~64KB pipe buffer fills, the agent BLOCKS on its next stderr write —
mid-turn, after streaming chat.done but before writing the prompt RESPONSE frame.
The TUI's `await prompt()` then never resolves: "Responding…" sticks and the
composer locks (the reported bug). HarnessTui._drain_stderr reads stderr to EOF
so the buffer can never fill.

These exercise the drain coroutine directly with a fake stderr StreamReader — no
subprocess — so they're fast and deterministic.
"""

import asyncio
import sys

sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

from harness.tui.app import HarnessTui


class _RecordingTracer:
    def __init__(self):
        self.events = []

    def emit(self, source, type, **data):
        self.events.append((source, type, data))


class _Proc:
    """Minimal stand-in for the subprocess: just a .stderr StreamReader."""
    def __init__(self, reader):
        self.stderr = reader


def _bare_app():
    # construct without running the Textual app; we only call _drain_stderr
    return HarnessTui(agent_cmd=["x"], cwd=".", model="mock")


def _reader_with(lines: list[bytes]) -> asyncio.StreamReader:
    # MUST be built inside a running loop: StreamReader() binds the current event
    # loop at construction (get_event_loop()), which raises in a bare MainThread.
    r = asyncio.StreamReader()
    for ln in lines:
        r.feed_data(ln)
    r.feed_eof()
    return r


def test_drain_reads_stderr_to_eof():
    """_drain_stderr returns when stderr hits EOF — it must not block forever,
    and it must consume every line (so the pipe buffer never fills)."""
    app = _bare_app()
    app._tracer = _RecordingTracer()

    async def go():
        reader = _reader_with([b"line one\n", b"line two\n"])
        # if the drain didn't stop at EOF this wait_for would time out
        await asyncio.wait_for(app._drain_stderr(_Proc(reader)), timeout=5.0)

    asyncio.run(go())
    # both lines were consumed and relayed to the tracer
    texts = [d["text"] for s, t, d in app._tracer.events if t == "stderr"]
    assert texts == ["line one", "line two"], texts


def test_drain_handles_no_stderr():
    """A proc without a stderr stream is a no-op, not a crash."""
    app = _bare_app()
    app._tracer = _RecordingTracer()

    async def go():
        await asyncio.wait_for(app._drain_stderr(_Proc(None)), timeout=5.0)

    asyncio.run(go())  # must simply return
    assert app._tracer.events == []
