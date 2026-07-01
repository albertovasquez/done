from textual.app import App, ComposeResult

from harness.jobs.view import JobRow
from harness.tui.theme import HARNESS_THEME
from harness.tui.widgets.jobs_table import JobsTable

MIXED = (
    JobRow("Index repo dependencies", "Scanning package graphs", "RUNNING", None, "", "00:18:42"),
    JobRow("Nightly sync", "Syncing upstream", "RUNNING", None, "", "00:07:11"),
    JobRow("Refresh embeddings", "Rebuilding index", "QUEUED", None, "", "—"),
    JobRow("Weekly report cron", "Weekly reports", "SCHEDULED", None, "in 2d 14h", "—"),
    JobRow("Customer import", "Normalize data", "COMPLETED", None, "", "00:04:03"),
)


class _Host(App):
    def __init__(self, rows):
        super().__init__()
        self._rows = rows

    def compose(self) -> ComposeResult:
        yield JobsTable(id="jt")

    def on_mount(self):
        self.register_theme(HARNESS_THEME)
        self.theme = "harness"
        self.query_one("#jt", JobsTable).set_rows(self._rows)


def test_jobs_table_mixed(snap_compare):
    assert snap_compare(_Host(MIXED), terminal_size=(120, 30))


def test_jobs_table_empty(snap_compare):
    assert snap_compare(_Host(()), terminal_size=(120, 30))


def test_jobs_table_scheduled_only(snap_compare):
    rows = tuple(r for r in MIXED if r.status == "SCHEDULED")
    assert snap_compare(_Host(rows), terminal_size=(120, 30))
