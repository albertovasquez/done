"""QuickKeysPanel — the drawer's static keybinding legend (≡ QUICK KEYS).
A reference, not behavior; lists only keys that work today. Tokens only."""

from __future__ import annotations

from textual.widgets import Static

QUICK_KEYS: list[tuple[str, str]] = [
    ("tab", "switch panel"),
    ("↑↓", "navigate"),
    ("enter", "switch agent"),
    ("esc", "close"),
    ("/", "focus prompt"),
]


_PERSONA_HINT = "Each persona keeps its own conversation. ↑↓ to choose · enter to switch"


def quick_keys_markup() -> str:
    head = "[$muted][b]≡ QUICK KEYS[/b][/]"
    rows = "\n".join(f"[$muted on $surface] {k} [/]  [$muted]{label}[/]"
                     for k, label in QUICK_KEYS)
    hint = f"\n[$muted]{_PERSONA_HINT}[/]"
    return head + "\n" + rows + hint


class QuickKeysPanel(Static):
    def __init__(self) -> None:
        super().__init__(quick_keys_markup(), markup=True, id="quick-keys")
