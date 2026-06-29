"""DecisionModal — the 'grill-me' clarification UI as a focused modal overlay.

A centered, bordered box (shares SelectModal's #select-box look) showing the
question + numbered options (title + dimmed rationale), with the first option
marked "(recommended)", plus the two 'Type something' / 'Chat about this'
fallbacks. Replaces the old inline DecisionPrompt: a blocking clarification is
the same interaction shape as the permission prompt, so it gets the same modal
treatment (dims the conversation, owns focus).

Contract:
    app.push_screen(DecisionModal(view), callback)   # callback(value: int | None)

Dismisses with the chosen option index (0..n-1), TYPE_SOMETHING / CHAT_ABOUT_IT
for the fallbacks, or None on esc/cancel. The app maps that outcome to the same
selection logic as before (submit option title / focus / prefill composer)."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.events import Key
from textual.screen import ModalScreen
from textual.widgets import Static

from harness.tui.state import DecisionView

TYPE_SOMETHING = -1
CHAT_ABOUT_IT = -2


class DecisionModal(ModalScreen):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, view: DecisionView) -> None:
        super().__init__()
        self._view = view
        self._cursor = 0          # 0..n-1 options, then fallbacks
        self._n = len(view.options)

    def option_lines(self) -> list[str]:
        """Return rendered option lines with a '› ' cursor prefix on the active row.

        Each option occupies 1 or 2 lines (title + optional rationale), then the
        two fallbacks each occupy 1 line. The first option's title carries a
        '(recommended)' marker (the router emits options best-first; there is no
        explicit recommended flag yet — see GH #117). The cursor tracks *option
        index* (not line index), so we mark the title line whose index matches
        self._cursor.
        """
        lines: list[str] = []
        for i, (title, rationale) in enumerate(self._view.options):
            prefix = "› " if i == self._cursor else "  "
            rec = "  [$muted](recommended)[/]" if i == 0 else ""
            lines.append(f"[$accent]{prefix}{i + 1}. {title}[/]{rec}")
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
        self.query_one("#decision-options", Static).update("\n".join(self.option_lines()))

    def compose(self) -> ComposeResult:
        with Vertical(id="decision-box"):
            yield Static(f"[b]{self._view.question}[/b]   [$muted]esc[/]",
                         id="decision-title", markup=True)
            yield Static("\n".join(self.option_lines()), markup=True,
                         id="decision-options")
            yield Static("[$muted]↑↓ move · enter select · esc cancel[/]",
                         id="decision-footer", markup=True)

    def on_mount(self) -> None:
        self.focus()

    def move(self, delta: int) -> None:
        total = self._n + 2          # options + 2 fallbacks
        self._cursor = max(0, min(total - 1, self._cursor + delta))
        self._refresh_options()

    def select(self) -> None:
        if self._cursor < self._n:
            self.dismiss(self._cursor)
        elif self._cursor == self._n:
            self.dismiss(TYPE_SOMETHING)
        else:
            self.dismiss(CHAT_ABOUT_IT)

    def action_cancel(self) -> None:
        self.dismiss(None)

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
            # Out-of-range digits are intentionally swallowed while the modal has focus.
            event.stop()
