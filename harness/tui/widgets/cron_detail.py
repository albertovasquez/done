"""CronDetail — per-job run-history chart widget.

Displays a PlotextPlot bar chart of run durations (in seconds) over time,
one bar per recorded run in the job's runs/<id>.jsonl file.

Public API:
    read_run_series(job_id) -> list[tuple[float, float, str]]
        Pure reader — unit-tested. Returns (started_at, duration, status)
        tuples in file order; [] if the file is absent; skips malformed lines.

    CronDetail(job_id)
        Textual widget. Call refresh_chart(job_id) to reload data.

Pattern: mirrors CronDashboard (cron_dashboard.py) — Widget subclass, compose()
yields child widgets, on_mount() seeds data, refresh method reloads on demand.
"""

from __future__ import annotations

import json
import logging

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Label
from textual_plotext import PlotextPlot

from harness.jobs import paths as jp

logger = logging.getLogger(__name__)


# ── Pure reader (unit-tested) ─────────────────────────────────────────────────


def read_run_series(job_id: str) -> list[tuple[float, float, str]]:
    """Read the run log for *job_id* and return (started_at, duration, status) tuples.

    Args:
        job_id: The job id whose runs/<id>.jsonl file to read.

    Returns:
        A list of (started_at, duration, status) tuples in file order.
        Returns [] if the file does not exist.
        Silently skips any line that is not valid JSON.
    """
    path = jp.run_log(job_id)
    if not path.exists():
        return []
    result: list[tuple[float, float, str]] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            started_at = float(obj["started_at"])
            duration = float(obj["duration"])
            status = str(obj["status"])
        except (KeyError, ValueError, TypeError):
            continue
        result.append((started_at, duration, status))
    return result


# ── Widget ────────────────────────────────────────────────────────────────────


class CronDetail(Widget):
    """Per-job detail panel showing a run-duration bar chart.

    Compose a PlotextPlot child; on_mount seeds the chart via _draw().
    Call refresh_chart(job_id) to reload from a new or updated job.

    App-level wiring:
      - Mount in a drawer, tab, or sidebar — e.g. beside CronDashboard.
      - Call refresh_chart(job_id) whenever the user selects a different job.
      - No auto-polling: the host app should call refresh_chart() after
        any relevant state change (job run, job switch, timer).
    """

    DEFAULT_CSS = """
    CronDetail {
        width: 1fr;
        height: 1fr;
    }
    CronDetail > Label {
        text-align: center;
        width: 1fr;
    }
    """

    def __init__(self, job_id: str, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._job_id = job_id

    def compose(self) -> ComposeResult:
        yield Label(f"Run history: {self._job_id}", id="cron-detail-title")
        yield PlotextPlot(id="cron-detail-plot")

    def on_mount(self) -> None:
        """Seed the chart after mount."""
        self._draw(self._job_id)

    def refresh_chart(self, job_id: str) -> None:
        """Reload the chart for *job_id* (call from a timer or selection handler)."""
        self._job_id = job_id
        title = self.query_one("#cron-detail-title", Label)
        title.update(f"Run history: {job_id}")
        self._draw(job_id)

    def _draw(self, job_id: str) -> None:
        """Read run series and draw the bar chart."""
        plot_widget = self.query_one(PlotextPlot)
        plt = plot_widget.plt
        plt.clear_data()
        plt.clear_figure()

        series = read_run_series(job_id)
        if not series:
            plt.title("No runs recorded")
            plt.plotsize(plot_widget.size.width or 40, plot_widget.size.height or 10)
            plot_widget.refresh()
            return

        xs = list(range(1, len(series) + 1))
        ys = [duration for _, duration, _ in series]
        statuses = [status for _, _, status in series]

        plt.title(f"Run durations — {job_id}")
        plt.xlabel("Run #")
        plt.ylabel("Duration (s)")

        # Colour bars by status: ok=green, error=red, anything else=default
        colors = []
        for s in statuses:
            if s == "ok":
                colors.append("green")
            elif s == "error":
                colors.append("red")
            else:
                colors.append("blue")

        plt.bar(xs, ys, color=colors)
        plot_widget.refresh()
