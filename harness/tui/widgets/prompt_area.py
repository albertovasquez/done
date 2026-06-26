"""PromptArea — the compose box where the user types a prompt.

A multi-line TextArea that auto-grows with its content (height: auto, capped at
max-height: 3 in app.tcss, then it scrolls). It replaces the old single-line
Input so a wrapped or multi-line prompt is visible while typing.

Key model (Shift+Enter needs a terminal that distinguishes it from Enter):
  Enter        submit the prompt  -> posts PromptArea.Submitted
  Shift+Enter  insert a newline   -> matched across terminal encodings, see
                                     _NEWLINE_KEYS

A `.value` alias mirrors Input's API so the app's existing call sites
(`.value = ""`, reads, slash-menu region math) keep working unchanged. The app
listens for PromptArea.Submitted (Enter) and TextArea.Changed (every edit)."""

from __future__ import annotations

from textual import events
from textual.message import Message
from textual.widgets import TextArea


class PromptArea(TextArea):
    """Auto-growing compose box. Enter submits; Shift+Enter inserts a newline."""

    # A *modified* Enter (Shift/Alt/Ctrl/…+Enter) must insert a newline; only a
    # bare Enter submits. Terminals encode modified Enter in several ways and
    # Textual surfaces each under a different event.key:
    #   Kitty CSI-u form        -> "shift+enter", "alt+enter", "ctrl+enter", …
    #   modifyOtherKeys form    -> "shift+\r", "ctrl+\r", "ctrl+shift+\r", …
    #   (cmux/libghostty + Ghostty use the modifyOtherKeys form.)
    # Rather than enumerate every combination, match structurally: a key with a
    # modifier prefix ("<mods>+") whose final segment is enter / \r / \n.
    _ENTER_SEGMENTS = frozenset({"enter", "return", "\r", "\n"})

    @classmethod
    def _is_modified_enter(cls, key: str) -> bool:
        if "+" not in key:                 # bare key (e.g. "enter") -> not modified
            return False
        return key.rsplit("+", 1)[-1] in cls._ENTER_SEGMENTS

    class Submitted(Message):
        """Posted when the user presses Enter. Carries the current text."""

        def __init__(self, prompt_area: "PromptArea", text: str) -> None:
            super().__init__()
            self.prompt_area = prompt_area
            self.text = text

    def __init__(self, *, placeholder: str = "", id: str | None = None) -> None:
        # soft_wrap: long lines wrap (box grows by height, not horizontally).
        # show_line_numbers off + compact: read as a plain chat box, not an editor.
        # tab_behavior="focus": keep Tab for moving focus (the 'tab agents' hint),
        # not indentation — and it makes Shift+Tab work too.
        super().__init__(
            "",
            soft_wrap=True,
            show_line_numbers=False,
            tab_behavior="focus",
            compact=True,
            placeholder=placeholder,
            id=id,
        )

    # ---- Input-compatible .value shim (TextArea's native field is .text) ----

    @property
    def value(self) -> str:
        return self.text

    @value.setter
    def value(self, new: str) -> None:
        self.text = new

    # ---- key handling ----

    async def _on_key(self, event: events.Key) -> None:
        # While the slash menu is open, let up/down/escape bubble to the app's
        # on_key (it drives menu selection). TextArea would otherwise consume
        # them as cursor moves. Don't stop the event — just don't handle it here.
        if getattr(self.app, "_slash", None) is not None and event.key in (
            "up", "down", "escape",
        ):
            return

        # Modified Enter (Shift/Alt/Ctrl+Enter) inserts a newline. TextArea's own
        # _on_key only maps bare "enter" to "\n" and ignores modified variants, so
        # we handle them. On legacy terminals that can't distinguish the keys, the
        # press arrives as bare "enter" and falls through to the submit branch.
        if self._is_modified_enter(event.key):
            event.stop()
            event.prevent_default()
            self.insert("\n")
            return

        # Enter submits.
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self.post_message(self.Submitted(self, self.text))
            return

        await super()._on_key(event)
