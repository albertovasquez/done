"""SelectModal — a reusable search-and-select overlay (pick one of many).

Generic and content-agnostic: a title bar with an `esc` hint, a live-filter
search box, a scrollable option list with the current value marked by ●, and a
footer hint slot. Used by /models now; reusable for /agents, providers, etc.

Contract:
    modal = SelectModal(title="Select model", options=[SelectOption(id, label), ...],
                        current="claude-opus-4-8", footer="↑↓ move · enter select")
    app.push_screen(modal, callback)   # callback(value: str | None)

Dismisses with the chosen option id, or None on esc/cancel."""

from __future__ import annotations

from dataclasses import dataclass

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, ListItem, ListView, Static


@dataclass(frozen=True)
class SelectOption:
    id: str
    label: str


class SelectModal(ModalScreen):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, title: str, options: list[SelectOption],
                 current: str | None = None, footer: str = "") -> None:
        super().__init__()
        self._title = title
        self._options = options
        self._current = current
        self._footer = footer

    def compose(self) -> ComposeResult:
        with Vertical(id="select-box"):
            with Vertical(id="select-header"):
                yield Static(f"[b]{self._title}[/b]   [$muted]esc[/]",
                             id="select-title", markup=True)
            yield Input(placeholder="Search", id="select-search")
            yield ListView(id="select-list")
            if self._footer:
                yield Static(self._footer, id="select-footer", markup=True)

    def on_mount(self) -> None:
        self._populate(self._options)
        self.query_one("#select-search", Input).focus()

    def _row_markup(self, opt: SelectOption) -> str:
        marker = "●" if opt.id == self._current else " "
        return f"{marker} {opt.label}"

    def _populate(self, options: list[SelectOption]) -> None:
        lv = self.query_one("#select-list", ListView)
        lv.clear()
        for opt in options:
            item = ListItem(Label(self._row_markup(opt), markup=False))
            item.data = opt.id            # carry the id for selection
            lv.append(item)
        # highlight the current value if present, else the first row
        idx = next((i for i, o in enumerate(options) if o.id == self._current), 0)
        if options:
            lv.index = idx

    @on(Input.Changed, "#select-search")
    def _filter(self, event: Input.Changed) -> None:
        q = event.value.lower().strip()
        filtered = [o for o in self._options if q in o.label.lower()] if q else self._options
        self._populate(filtered)

    @on(Input.Submitted, "#select-search")
    def _submit_search(self) -> None:
        # Enter in the search box selects the highlighted row.
        lv = self.query_one("#select-list", ListView)
        if lv.highlighted_child is not None:
            self.dismiss(getattr(lv.highlighted_child, "data", None))

    @on(ListView.Selected, "#select-list")
    def _selected(self, event: ListView.Selected) -> None:
        self.dismiss(getattr(event.item, "data", None))

    def action_cancel(self) -> None:
        self.dismiss(None)
