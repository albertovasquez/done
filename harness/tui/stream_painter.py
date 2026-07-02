"""StreamPainter — turns a series of agent message deltas into correctly-placed,
coalesced Markdown in the transcript.

Extracted verbatim from HarnessTui (see ADR-0001). The painter owns the live
answer widget and the six pieces of streaming state; it decides whether an
arriving delta opens a fresh block or extends an existing one. It depends on a
narrow `TranscriptView` seam so the block-placement logic — the part that
produced the stream-misroute (#81), footer-above-answer (#138), and
coalesce-deltas (#217) bugs — is testable against a fake, with no Textual runtime.

Two block-placement signals are kept ON PURPOSE (ADR-0001): the explicit
`boundary` flag AND the positional `prior_is_last` check. They are not provably
equivalent, so this is a behavior-preserving port; collapsing them is deferred to
a tested follow-up.
"""

from __future__ import annotations

from typing import Any, Protocol

from textual.widgets import Markdown

from harness.tui.state import strip_done_sentinel_prose


class TranscriptView(Protocol):
    """The narrow seam the painter depends on. The live TUI adapts this over the
    real transcript; tests provide a fake. Scheduling ops delegate to the App's
    message pump (never the answer widget) so the paint timer shares the App's
    lifecycle (ADR-0001)."""

    def children(self) -> list:
        """Current transcript children, in order (for the positional check)."""

    def mount(self, widget: Any) -> None:
        """Append a widget to the transcript."""

    def mount_before_footer(self, widget: Any) -> None:
        """Mount a widget, keeping any trailing run-caption footer last: a late
        answer must render ABOVE the footer, not under it."""

    def after_refresh(self, fn: Any, *args: Any) -> None:
        """Schedule a one-shot call for after the next refresh (delegates to the
        App). Used for the first paint, which must land post-mount."""

    def schedule(self, fn: Any, interval: float) -> Any:
        """Start a repeating timer (delegates to the App); returns a handle whose
        `.stop()` cancels it."""

    def hide_working(self) -> None:
        """Remove the 'model is working' indicator, if present (idempotent)."""


