"""Unit tests for StreamPainter's block-placement decision, driven against a fake
TranscriptView — no Textual runtime. These cover the four cases that produced the
historical stream bugs (#81 misroute, #138 footer-above-answer, #217 coalesce):
new answer, new step after a boundary, late delta extends in place, and footer
ordering. The interface is the test surface (ADR-0001)."""

import sys
sys.path.insert(0, "upstream/src"); sys.path.insert(0, ".")

from harness.tui.stream_painter import StreamPainter


class FakeTimer:
    def __init__(self, fn) -> None:
        self.fn = fn
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True

    def tick(self) -> None:
        """Fire the repeating flush, as the real 12Hz interval would."""
        if not self.stopped:
            self.fn()


class FakeMarkdown:
    """Stands in for a mounted Markdown widget: records painted text."""
    def __init__(self, initial="") -> None:
        self.painted: list[str] = []

    def update(self, text) -> None:
        self.painted.append(text)


class FakeFooter:
    _copyable = True


class FakeView:
    """Fake TranscriptView: a children list + synchronous scheduling so a paint
    lands immediately (mirrors call_after_refresh once the widget is mounted)."""
    def __init__(self) -> None:
        self._kids: list = []
        self.hide_working_calls = 0
        self.timer: FakeTimer | None = None

    def children(self) -> list:
        return list(self._kids)

    def mount(self, widget) -> None:
        self._kids.append(widget)

    def mount_before_footer(self, widget) -> None:
        if self._kids and getattr(self._kids[-1], "_copyable", False):
            self._kids.insert(len(self._kids) - 1, widget)
        else:
            self._kids.append(widget)

    def after_refresh(self, fn, *args) -> None:
        fn(*args)                       # synchronous: widget is already mounted

    def schedule(self, fn, interval):
        self.timer = FakeTimer(fn)
        return self.timer

    def hide_working(self) -> None:
        self.hide_working_calls += 1


def _painter(monkeypatch):
    """A painter whose Markdown() constructor yields FakeMarkdown, so mounted
    widgets are inspectable without a Textual app."""
    import harness.tui.stream_painter as spmod
    monkeypatch.setattr(spmod, "Markdown", FakeMarkdown)
    view = FakeView()
    return StreamPainter(view), view


def test_new_answer_opens_one_widget(monkeypatch):
    p, view = _painter(monkeypatch)
    p.delta("hello ")                  # first chunk paints synchronously
    p.delta("world")                   # open-stream chunk coalesces onto the timer
    md_widgets = [k for k in view.children() if isinstance(k, FakeMarkdown)]
    assert len(md_widgets) == 1
    assert p.buf == "hello world"      # buffer accumulates every chunk
    assert md_widgets[0].painted[-1] == "hello ", "mid-stream chunk should wait for the timer"
    assert p.dirty, "coalesced chunk stays dirty until the tick"
    view.timer.tick()                  # 12Hz flush fires
    assert md_widgets[0].painted[-1] == "hello world"
    assert not p.dirty and not p.closed


def test_new_step_after_boundary_opens_fresh_widget(monkeypatch):
    """A tool/thought boundary mid-turn → next prose is a NEW step, its own block."""
    p, view = _painter(monkeypatch)
    p.delta("step one")
    p.end(boundary=True)               # tool call interleaves
    # a non-stream widget (e.g. tool caption) is mounted by the App between steps
    view.mount(object())
    p.delta("step two")
    md_widgets = [k for k in view.children() if isinstance(k, FakeMarkdown)]
    assert len(md_widgets) == 2, "new step must open its own block, not merge"
    assert md_widgets[1].painted[-1] == "step two"


def test_late_delta_extends_prior_widget_in_place(monkeypatch):
    """Turn-end close (no boundary); a straggling delta of the just-closed answer
    extends its widget in place — never a fresh block."""
    p, view = _painter(monkeypatch)
    p.delta("answer")
    p.end()                            # turn-end close (boundary=False)
    p.clear_boundary()                 # a new user turn began
    view.mount(object())               # ...and the user's own message was mounted
    assert p.closed
    p.delta(" tail")                   # late delta arrives now
    md_widgets = [k for k in view.children() if isinstance(k, FakeMarkdown)]
    assert len(md_widgets) == 1, "late delta must NOT open a stray block"
    assert p.buf == "answer tail"
    assert md_widgets[0].painted[-1] == "answer tail"
    assert p.closed, "a late delta does not re-open the stream"


def test_answer_mounts_above_a_trailing_footer(monkeypatch):
    """#138: if the run-caption footer already landed (prompt returned before the
    deltas), the answer mounts ABOVE it so the caption stays below the prose."""
    p, view = _painter(monkeypatch)
    footer = FakeFooter()
    view.mount(footer)                 # footer mounted first (late-delivery)
    p.delta("late answer")
    kids = view.children()
    md_idx = next(i for i, k in enumerate(kids) if isinstance(k, FakeMarkdown))
    foot_idx = kids.index(footer)
    assert md_idx < foot_idx, "answer must render above the footer, not under it"


def test_reset_clears_state_and_stops_timer(monkeypatch):
    p, view = _painter(monkeypatch)
    p.delta("x")                       # opens stream, arms timer
    timer = p._stream_timer
    p.reset()
    assert p.widget is None and p.buf == "" and p.closed and not p.dirty
    assert timer.stopped, "reset must stop the flush timer"
