"""SlashMenu — the inline command list shown above the compose box when the input
starts with '/'. Filters as you type; ↑/↓ move; the app runs the highlighted
command on Enter. Rendered as 'name' (accent) + 'description' (muted) rows, the
highlighted row inverted — matching the opencode slash menu.

The menu is display + selection only; the app owns command dispatch (it reads
`highlighted_command()` and runs the registry handler)."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Label, ListItem, ListView

from harness.tui.commands import Command, filter_commands


class SlashMenu(Vertical):
    """A filtered command list. Mounted/removed by the app as '/' is typed/cleared."""

    def __init__(self, commands: list[Command]) -> None:
        super().__init__(id="slash-menu")
        self._commands = commands
        self._filtered: list[Command] = list(commands)

    def compose(self) -> ComposeResult:
        yield ListView(id="slash-list")

    def on_mount(self) -> None:
        self._render_rows(self._filtered)

    def _row_markup(self, c: Command) -> str:
        # name in accent, padded; description muted.
        return f"[$accent]/{c.name}[/]   [$muted]{c.description}[/]"

    def _render_rows(self, commands: list[Command]) -> None:
        lv = self.query_one("#slash-list", ListView)
        lv.clear()
        for c in commands:
            item = ListItem(Label(self._row_markup(c), markup=True))
            item.data = c.name
            lv.append(item)
        if commands:
            lv.index = 0

    def update_query(self, query: str) -> None:
        """query is the text after the leading '/'."""
        self._filtered = filter_commands(self._commands, query)
        self._render_rows(self._filtered)

    def move(self, delta: int) -> None:
        lv = self.query_one("#slash-list", ListView)
        if not self._filtered:
            return
        cur = lv.index or 0
        lv.index = max(0, min(len(self._filtered) - 1, cur + delta))

    def highlighted_command(self) -> Command | None:
        lv = self.query_one("#slash-list", ListView)
        if not self._filtered or lv.index is None:
            return None
        return self._filtered[lv.index]
