"""NewPersonaModal — name a new persona, create it, switch to it.

Lifecycle: input (type a name) → creating (spinner) → dismiss(id) on success, or
error (inline message, back to input). Enter on an empty name is ignored; esc
cancels (dismiss None). The app owns the actual create call (via the ext-method);
this widget only collects the name, shows progress/errors, and dismisses with the
id. Mirrors SelectModal's ModalScreen/dismiss pattern. Tokens only."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static

_SPINNER = ["◐", "◓", "◑", "◒"]            # mirrors ActivityStatus._CYCLE


class NewPersonaModal(ModalScreen):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, reduced_motion: bool = False) -> None:
        super().__init__()
        self._reduced_motion = reduced_motion
        self._i = 0
        self._timer = None

    def compose(self) -> ComposeResult:
        with Vertical(id="new-persona-box"):
            yield Static("[b]New persona[/b]   [$muted]esc[/]",
                         id="new-persona-title", markup=True)
            yield Input(placeholder="name (a-z 0-9 - _)", id="new-persona-name")
            yield Static("", id="new-persona-status", markup=True)

    def on_mount(self) -> None:
        self.query_one("#new-persona-name", Input).focus()

    @on(Input.Submitted, "#new-persona-name")
    def _submit(self) -> None:
        name = self.query_one("#new-persona-name", Input).value.strip()
        if not name:
            return                                  # empty -> ignore, stay open
        self.dismiss(name)

    def set_creating(self) -> None:
        """Switch to the creating state: disable input, start the spinner."""
        self.query_one("#new-persona-name", Input).disabled = True
        self._tick()
        if not self._reduced_motion:
            self._timer = self.set_interval(0.15, self._tick)

    def _tick(self) -> None:
        glyph = "◐" if self._reduced_motion else _SPINNER[self._i % len(_SPINNER)]
        self._i += 1
        self.query_one("#new-persona-status", Static).update(
            f"[$accent]{glyph}[/] creating…")

    def set_error(self, msg: str) -> None:
        """Show an error, re-enable input for a retry."""
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        inp = self.query_one("#new-persona-name", Input)
        inp.disabled = False
        inp.focus()
        self.query_one("#new-persona-status", Static).update(f"[$error]{msg}[/]")

    def action_cancel(self) -> None:
        self.dismiss(None)
