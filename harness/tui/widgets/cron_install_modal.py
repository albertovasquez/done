"""CronInstallModal — one-time opt-in prompt for OS autostart service.

Pushed programmatically from HarnessTui._show_cron_install_prompt during
on_mount (never key-bound, so no binding collisions). Dismisses with True
(user accepted) or False (user declined / esc). The install side-effect lives
in the app callback so this modal has no service import and stays trivially
testable.
"""
from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class CronInstallModal(ModalScreen):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="cron-install-box"):
            yield Static(
                "[b]Enable background scheduler?[/b]   [$muted]esc = no[/]",
                id="cron-install-title",
                markup=True,
            )
            yield Static(
                "Start DoneDone's scheduler at login so scheduled jobs fire "
                "even when no window is open.",
                id="cron-install-body",
            )
            yield Button("Yes, install", id="cron-install-yes", variant="primary")
            yield Button("No thanks", id="cron-install-no")

    @on(Button.Pressed, "#cron-install-yes")
    def _yes(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#cron-install-no")
    def _no(self) -> None:
        self.dismiss(False)

    def action_cancel(self) -> None:
        self.dismiss(False)
