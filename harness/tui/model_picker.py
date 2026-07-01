"""Pure builder: reconciled ModelStatus list -> grouped, status-tagged
SelectOption rows for the model picker. No TUI, no I/O — unit-tested directly."""
from __future__ import annotations

from harness.tui.widgets.select_modal import SelectOption

_STATUS_TAG = {"available": "", "login_needed": "  — login needed",
               "stale_config": "  — refresh proxy config"}


def build_picker_rows(statuses) -> list[SelectOption]:
    rows: list[SelectOption] = []
    by_provider: dict[str, list] = {}
    for s in statuses:
        by_provider.setdefault(s.provider, []).append(s)
    for provider in sorted(by_provider):
        rows.append(SelectOption(id="", label=f"— {provider} —", group=provider, disabled=True))
        for s in sorted(by_provider[provider], key=lambda x: x.display_name):
            selectable = s.status == "available" and s.bind_id is not None
            rows.append(SelectOption(
                id=s.bind_id or "",
                label=f"{s.display_name}{_STATUS_TAG.get(s.status, '')}",
                group=provider,
                disabled=not selectable,
            ))
    return rows
