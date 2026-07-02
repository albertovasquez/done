"""ProxyRefreshModal — consent prompt when the proxy config has drifted.

Pushed programmatically from HarnessTui._show_proxy_refresh_prompt during
on_mount (never key-bound). Dismisses True (regenerate + restart now) or False
(not now — fall back to the #292 log line). The refresh side-effect lives in
the app callback so this modal has no lifecycle import and stays trivially
testable. Restart is user-consented by construction — no code path restarts
the machine-global proxy unattended (#292 hard constraint).
"""
from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ProxyRefreshModal(ModalScreen):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="proxy-refresh-box"):
            yield Static(
                "[b]Proxy config is stale[/b]   [$muted]esc = not now[/]",
                id="proxy-refresh-title",
                markup=True,
            )
            yield Static(
                "NEURALWATT_API_KEY (or the served model list) changed since the "
                "last install. Regenerate the proxy config and restart the proxy "
                "now? In-flight requests from other sessions may be dropped.",
                id="proxy-refresh-body",
            )
            yield Button("Restart now", id="proxy-refresh-yes", variant="primary")
            yield Button("Not now", id="proxy-refresh-no")

    @on(Button.Pressed, "#proxy-refresh-yes")
    def _yes(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#proxy-refresh-no")
    def _no(self) -> None:
        self.dismiss(False)

    def action_cancel(self) -> None:
        self.dismiss(False)
