"""MiniSweAgentRunner: the dev/CLI agent bridge that YIELDS events live.

Used only by the non-ACP developer entrypoint (run_traced.py). The production
ACP path drives TracingAgent directly (acp_agent.py) and does NOT use this — so
this is the CLI bridge, not a general "agent runtime" abstraction.

Bridges the Phase-0 TracingAgent (which PUSHES events via emitter.emit deep in
its loop) to a generator (PULL) using a background thread + a thread-safe queue.

Threading contract (see spec §2/§3):
  - Exactly ONE producer (the agent thread) calls QueueEmitter.emit; emit does a
    synchronous queue.put. The same thread puts the _DONE sentinel AFTER
    agent.run() returns/raises. One FIFO queue + single producer => run.finished
    (emitted in TracingAgent.run()'s finally) is always dequeued before _DONE.
  - The worker catches BaseException (TracingAgent catches BaseException too), so
    a worker-side KeyboardInterrupt still reaches the _DONE finally; otherwise the
    generator would block forever on queue.get().
  - Generator cleanup (gen.close()/break) is BLOCKING: it drains to _DONE and
    joins the worker. No cooperative cancellation in Phase 1. An ABANDONED
    (never-closed) generator may outlive iteration — callers must exhaust or close.
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from typing import Any, Callable, Iterator

from harness.events import Event, _EventSource
from harness.tracing_agent import TracingAgent


@dataclass
class RunResult:
    exit_status: str
    ok: bool
    n_calls: int
    total_cost: float
    submission: str = ""
    error: str | None = None


class QueueEmitter(_EventSource):
    """Emitter that enqueues events instead of writing files. Sole producer is
    the agent thread. Satisfies the contract TracingAgent uses: set_clock + emit
    (inherited/defined here); close() is a no-op."""

    def __init__(self, q: "queue.Queue[Any]", *, clock: Callable[[], float]):
        super().__init__(clock)
        self._q = q

    def emit(self, type: str, **data: Any) -> Event:
        event = self._next_event(type, **data)
        self._q.put(event)  # synchronous; unbounded queue never blocks
        return event

    def close(self) -> None:  # no-op; the runner owns lifecycle
        pass


class _Done:
    """Sentinel put on the queue by the worker after agent.run() finishes.
    Carries the returned result dict (success) or the captured exception."""
    __slots__ = ("result_dict", "exc")

    def __init__(self, result_dict: dict | None, exc: BaseException | None):
        self.result_dict = result_dict
        self.exc = exc


class MiniSweAgentRunner:
    """The dev/CLI bridge: run a TracingAgent on a worker thread and yield its
    events. (Not an abstract base — there is a single, concrete implementation;
    the production ACP path bridges the agent itself.)"""

    result: RunResult | None = None

    def __init__(self, model, env, *, agent_cfg: dict):
        self._model = model
        self._env = env
        self._agent_cfg = agent_cfg
        self.result = None

    def run(self, task: str, *, skill_block: str = "", persona_block: str = "",
            **kwargs) -> Iterator[Event]:
        q: "queue.Queue[Any]" = queue.Queue()
        emitter = QueueEmitter(q, clock=lambda: 0.0)
        agent = TracingAgent(self._model, self._env, emitter=emitter,
                             skill_block=skill_block, persona_block=persona_block,
                             **self._agent_cfg)

        def _worker():
            result_dict = None
            exc: BaseException | None = None
            try:
                result_dict = agent.run(task, **kwargs)
            except BaseException as e:  # noqa: BLE001 — fidelity: capture & relay, incl. KeyboardInterrupt
                exc = e
            finally:
                q.put(_Done(result_dict, exc))

        thread = threading.Thread(target=_worker, name="agentrunner-worker", daemon=True)
        thread.start()

        last_finished: Event | None = None
        done: _Done | None = None
        try:
            while True:
                item = q.get()
                if isinstance(item, _Done):
                    done = item
                    break
                if item.type == "run.finished":
                    last_finished = item
                yield item
        finally:
            # Early close/break: drain to _DONE and join so the worker doesn't leak.
            if done is None:
                while True:
                    item = q.get()
                    if isinstance(item, _Done):
                        done = item
                        break
                    if item.type == "run.finished":
                        last_finished = item
            thread.join()

        # Assemble result, then re-raise on the caller's thread if the worker failed.
        self.result = _build_result(done, last_finished)
        if done.exc is not None:
            raise done.exc


def _build_result(done: "_Done", finished: Event | None) -> RunResult:
    fd = finished.data if finished else {}
    rd = done.result_dict or {}
    return RunResult(
        exit_status=rd.get("exit_status") or fd.get("exit_status", ""),
        ok=bool(fd.get("ok", done.exc is None)),
        n_calls=int(fd.get("n_calls", 0)),
        total_cost=float(fd.get("total_cost", 0.0)),
        submission=rd.get("submission", ""),  # "" on error path (rd absent)
        error=fd.get("exception_str") if done.exc is not None else None,
    )
