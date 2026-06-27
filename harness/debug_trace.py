"""DebugTracer: the single writer of the unified --debug trace.

One file (harness/runs/<ts>/trace.jsonl), one writer (the TUI). Wraps the
existing Emitter so the JSONL line shape stays consistent with the CLI's
events.jsonl, but adds a top-level `source` field ("dn" | "agent") so a reader
can tell which process spoke. When --debug is off, NullTracer is used and every
call site is a no-op (preserves the byte-identical-wire invariant)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from harness.events import Emitter


class DebugTracer:
    def __init__(self, emitter: Emitter) -> None:
        self._emitter = emitter

    @classmethod
    def open(cls, run_dir: str | Path) -> "DebugTracer":
        run_dir = Path(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        # Real wall-clock so two processes' events order correctly; console off
        # (the trace is a file — never printed; on the agent, stdout is the wire).
        emitter = Emitter(run_dir / "trace.jsonl", clock=time.time, console=False)
        return cls(emitter)

    def emit(self, source: str, type: str, **data: Any) -> None:
        # Build the Event through the Emitter so seq stays monotonic and the file
        # handle is shared, then serialize with `source` as a sibling of
        # seq/t/type/data (the schema's one addition over the CLI's events.jsonl).
        ev = self._emitter._next_event(type, **data)   # noqa: SLF001 — same package
        line = {"seq": ev.seq, "t": ev.t, "source": source,
                "type": ev.type, "data": ev.data}
        try:
            self._emitter._fh.write(json.dumps(line) + "\n")  # noqa: SLF001
            self._emitter._fh.flush()                         # noqa: SLF001
        except Exception:  # noqa: BLE001 — observation must not abort the observed
            pass

    def close(self) -> None:
        self._emitter.close()


class NullTracer:
    def emit(self, source: str, type: str, **data: Any) -> None:
        return None

    def close(self) -> None:
        return None


def make_tracer(enabled: bool, run_dir: str | Path) -> "DebugTracer | NullTracer":
    return DebugTracer.open(run_dir) if enabled else NullTracer()
