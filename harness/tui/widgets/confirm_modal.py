"""ConfirmModal — a small, generic yes/no confirmation dialog.

Pushed programmatically via `push_screen(ConfirmModal(...), callback=...)`.
Dismisses with True (user confirmed) or False (user declined / esc). The
side-effect lives in the caller's callback so this modal stays trivially
testable and carries no domain imports.

Mirrors CronInstallModal (cron_install_modal.py): ModalScreen, esc = cancel,
two buttons. Used first for the destructive cron-job delete (issue #178), where
a bare key press must no longer destroy a job without confirmation.
"""
from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ConfirmModal(ModalScreen):
    # y confirms, esc/n cancel — the classic [y/N] contract, keyboard-first.
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("n", "cancel", "Cancel"),
        Binding("y", "confirm", "Confirm"),
    ]

    def __init__(
        self,
        prompt: str,
        *,
        confirm_label: str = "Confirm",
        cancel_label: str = "Cancel",
        confirm_variant: str = "primary",
    ) -> None:
        super().__init__()
        self._prompt = prompt
        self._confirm_label = confirm_label
        self._cancel_label = cancel_label
        self._confirm_variant = confirm_variant

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Static(
                f"[b]{self._prompt}[/b]   [$muted]y = yes · esc = no[/]",
                id="confirm-title",
                markup=True,
            )
            yield Button(
                self._confirm_label, id="confirm-yes", variant=self._confirm_variant
            )
            yield Button(self._cancel_label, id="confirm-no")

    @on(Button.Pressed, "#confirm-yes")
    def _yes(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#confirm-no")
    def _no(self) -> None:
        self.dismiss(False)

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)
