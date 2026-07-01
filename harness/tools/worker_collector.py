"""WorkerCollector: coalesces per-worker TracingAgent events into field_meta
progress payloads for the TUI worker card.

Each SubagentTool worker runs on its own pool thread and yields events
(run.started / llm.return / run.finished). on_event() folds them under a lock
into a per-worker state map. Progress emits are gated to ~min_interval seconds so
that N worker threads calling emit() (which marshals onto the single ACP event
loop) do not serialize on that loop and defeat the parallelism subagents exist
for. dispatched()/finished() are single, ungated emits.

emit is a callable that takes {"workers": {...}} — SubagentTool wires it to the
parent env's emit_progress. clock is injected (monotonic in production) so the
time gate is testable without sleeping.
"""
from __future__ import annotations

import threading
from typing import Callable


class WorkerCollector:
    def __init__(self, goals: list[str], *, emit: Callable[[dict], None],
                 clock: Callable[[], float], min_interval: float = 0.08):
        self._emit = emit
        self._clock = clock
        self._min_interval = min_interval
        self._lock = threading.Lock()
        self._last_emit = 0.0
        # idx → row dict. goal is fixed at dispatch; the rest mutate as events fold.
        self._rows: dict[int, dict] = {
            i: {"idx": i, "goal": g, "status": "pending",
                "started_at": 0.0, "elapsed": 0.0, "tokens": 0}
            for i, g in enumerate(goals)
        }

    # ---- emit helpers (caller holds no lock; these snapshot under the lock) ----

    def dispatched(self) -> None:
        with self._lock:
            payload = {"action": "dispatched", "workers": self._rows_list()}
        self._emit({"workers": payload})

    def finished(self) -> None:
        with self._lock:
            ok = sum(1 for r in self._rows.values() if r["status"] == "done")
            failed = sum(1 for r in self._rows.values() if r["status"] == "failed")
            total_elapsed = max((r["elapsed"] for r in self._rows.values()), default=0.0)
            total_tokens = sum(r["tokens"] for r in self._rows.values())
        self._emit({"workers": {"action": "finished", "summary": {
            "ok": ok, "failed": failed,
            "total_elapsed": total_elapsed, "total_tokens": total_tokens}}})

    def on_event(self, idx: int, event) -> None:
        """Fold one worker event; emit a coalesced progress payload if the time
        gate has elapsed. Everything that reads/writes the map is under the lock;
        the emit itself happens after releasing it."""
        payload = None
        with self._lock:
            row = self._rows.get(idx)
            if row is not None:
                self._fold(row, event)
            now = self._clock()
            if now - self._last_emit >= self._min_interval:
                self._last_emit = now
                payload = {"action": "progress", "workers": self._rows_list()}
        if payload is not None:
            self._emit({"workers": payload})

    def snapshot(self) -> list[dict]:
        with self._lock:
            return self._rows_list()

    # ---- internals (lock held by callers) ----

    def _fold(self, row: dict, event) -> None:
        t = getattr(event, "type", "")
        data = getattr(event, "data", {}) or {}
        if t == "run.started":
            row["status"] = "running"
            row["started_at"] = self._clock()
        elif t == "llm.return":
            usage = data.get("usage") or {}
            row["tokens"] += int(usage.get("total", 0) or 0)
        elif t == "run.finished":
            row["status"] = "done" if data.get("ok") else "failed"
            row["elapsed"] = float(data.get("elapsed_s", 0.0) or 0.0)

    def _rows_list(self) -> list[dict]:
        return [dict(self._rows[i]) for i in sorted(self._rows)]
