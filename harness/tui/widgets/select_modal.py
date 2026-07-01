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

from rich.markup import escape as _escape_markup
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
    group: str | None = None      # provider header this row belongs under
    disabled: bool = False        # non-selectable (header, or login_needed/stale_config)


class SelectModal(ModalScreen):
    # The search Input holds focus (so typing filters), but a single-line Input
    # doesn't navigate the list — ↑↓ would otherwise do nothing and you'd have to
    # click. Bind the nav keys on the screen with priority so they reach the list
    # regardless of which child is focused, then drive the ListView's own cursor
    # actions (identical behavior to focusing the list directly).
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("up", "nav_up", "Up", show=False, priority=True),
        Binding("down", "nav_down", "Down", show=False, priority=True),
        Binding("pageup", "nav_pageup", "Page up", show=False, priority=True),
        Binding("pagedown", "nav_pagedown", "Page down", show=False, priority=True),
    ]

    def __init__(self, title: str, options: list[SelectOption],
                 current: str | None = None, footer: str = "",
                 searchable: bool = True, body: str = "") -> None:
        super().__init__()
        self._title = title
        self._options = options
        self._current = current
        self._footer = footer
        self._searchable = searchable
        self._body = body

    def compose(self) -> ComposeResult:
        with Vertical(id="select-box"):
            with Vertical(id="select-header"):
                yield Static(f"[b]{self._title}[/b]   [$muted]esc[/]",
                             id="select-title", markup=True)
                if self._body:
                    yield Static(self._body, id="select-body",
                                 markup=False, classes="select-body-code")
            if self._searchable:
                yield Input(placeholder="Search", id="select-search")
            yield ListView(id="select-list")
            if self._footer:
                yield Static(self._footer, id="select-footer", markup=True)

    def on_mount(self) -> None:
        self._populate(self._options)
        # focus the search box if present, else the list (so ↑↓/enter work)
        if self._searchable:
            self.query_one("#select-search", Input).focus()
        else:
            self.query_one("#select-list", ListView).focus()

    def _row_markup(self, opt: SelectOption) -> str:
        marker = "●" if opt.id == self._current else " "
        if opt.disabled:
            return f"[$muted]{marker} {_escape_markup(opt.label)}[/]"
        return f"{marker} {opt.label}"

    def _populate(self, options: list[SelectOption]) -> None:
        lv = self.query_one("#select-list", ListView)
        lv.clear()
        for opt in options:
            item = ListItem(Label(self._row_markup(opt), markup=opt.disabled))
            item.data = opt.id            # carry the id for selection
            if opt.disabled:
                item.disabled = True      # ListView skips disabled rows on highlight/enter
            lv.append(item)
        # highlight the current value if present, else the first selectable row
        idx = next((i for i, o in enumerate(options)
                    if o.id == self._current and not o.disabled), None)
        if idx is None:
            idx = next((i for i, o in enumerate(options) if not o.disabled), 0)
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

    def action_nav_up(self) -> None:
        self.query_one("#select-list", ListView).action_cursor_up()

    def action_nav_down(self) -> None:
        self.query_one("#select-list", ListView).action_cursor_down()

    def action_nav_pageup(self) -> None:
        self.query_one("#select-list", ListView).action_page_up()

    def action_nav_pagedown(self) -> None:
        self.query_one("#select-list", ListView).action_page_down()

    def action_cancel(self) -> None:
        self.dismiss(None)
