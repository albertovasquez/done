"""Event model and Emitter: the single point where a run becomes observable.

Two sinks share one Event:
  - JSONL file (durable; backs success criterion #2 — fails loudly if unopenable)
  - console (human-readable; best-effort, never crashes the run)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("trace.events")


@dataclass
class Event:
    seq: int
    t: float
    type: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"seq": self.seq, "t": self.t, "type": self.type, "data": self.data}


class _EventSource:
    """Owns the monotonic seq counter and the run clock; builds Events.
    Shared by Emitter (file/console) and QueueEmitter (runner)."""

    def __init__(self, clock: Callable[[], float]):
        self._clock = clock
        self._seq = 0

    def _next_event(self, type: str, **data: Any) -> Event:
        event = Event(seq=self._seq, t=round(self._clock(), 3), type=type, data=data)
        self._seq += 1
        return event

    def set_clock(self, clock: Callable[[], float]) -> None:
        """Let the agent install its own run-relative clock at run start."""
        self._clock = clock


class Emitter(_EventSource):
    def __init__(self, jsonl_path: str | Path, *, clock: Callable[[], float], console: bool = True):
        super().__init__(clock)
        self._console = console
        # Loud failure at startup if the JSONL artifact cannot be created.
        self._fh = open(jsonl_path, "w", encoding="utf-8")

    def emit(self, type: str, **data: Any) -> Event:
        event = self._next_event(type, **data)
        self.write_event(event)
        return event

    def write_event(self, event: Event) -> None:
        """Persist an already-built event WITHOUT reassigning its seq/t.
        Used by emit() and by clients consuming events the runner already built."""
        # JSONL sink: best-effort per-line, but log on failure.
        try:
            self._fh.write(json.dumps(event.to_dict()) + "\n")
            self._fh.flush()
        except Exception:  # noqa: BLE001 — observation must not abort the observed
            logger.exception("failed to write event to JSONL")
        # Console sink: never crash the run.
        if self._console:
            try:
                self._print_console(event)
            except Exception:  # noqa: BLE001
                logger.exception("failed to print event to console")

    def _print_console(self, event: Event) -> None:
        parts = " ".join(f"{k}={v}" for k, v in event.data.items())
        print(f"[t={event.t:>5.1f}s] {event.type:<13} {parts}")

    def write_renumbered(self, event: Event) -> None:
        """Write an externally-built event but reassign its seq to THIS emitter's
        next value, so a single emitter keeps one contiguous seq stream across
        events it built (emit) and events built elsewhere (e.g. the runner)."""
        renum = Event(seq=self._seq, t=event.t, type=event.type, data=event.data)
        self._seq += 1
        self.write_event(renum)

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:  # noqa: BLE001
            logger.exception("failed to close JSONL file")
