"""DecisionPrompt — the inline 'grill-me' clarification UI: a question + numbered
options (title + dimmed rationale) + 'Type something' / 'Chat about this'
fallbacks. Display + selection only; the app acts on the Selected message. The
same model escalates to a modal when blocking (app's choice of mount target).
See spec §6 / components.md D."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Static

from harness.tui.state import DecisionView

TYPE_SOMETHING = -1
CHAT_ABOUT_IT = -2


class DecisionPrompt(Vertical):
    class Selected(Message):
        def __init__(self, index: int) -> None:
            super().__init__()
            self.index = index

    def __init__(self, view: DecisionView) -> None:
        super().__init__(id="decision-prompt")
        self._view = view
        self._cursor = 0          # 0..n-1 options, then fallbacks
        self._n = len(view.options)

    def option_lines(self) -> list[str]:
        lines: list[str] = []
        for i, (title, rationale) in enumerate(self._view.options, start=1):
            lines.append(f"[$accent]{i}. {title}[/]")
            if rationale:
                lines.append(f"     [$muted]{rationale}[/]")
        lines.append(f"[$muted]{self._n + 1}. Type something[/]")
        lines.append(f"[$muted]{self._n + 2}. Chat about this[/]")
        return lines

    def compose(self) -> ComposeResult:
        yield Static(f"[$foreground]{self._view.question}[/]", markup=True,
                     id="decision-question")
        yield Static("\n".join(self.option_lines()), markup=True, id="decision-options")

    def move(self, delta: int) -> None:
        total = self._n + 2          # options + 2 fallbacks
        self._cursor = max(0, min(total - 1, self._cursor + delta))

    def select(self) -> None:
        if self._cursor < self._n:
            self.post_message(self.Selected(self._cursor))
        elif self._cursor == self._n:
            self.post_message(self.Selected(TYPE_SOMETHING))
        else:
            self.post_message(self.Selected(CHAT_ABOUT_IT))
