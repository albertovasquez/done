"""PermissionModal — the prompt shown before the agent runs a command.

A thin wrapper over the shared SelectModal: a permission request is just
"pick one of {Allow once, Reject}" with the command as the title. Reusing
SelectModal gives it the same clean, keyboard-navigable look as /models — no
bespoke buttons. Dismisses with the chosen option_id (str) or None (esc); the
client's request_permission decides allow vs reject by the option's kind."""

from __future__ import annotations

from harness.tui.widgets.select_modal import SelectModal, SelectOption


class PermissionModal(SelectModal):
    def __init__(self, command: str, options) -> None:
        opts = [
            SelectOption(id=getattr(o, "option_id", "allow"),
                         label=getattr(o, "name", "Allow"))
            for o in (options or [])
        ]
        title = f"$ {command}" if command else "Permission requested"
        super().__init__(
            title=title,
            options=opts,
            current=None,
            footer="[$muted]↑↓ move · enter select · esc reject[/]",
            searchable=False,
        )

    def action_cancel(self) -> None:
        # esc on a permission prompt means reject, not just "close"
        self.dismiss(None)
