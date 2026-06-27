"""The persona rail's pure roster model — no Textual, no I/O.

Composes the AgentRail's display rows from the existing-persona list, the active
id (from C2a's FleetSnapshot.active_id), and a name lookup. Pure so it is
exhaustively unit-testable, like render.py / state.py."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class PersonaRow:
    id: str
    name: str
    active: bool


def persona_rows(
    personas: list[str],
    active_id: str,
    name_of: Callable[[str], str | None],
) -> tuple[PersonaRow, ...]:
    """One row per persona, in the given order; name falls back to the id.
    INVARIANT: the active id is always present — appended last if not in
    `personas` — so the rail can always highlight the running persona (mirrors
    C2a's 'active is never None')."""
    rows = [
        PersonaRow(id=pid, name=(name_of(pid) or pid), active=(pid == active_id))
        for pid in personas
    ]
    if not any(r.id == active_id for r in rows):
        rows.append(PersonaRow(id=active_id, name=active_id, active=True))
    return tuple(rows)
