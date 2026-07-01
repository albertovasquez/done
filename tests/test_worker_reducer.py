"""Worker-batch reducer: dispatched → seed rows; progress → merge by idx;
finished → set summary + clear live rows. Late progress after finished is a
no-op (finished is authoritative)."""
from __future__ import annotations

from harness.tui.state import (
    WorkerBatch, WorkerSummary, WorkerView,
    initial_snapshot, reduce,
)


def _snap():
    return initial_snapshot("default", "agent")


def test_dispatched_seeds_pending_rows():
    s = reduce(_snap(), WorkerBatch(action="dispatched", workers=(
        WorkerView(idx=0, goal="explore engine", status="pending"),
        WorkerView(idx=1, goal="explore tui", status="pending"),
    )))
    a = s.active
    assert len(a.workers) == 2
    assert [w.goal for w in a.workers] == ["explore engine", "explore tui"]
    assert all(w.status == "pending" for w in a.workers)
    assert a.worker_summary is None


def test_progress_merges_by_idx():
    s = reduce(_snap(), WorkerBatch(action="dispatched", workers=(
        WorkerView(idx=0, goal="a", status="pending"),
        WorkerView(idx=1, goal="b", status="pending"),
    )))
    s = reduce(s, WorkerBatch(action="progress", workers=(
        WorkerView(idx=0, goal="a", status="running", started_at=10.0, tokens=52900),
    )))
    a = s.active
    w0 = next(w for w in a.workers if w.idx == 0)
    w1 = next(w for w in a.workers if w.idx == 1)
    assert w0.status == "running" and w0.tokens == 52900 and w0.started_at == 10.0
    assert w1.status == "pending"   # untouched


def test_finished_sets_summary_and_clears_live_rows():
    s = reduce(_snap(), WorkerBatch(action="dispatched", workers=(
        WorkerView(idx=0, goal="a", status="running"),
    )))
    s = reduce(s, WorkerBatch(action="finished",
                              summary=WorkerSummary(ok=3, failed=1,
                                                    total_elapsed=192.0, total_tokens=198800)))
    a = s.active
    assert a.workers == ()          # live rows cleared
    assert a.worker_summary == WorkerSummary(ok=3, failed=1,
                                             total_elapsed=192.0, total_tokens=198800)


def test_late_progress_after_finished_is_noop():
    s = reduce(_snap(), WorkerBatch(action="dispatched", workers=(
        WorkerView(idx=0, goal="a", status="running"),
    )))
    s = reduce(s, WorkerBatch(action="finished",
                              summary=WorkerSummary(ok=1, failed=0, total_elapsed=5.0, total_tokens=100)))
    before = s.active
    s = reduce(s, WorkerBatch(action="progress", workers=(
        WorkerView(idx=0, goal="a", status="running", tokens=999),
    )))
    # No live workers to merge into → snapshot unchanged (summary preserved).
    assert s.active.workers == ()
    assert s.active.worker_summary == before.worker_summary
