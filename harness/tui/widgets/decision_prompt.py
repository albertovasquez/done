"""DecisionPrompt — the inline 'grill-me' clarification UI: a question + numbered
options (title + dimmed rationale) + 'Type something' / 'Chat about this'
fallbacks. Display + selection only; the app acts on the Selected message. The
same model escalates to a modal when blocking (app's choice of mount target).
See spec §6 / components.md D."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.events import Key
from textual.message import Message
from textual.widgets import Static

from harness.tui.state import DecisionView

TYPE_SOMETHING = -1
CHAT_ABOUT_IT = -2


class DecisionPrompt(Vertical):
    can_focus = True

    class Selected(Message):
        def __init__(self, index: int) -> None:
            super().__init__()
            self.index = index

    def __init__(self, view: DecisionView) -> None:
        super().__init__(id="decision-prompt")
        self._view = view
        self._cursor = 0          # 0..n-1 options, then fallbacks
        self._n = len(view.options)

    def on_mount(self) -> None:
        self.focus()

    def option_lines(self) -> list[str]:
        """Return rendered option lines with a '› ' cursor prefix on the active row.

        Cursor rows map to flat positions where each option occupies 1 or 2 lines
        (title + optional rationale), then the two fallbacks each occupy 1 line.
        The cursor tracks *option index* (not line index), so we mark the title
        line for each option when its index matches self._cursor.
        """
        lines: list[str] = []
        for i, (title, rationale) in enumerate(self._view.options):
            prefix = "› " if i == self._cursor else "  "
            lines.append(f"[$accent]{prefix}{i + 1}. {title}[/]")
            if rationale:
                lines.append(f"     [$muted]{rationale}[/]")
        # fallback rows: cursor positions self._n and self._n+1
        ts_prefix = "› " if self._cursor == self._n else "  "
        ca_prefix = "› " if self._cursor == self._n + 1 else "  "
        lines.append(f"[$muted]{ts_prefix}{self._n + 1}. Type something[/]")
        lines.append(f"[$muted]{ca_prefix}{self._n + 2}. Chat about this[/]")
        return lines

    def _refresh_options(self) -> None:
        """Re-render the options Static so the cursor marker is visible.

        No-op when not mounted (e.g. during unit tests that call move() directly).
        """
        if not self.is_mounted:
            return
        options_widget = self.query_one("#decision-options", Static)
        options_widget.update("\n".join(self.option_lines()))

    def compose(self) -> ComposeResult:
        yield Static(f"[$foreground]{self._view.question}[/]", markup=True,
                     id="decision-question")
        yield Static("\n".join(self.option_lines()), markup=True, id="decision-options")

    def move(self, delta: int) -> None:
        total = self._n + 2          # options + 2 fallbacks
        self._cursor = max(0, min(total - 1, self._cursor + delta))
        self._refresh_options()

    def select(self) -> None:
        if self._cursor < self._n:
            self.post_message(self.Selected(self._cursor))
        elif self._cursor == self._n:
            self.post_message(self.Selected(TYPE_SOMETHING))
        else:
            self.post_message(self.Selected(CHAT_ABOUT_IT))

    def on_key(self, event: Key) -> None:
        if event.key == "up":
            self.move(-1)
            event.stop()
        elif event.key == "down":
            self.move(1)
            event.stop()
        elif event.key == "enter":
            self.select()
            event.stop()
        elif event.character and event.character.isdigit():
            n = int(event.character)
            total = self._n + 2
            if 1 <= n <= total:
                self._cursor = n - 1          # convert 1-indexed to 0-indexed
                self.select()
            # Out-of-range digits are intentionally swallowed (event.stop) while the prompt has focus.
            event.stop()
