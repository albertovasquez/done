"""Atomic status widgets for the design system: StatusChip (uppercase pill),
StateDot (leading glyph), ActivityGlyph (the ONE looping animation). All read the
shared token vocabulary; colors come from the theme. See spec §6 / components.md."""

from __future__ import annotations

from textual.widgets import Static

from harness.tui.state import AgentState
from harness.tui.tokens import GLYPH, STATUS_LABEL

_STATE_TOKEN = {
    AgentState.IDLE: "muted",
    AgentState.THINKING: "accent",
    AgentState.RESPONDING: "accent",
    AgentState.RUNNING_TOOL: "accent",
    AgentState.AWAITING_PERMISSION: "scheduled",
    AgentState.AWAITING_DECISION: "scheduled",
    AgentState.SCHEDULED: "scheduled",
    AgentState.DONE: "success",
    AgentState.FAILED: "error",
}

_STATE_GLYPH = {
    AgentState.IDLE: "idle",
    AgentState.THINKING: "active",
    AgentState.RESPONDING: "responding",
    AgentState.RUNNING_TOOL: "active",
    AgentState.AWAITING_PERMISSION: "awaiting",
    AgentState.AWAITING_DECISION: "awaiting",
    AgentState.SCHEDULED: "scheduled",
    AgentState.DONE: "done",
    AgentState.FAILED: "failed",
}


def state_color_token(state: AgentState) -> str:
    return _STATE_TOKEN.get(state, "muted")


class StatusChip(Static):
    def __init__(self, label: str, color_token: str) -> None:
        super().__init__(markup=True)
        self._label = label
        self._token = color_token
        self.update(f"[${color_token}][b]{label}[/b][/]")

    @classmethod
    def from_state(cls, state: AgentState) -> "StatusChip":
        label = STATUS_LABEL.get(state.value, state.value.upper())
        return cls(label, state_color_token(state))


class StateDot(Static):
    def __init__(self, state: AgentState) -> None:
        super().__init__(markup=True)
        glyph = GLYPH[_STATE_GLYPH.get(state, "idle")]
        self.update(f"[${state_color_token(state)}]{glyph}[/]")


class ActivityGlyph(Static):
    """The single looping animation in the UI: a quiet spinner of half-moons.
    reduced_motion → a static ◐ (no timer)."""
    _CYCLE = ["◐", "◓", "◑", "◒"]

    def __init__(self, reduced_motion: bool = False) -> None:
        super().__init__(markup=True)
        self._frames_static = reduced_motion
        self._i = 0

    def on_mount(self) -> None:
        self.update("[$accent]◐[/]")
        if not self._frames_static:
            self.set_interval(0.15, self._tick)

    def _tick(self) -> None:
        self._i = (self._i + 1) % len(self._CYCLE)
        self.update(f"[$accent]{self._CYCLE[self._i]}[/]")
