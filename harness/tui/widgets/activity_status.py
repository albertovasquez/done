"""ActivityStatus — the live work line: '◐ <label>… (1m 18s · ↓ 4.0k tokens)'.
Supersedes the bare LoadingIndicator. Reads an AgentSnapshot; the app supplies
elapsed (it owns the clock). Animates one glyph while working; blank when idle/
terminal. See spec §6 / components.md C."""

from __future__ import annotations

from textual.widgets import Static

from harness.tui.state import AgentSnapshot, AgentState

_WORKING = {AgentState.THINKING, AgentState.RESPONDING, AgentState.RUNNING_TOOL,
            AgentState.AWAITING_PERMISSION, AgentState.AWAITING_DECISION}
_CYCLE = ["◐", "◓", "◑", "◒"]


def _fmt_elapsed(s: float) -> str:
    s = int(s)
    return f"{s//60}m {s%60:02d}s" if s >= 60 else f"{s}s"


def _fmt_tokens(n: int) -> str:
    return f"{n/1000:.1f}k" if n >= 1000 else str(n)


class ActivityStatus(Static):
    def __init__(self) -> None:
        super().__init__(markup=True)
        self._i = -1   # first _tick increments to 0, so cycle starts at frame 0
        self._snap: AgentSnapshot | None = None

    def on_mount(self) -> None:
        self._timer = self.set_interval(0.15, self._tick)

    def line_for(self, snap: AgentSnapshot, glyph: str = "◐") -> str:
        if snap.state not in _WORKING:
            return ""
        label = snap.activity_label or "Working"
        meta = f"{_fmt_elapsed(snap.elapsed)} · ↓ {_fmt_tokens(snap.tokens)} tokens"
        return f"[$accent]{glyph}[/] [$foreground]{label}…[/] [$muted]({meta})[/]"

    def update_from(self, snap: AgentSnapshot) -> None:
        self._snap = snap
        is_working = snap.state in _WORKING
        # Pause/resume the timer to match the current snapshot's working state.
        # Ensures timer is paused even on the first update if the snapshot is
        # idle/done/failed (not just on transitions). Idempotent operations.
        # NOTE: timer pause/resume cannot be tested without mounting the widget;
        # unit tests cover display output only.
        if hasattr(self, "_timer"):
            if is_working:
                self._timer.resume()
            else:
                self._timer.pause()
        self._render()

    def _tick(self) -> None:
        if self._snap is None or self._snap.state not in _WORKING:
            return
        self._i = (self._i + 1) % len(_CYCLE)
        self._render()

    def _render(self) -> None:
        if self._snap is None:
            self.update("")
            return
        self.update(self.line_for(self._snap, _CYCLE[self._i]))
