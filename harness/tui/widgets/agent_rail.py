"""AgentRail — the persona list (the C2 drawer's right rail).

Dumb/reactive: given a tuple of PersonaRow, renders one selectable line per
persona (active marker + name) and posts PersonaSelected(id) when a row is
chosen. No business logic — the app composes the rows (roster.persona_rows). The
rail is VIEW-ONLY in C2b (selection does not switch personas; in-process switching
is deferred to C2c). Mirrors select_modal's ListView usage."""

from __future__ import annotations

from textual import on
from textual.binding import Binding
from textual.message import Message
from textual.widgets import ListItem, ListView, Static

from harness.tui.roster import PersonaRow
from harness.tui.state import AgentState
from harness.tui.tokens import GLYPH, STATUS_LABEL
from harness.tui.widgets.status_chip import _STATE_GLYPH, state_color_token


class PersonaSelected(Message):
    def __init__(self, id: str) -> None:
        self.id = id
        super().__init__()


class NewPersonaRequested(Message):
    """Posted when the user presses `n` in the rail to create a new persona."""


# AgentState → the STATUS_LABEL/colour vocabulary key (display only).
_STATUS_KEY = {
    AgentState.IDLE: "idle",
    AgentState.THINKING: "running",
    AgentState.RESPONDING: "running",
    AgentState.RUNNING_TOOL: "running",
    AgentState.AWAITING_PERMISSION: "scheduled",
    AgentState.AWAITING_DECISION: "scheduled",
    AgentState.SCHEDULED: "scheduled",
    AgentState.DONE: "idle",
    AgentState.FAILED: "idle",
}


def _status_label(state: AgentState) -> str:
    return STATUS_LABEL.get(_STATUS_KEY.get(state, "idle"), "IDLE")


def card_markup(row: PersonaRow, subline: str) -> str:
    """Two-line card markup: name (left) + status label/dot (right), then a muted
    sub-line. Active name is accent-bold; idle is plain foreground. Tokens only;
    no icon tile."""
    token = state_color_token(row.status)
    dot = GLYPH[_STATE_GLYPH.get(row.status, "idle")]
    name = f"[$accent][b]{row.name}[/b][/]" if row.active else f"[$foreground]{row.name}[/]"
    status = f"[${token}]{_status_label(row.status)} {dot}[/]"
    return f"{name}    {status}\n[$muted]{subline}[/]"


class AgentRail(ListView):
    """A selectable persona list. Rows are set via set_rows(); choosing a row
    posts PersonaSelected(id)."""

    BINDINGS = [Binding("n", "new_persona", "New persona")]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._rows: tuple[PersonaRow, ...] = ()

    def set_rows(self, rows: tuple[PersonaRow, ...]) -> None:
        self._rows = rows
        self.clear()
        for r in rows:
            item = ListItem(Label(_row_label(r), markup=False))
            item.data = r.id                 # carry the id for selection (select_modal pattern)
            self.append(item)

    def _rail_text(self) -> str:
        """The rendered lines as one string (test helper)."""
        return "\n".join(_row_label(r) for r in self._rows)

    def select_id(self, persona_id: str) -> None:
        """Programmatic selection entrypoint (used by tests + enter/click)."""
        self.post_message(PersonaSelected(persona_id))

    def action_new_persona(self) -> None:
        self.post_message(NewPersonaRequested())

    @on(ListView.Selected)
    def _on_selected(self, event: ListView.Selected) -> None:
        event.stop()
        pid = getattr(event.item, "data", None)
        if pid:
            self.post_message(PersonaSelected(pid))
