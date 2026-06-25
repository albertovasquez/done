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


class Emitter:
    def __init__(self, jsonl_path: str | Path, *, clock: Callable[[], float], console: bool = True):
        self._clock = clock
        self._console = console
        self._seq = 0
        # Loud failure at startup if the JSONL artifact cannot be created.
        self._fh = open(jsonl_path, "w", encoding="utf-8")

    def emit(self, type: str, **data: Any) -> Event:
        event = Event(seq=self._seq, t=round(self._clock(), 3), type=type, data=data)
        self._seq += 1
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
        return event

    def _print_console(self, event: Event) -> None:
        parts = " ".join(f"{k}={v}" for k, v in event.data.items())
        print(f"[t={event.t:>5.1f}s] {event.type:<13} {parts}")

    def set_clock(self, clock: Callable[[], float]) -> None:
        """Let the agent install its own run-relative clock at run start."""
        self._clock = clock

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:  # noqa: BLE001
            logger.exception("failed to close JSONL file")
