"""worker_batch(field_meta): decode field_meta['harness']['workers'] (the seam
the Collector emits on, nested under 'harness' by with_meta) into a WorkerBatch
state event, or None when absent/malformed."""
from __future__ import annotations

from harness.tui.render import worker_batch
from harness.tui.state import WorkerBatch, WorkerSummary, WorkerView


def test_none_when_no_workers_key():
    assert worker_batch(None) is None
    assert worker_batch({}) is None
    assert worker_batch({"harness": {}}) is None
    assert worker_batch({"workers": {"action": "dispatched"}}) is None  # not under harness


def test_decode_dispatched():
    fm = {"harness": {"workers": {"action": "dispatched", "workers": [
        {"idx": 0, "goal": "a"}, {"idx": 1, "goal": "b"}]}}}
    ev = worker_batch(fm)
    assert isinstance(ev, WorkerBatch)
    assert ev.action == "dispatched"
    assert ev.workers == (
        WorkerView(idx=0, goal="a", status="pending"),
        WorkerView(idx=1, goal="b", status="pending"),
    )


def test_decode_progress_carries_metrics():
    fm = {"harness": {"workers": {"action": "progress", "workers": [
        {"idx": 0, "goal": "a", "status": "running", "started_at": 3.0, "tokens": 52900}]}}}
    ev = worker_batch(fm)
    assert ev.action == "progress"
    (w,) = ev.workers
    assert w.status == "running" and w.started_at == 3.0 and w.tokens == 52900


def test_decode_finished_summary():
    fm = {"harness": {"workers": {"action": "finished",
                                  "summary": {"ok": 3, "failed": 1,
                                              "total_elapsed": 192.0, "total_tokens": 198800}}}}
    ev = worker_batch(fm)
    assert ev.action == "finished"
    assert ev.summary == WorkerSummary(ok=3, failed=1, total_elapsed=192.0, total_tokens=198800)


def test_malformed_action_returns_none():
    assert worker_batch({"harness": {"workers": {"workers": []}}}) is None   # no action
    assert worker_batch({"harness": {"workers": "nonsense"}}) is None
