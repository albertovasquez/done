"""WorkerCollector: folds per-worker TracingAgent events (run.started / llm.return
/ run.finished) into coalesced field_meta['workers'] progress payloads, emitted
behind an ~80ms time gate so N worker threads don't serialize on the ACP loop."""
from __future__ import annotations

import threading

from harness.tools.worker_collector import WorkerCollector


class FakeEvent:
    def __init__(self, type, **data):
        self.type = type
        self.data = data


def _clock():
    """Deterministic monotonic clock, advanced by tests via .t."""
    box = {"t": 0.0}
    return box, (lambda: box["t"])


def test_dispatched_emits_all_goals():
    emits = []
    c = WorkerCollector(["explore a", "explore b"], emit=emits.append, clock=lambda: 0.0)
    c.dispatched()
    assert len(emits) == 1
    payload = emits[0]["workers"]
    assert payload["action"] == "dispatched"
    assert [w["goal"] for w in payload["workers"]] == ["explore a", "explore b"]
    assert all(w["status"] == "pending" for w in payload["workers"])


def test_progress_coalesced_by_time_gate():
    box, clk = _clock()
    emits = []
    c = WorkerCollector(["a", "b"], emit=emits.append, clock=clk, min_interval=0.08)
    c.dispatched()
    emits.clear()
    # two events within the gate → at most one progress emit
    c.on_event(0, FakeEvent("run.started"))
    c.on_event(0, FakeEvent("llm.return", usage={"total": 1000}))
    assert len(emits) <= 1
    # advance past the gate → next event flushes
    box["t"] = 0.09
    c.on_event(1, FakeEvent("run.started"))
    assert len(emits) >= 1
    last = emits[-1]["workers"]
    assert last["action"] == "progress"


def test_tokens_accumulate_across_llm_returns():
    box, clk = _clock()
    c = WorkerCollector(["a"], emit=lambda _m: None, clock=clk, min_interval=0.0)
    c.dispatched()
    c.on_event(0, FakeEvent("run.started"))
    c.on_event(0, FakeEvent("llm.return", usage={"total": 1000}))
    c.on_event(0, FakeEvent("llm.return", usage={"total": 2500}))
    snap = c.snapshot()[0]
    assert snap["tokens"] == 3500
    assert snap["status"] == "running"


def test_finished_summary_counts_ok_and_failed():
    emits = []
    c = WorkerCollector(["a", "b"], emit=emits.append, clock=lambda: 0.0, min_interval=0.0)
    c.dispatched()
    c.on_event(0, FakeEvent("run.started"))
    c.on_event(1, FakeEvent("run.started"))
    c.on_event(0, FakeEvent("run.finished", ok=True, elapsed_s=10.0))
    c.on_event(1, FakeEvent("run.finished", ok=False, elapsed_s=5.0))
    c.on_event(0, FakeEvent("llm.return", usage={"total": 100}))  # ignore post-finish noise on tokens is fine
    c.finished()
    summary = emits[-1]["workers"]["summary"]
    assert summary["ok"] == 1 and summary["failed"] == 1


def test_thread_safe_under_concurrent_events():
    # Fire events from many threads; the lock must keep the token tally exact.
    c = WorkerCollector([f"w{i}" for i in range(4)], emit=lambda _m: None,
                        clock=lambda: 0.0, min_interval=0.0)
    c.dispatched()

    def worker(idx):
        for _ in range(100):
            c.on_event(idx, FakeEvent("llm.return", usage={"total": 10}))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    total = sum(s["tokens"] for s in c.snapshot())
    assert total == 4 * 100 * 10   # 4000, no lost updates
