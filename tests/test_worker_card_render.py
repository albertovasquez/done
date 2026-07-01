"""Worker card rendering: pure helpers that turn snapshot worker state into the
markup shown in the ActivityRegion (live rows) and the transcript (summary line).
No Textual mount needed."""
from __future__ import annotations

from harness.tui.state import AgentSnapshot, AgentState, WorkerSummary, WorkerView
from harness.tui.widgets.activity_region import ActivityRegion, worker_summary_line


def _snap(workers=(), summary=None):
    return AgentSnapshot(id="default", name="agent", state=AgentState.RUNNING_TOOL,
                         workers=workers, worker_summary=summary)


def test_show_workers_true_when_present():
    snap = _snap(workers=(WorkerView(idx=0, goal="a"),))
    assert ActivityRegion.show_workers(snap) is True


def test_show_workers_false_when_empty():
    assert ActivityRegion.show_workers(_snap()) is False


def test_worker_lines_header_counts_and_rows():
    snap = _snap(workers=(
        WorkerView(idx=0, goal="explore engine", status="running", tokens=52900),
        WorkerView(idx=1, goal="explore tui", status="done", elapsed=74.0, tokens=54800),
    ))
    lines = ActivityRegion.worker_lines(snap)
    # header + one row per worker
    assert len(lines) == 3
    assert "2 workers" in lines[0]
    assert "explore engine" in lines[1]
    assert "explore tui" in lines[2]
    # done worker shows its elapsed + tokens; running worker shows a working glyph
    assert "1m 14s" in lines[2]
    assert "54.8k" in lines[2]


def test_worker_summary_line_ok_and_failed():
    line = worker_summary_line(WorkerSummary(ok=3, failed=1, total_elapsed=192.0,
                                             total_tokens=198800))
    assert "3" in line and "1" in line          # ok + failed counts
    assert "3m 12s" in line                       # total elapsed
    assert "198.8k" in line                       # total tokens


def test_worker_summary_line_all_ok_omits_failed_noise():
    line = worker_summary_line(WorkerSummary(ok=4, failed=0, total_elapsed=10.0,
                                             total_tokens=1000))
    assert "4 workers" in line
    assert "failed" not in line.lower()
