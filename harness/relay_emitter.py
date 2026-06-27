"""RelayEmitter: a drop-in Emitter that ALSO forwards each event to a relay
callback (used by the agent to push TracingAgent's event stream to the TUI over
ACP). Subclassing Emitter means TracingAgent — which only calls emit()/set_clock()
— needs no change; we just stop sending its events to /dev/null."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from harness.events import Emitter, Event


class RelayEmitter(Emitter):
    def __init__(self, jsonl_path: str | Path, *, clock,
                 relay: Callable[[dict], None], console: bool = False):
        super().__init__(jsonl_path, clock=clock, console=console)
        self._relay = relay

    def write_event(self, event: Event) -> None:
        super().write_event(event)            # keep the file/console sink behavior
        try:
            self._relay({"type": event.type, "data": dict(event.data)})
        except Exception:  # noqa: BLE001 — observation must never abort the observed
            pass