class StreamPainter:
    def __init__(self, view: TranscriptView) -> None:
        self._view = view
        self._streaming_md: Markdown | None = None  # live Markdown widget, else None
        self._stream_buf = ""                        # accumulated text for _streaming_md
        self._stream_dirty = False                   # buffer changed since last paint
        self._stream_timer: Any = None               # repeating flush handle while open
        self._stream_closed = True                   # True => next delta starts a fresh widget
        self._boundary_after = False                 # True => an in-turn boundary closed the block
        # KNOWN GAP (#291): this flag has no per-turn identity. A stale turn's
        # late delta can consume a boundary meant for a LATER turn's first
        # delta (e.g. a cancelled turn's straggler arriving after the next
        # turn's task_classified chip), misrouting that later turn's real
        # answer. See test_cancelled_turn_stream_closed_immediately_on_esc.

    # ---- read accessors (the App's compat shims forward to these) ----

    @property
    def buf(self) -> str:
        return self._stream_buf

    @property
    def closed(self) -> bool:
        return self._stream_closed

    @property
    def dirty(self) -> bool:
        return self._stream_dirty

    @property
    def widget(self) -> Markdown | None:
        return self._streaming_md

    @property
    def boundary_pending(self) -> bool:
        return self._boundary_after

    @property
    def timer(self) -> Any:
        return self._stream_timer

    # ---- lifecycle ----

    def reset(self) -> None:
        """Reset stream-accumulation state so no late delta bleeds into a fresh
        view. Stops the flusher and clears dirty BEFORE nulling the widget so a
        stale flush can't fire (or paint into) a reset/replaced stream. Does NOT
        clear the transcript — that stays the App's job (_clear_transcript)."""
        self._stop_timer()
        self._stream_dirty = False
        self._streaming_md = None
        self._stream_buf = ""
        self._stream_closed = True
        self._boundary_after = False

    def clear_boundary(self) -> None:
        """A new user turn is NOT an in-turn boundary: clear the flag so a trailing
        late delta of the prior answer extends its widget rather than opening a new
        block under the next prompt."""
        self._boundary_after = False

    def end(self, *, boundary: bool = False) -> None:
        """Close the current live Markdown block: the NEXT delta starts a fresh
        widget. The widget reference is KEPT (not nulled) so a late delta belonging
        to the just-closed answer (notification-delivery can lag prompt() returning)
        still appends to ITS block, in place — rather than spawning a stray block
        under the next user prompt.

        `boundary=True` marks an IN-TURN step boundary (tool call, thought, or an
        explicit stream_reset): the agent is still producing this turn, so the next
        prose is a genuinely NEW step that must open its own widget. The default
        (`boundary=False`) is the turn-end / new-user-turn close, after which a
        trailing late delta of the just-closed answer extends it in place. `delta`
        keys on `_boundary_after` to tell the two apart."""
        self._stream_closed = True
        self._flush()                 # R1: paint any unpainted tail before close
        self._stop_timer()            # R2: no free-running timer between turns
        if boundary:
            self._boundary_after = True

    def delta(self, text: str) -> None:
        """Accumulate an agent message delta into a single live Markdown widget.

        Routing distinguishes three cases for a delta that arrives after the stream
        was closed:
          - a NEW answer (its first delta) opens a fresh widget at the bottom;
          - a NEW agent STEP within the same turn (after a tool call / thought /
            explicit stream_reset) opens its own fresh widget — so multi-step
            narration does not merge into the previous step's block;
          - a LATE delta for the just-finished answer (notification lag, after a
            NEW USER turn began) extends that prior widget in place — never a stray
            block under the next prompt.
        The new-step and late-delta cases have IDENTICAL positional signals (prior
        widget closed and no longer last), so position alone cannot separate them.
        We use the `_boundary_after` flag instead: set by `end(boundary=True)` on an
        in-turn boundary, cleared by `clear_boundary` (a new user turn) and `reset`.
        Flag set ⇒ new step (fresh widget); flag clear with a closed prior ⇒ late
        delta (extend in place).

        Markdown.update() is a no-op until the widget is mounted, so the render is
        scheduled via after_refresh — by the next refresh the mount has completed
        and the accumulated buffer renders."""
        kids = list(self._view.children())
        prior_is_last = self._streaming_md is not None and kids and kids[-1] is self._streaming_md
        # An IN-TURN boundary (tool line / thought / explicit stream_reset) closed
        # the prior block while the agent keeps producing this turn, so the next
        # prose is a genuinely NEW step that must open its own widget — NOT a late
        # delta of the just-closed answer. `_boundary_after` is set by
        # end(boundary=True) and cleared by clear_boundary (a new user turn is the
        # late-delta case, where the prior widget extends in place).
        boundary_after = self._boundary_after and self._streaming_md is not None

        opened_new = False
        if self._stream_closed and self._streaming_md is not None \
                and not prior_is_last and not boundary_after:
            # late delta for the just-closed answer → extend its widget in place;
            # the stream stays CLOSED (this delta does not begin a new answer).
            pass
        elif self._streaming_md is None or self._stream_closed:
            # new answer / new in-turn step → fresh widget at the bottom; stream
            # is now OPEN and the boundary has been consumed.
            self._view.hide_working()
            self._streaming_md = Markdown("")
            # Late-delivery ordering: prompt() can return (mounting THIS turn's
            # footer) before the trailing message deltas arrive. If the run-caption
            # footer is already the last child, mount the answer ABOVE it so the
            # '… (copy)' caption stays BELOW the prose — otherwise the answer lands
            # under the footer (footer-above-answer bug).
            self._view.mount_before_footer(self._streaming_md)
            self._stream_buf = ""
            self._stream_closed = False
            self._boundary_after = False
            opened_new = True
        # else: stream already open → keep extending it.
        self._stream_buf += text
        self._stream_dirty = True
        if self._stream_closed or opened_new:
            # R1: a late delta after close cannot rely on the interval (stopped on
            # close) → flush SYNC. opened_new: the first chunk of a new answer must
            # paint immediately, not wait up to 80ms for the timer (avoids a
            # post-hide_working blank flicker). Subsequent open-stream chunks
            # coalesce on the timer.
            self._flush()
            if not self._stream_closed:
                self._ensure_timer()   # arm for the chunks that follow
        else:
            self._ensure_timer()
        # No explicit scroll here: the anchored transcript (see _enter_conversation)
        # follows the stream to the bottom while the user is there, and holds
        # position once they scroll up to read earlier content.

    # ---- flushing / timer ----

    def _ensure_timer(self) -> None:
        # R2: start a 12Hz flusher on stream-open; it is stopped on close/reset.
        if self._stream_timer is None:
            self._stream_timer = self._view.schedule(self._flush, 1 / 12)

    def _stop_timer(self) -> None:
        if self._stream_timer is not None:
            self._stream_timer.stop()
            self._stream_timer = None

    def _flush(self) -> None:
        # R2/R3: no-op when nothing to paint or the widget is gone (teardown);
        # capture the CURRENT widget+buffer so a flush can't paint a stale buffer
        # into a new widget after a reset.
        if not self._stream_dirty or self._streaming_md is None:
            return
        md, buf = self._streaming_md, self._stream_buf
        self._stream_dirty = False
        # Drop the turn-end sentinel if a model typed it into its prose (the
        # tool-row guard _is_done_sentinel never sees a typed line). Stripping the
        # WHOLE buffer here — not the raw _stream_buf accumulator — keeps the
        # rendered widget clean (which is also what the (copy) affordance reads,
        # see _copy_turn_response) while leaving the accumulator untouched so a
        # sentinel split across chunks is matched once fully assembled.
        buf = strip_done_sentinel_prose(buf)
        # R4: md.update is a no-op until the widget mounts; after_refresh guarantees
        # the FIRST paint lands post-mount, matching prior behavior.
        self._view.after_refresh(md.update, buf)
