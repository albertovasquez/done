"""AgentRail — the persona list (the C2 drawer's right rail).

Dumb/reactive: given a tuple of PersonaRow, renders one selectable line per
persona (active marker + name) and posts PersonaSelected(id) when a row is
chosen. No business logic — the app composes the rows (roster.persona_rows) and
handles the selection (switch by re-exec). Mirrors select_modal's ListView usage."""

from __future__ import annotations

from textual import on
from textual.message import Message
from textual.widgets import Label, ListItem, ListView

from harness.tui.roster import PersonaRow

ACTIVE_GLYPH = "●"
IDLE_GLYPH = "○"


class PersonaSelected(Message):
    def __init__(self, id: str) -> None:
        self.id = id
        super().__init__()


def _row_label(r: PersonaRow) -> str:
    return f"{ACTIVE_GLYPH if r.active else IDLE_GLYPH} {r.name}"


class AgentRail(ListView):
    """A selectable persona list. Rows are set via set_rows(); choosing a row
    posts PersonaSelected(id)."""

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

    @on(ListView.Selected)
    def _on_selected(self, event: ListView.Selected) -> None:
        event.stop()
        pid = getattr(event.item, "data", None)
        if pid:
            self.post_message(PersonaSelected(pid))
