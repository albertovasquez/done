"""NewPersonaModal — name a new persona, create it, switch to it.

Lifecycle: input (type a name) → creating (spinner) → dismiss(resp) on success, or
error (inline message, stay open for retry). Enter on an empty name is ignored; esc
cancels (dismiss None). When on_create is supplied the modal owns the create call:
it shows the spinner while the worker runs, dismisses with the resp dict on success,
or calls set_error and stays open on failure. When on_create is None (widget-only
tests) _submit falls back to dismiss(name) — the old behaviour. Tokens only."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static

_SPINNER = ["◐", "◓", "◑", "◒"]            # mirrors ActivityStatus._CYCLE


class NewPersonaModal(ModalScreen):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(
        self,
        on_create: "Callable[[str], Awaitable[dict]] | None" = None,
        reduced_motion: bool = False,
    ) -> None:
        super().__init__()
        self._on_create = on_create
        self._reduced_motion = reduced_motion
        self._i = 0
        self._timer: "object | None" = None

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
        if self._on_create is None:
            # Fallback for widget-only tests: dismiss with the name string directly.
            self.dismiss(name)
            return
        self.set_creating()
        self.run_worker(self._do_create(name), thread=False)

    async def _do_create(self, name: str) -> None:
        """Worker: call the app's create callback; dismiss on success, set_error on failure."""
        try:
            resp = await self._on_create(name)
        except Exception as exc:
            self.set_error(str(exc))
            return
        if resp and resp.get("ok"):
            self.dismiss(resp)
        else:
            self.set_error((resp or {}).get("error", "create failed"))

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
