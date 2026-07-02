"""Episodic-never-sliding invariant for session history (#105, PR 3)."""
import pytest

from harness.history_view import CompactView, effective_history, reconcile

# Sizing: ctx_window=4000 -> budget = 0.5*4000 = 2000 tokens (>= MIN_BUDGET_FLOOR).
# Each message is 160 chars = 40 tokens. 60 msgs = 2400 tokens > 2000 -> fires.
# Tail protection keeps the last 20 msgs (~800 tokens >= tail target 400), so the
# post-episode view is ~810 tokens << 2000 -> genuinely episodic afterwards.
CTX = 4000
MSG = {"role": "user", "content": "x" * 160, "origin": "chat"}


def _transcript(n):
    return [dict(MSG) for _ in range(n)]


def _counting_summarize():
    calls = {"n": 0}

    def summarize(middle):
        calls["n"] += 1
        return "SUMMARY"
    return summarize, calls


def test_effective_history_is_transcript_when_no_view():
    t = _transcript(3)
    assert effective_history(t, None) == t
    assert effective_history(t, None) is not t          # copy, not alias


def test_effective_history_composes_view_plus_tail():
    t = _transcript(5)
    view = CompactView(upto=3, messages=[{"role": "user", "content": "S"}])
    out = effective_history(t, view)
    assert out == [{"role": "user", "content": "S"}] + t[3:]


def test_under_budget_no_compression_no_summarize_call():
    summarize, calls = _counting_summarize()
    t = _transcript(5)
    history, view, result = reconcile(t, None, summarize=summarize,
                                      fixed_overhead_tokens=0, ctx_window=CTX)
    assert history == t and view is None
    assert result.compressed is False and calls["n"] == 0


def test_episode_fires_once_then_head_is_byte_stable():
    summarize, calls = _counting_summarize()
    t = _transcript(60)                                  # over budget
    history1, view1, r1 = reconcile(t, None, summarize=summarize,
                                    fixed_overhead_tokens=0, ctx_window=CTX)
    assert r1.compressed and r1.method == "summary" and calls["n"] == 1
    assert view1 is not None and view1.upto == 60
    assert history1 == view1.messages

    t.extend(_transcript(5))                             # small tail growth
    history2, view2, r2 = reconcile(t, view1, summarize=summarize,
                                    fixed_overhead_tokens=0, ctx_window=CTX)
    assert r2.compressed is False and calls["n"] == 1    # NO re-summarize
    assert view2 is view1                                # view unchanged
    # THE invariant: the head of the history is byte-stable between episodes.
    assert history2[:len(view1.messages)] == view1.messages
    assert history2[len(view1.messages):] == t[60:]


def test_regrowth_triggers_second_episode_anchored_at_new_length():
    summarize, calls = _counting_summarize()
    t = _transcript(60)
    _, view1, _ = reconcile(t, None, summarize=summarize,
                            fixed_overhead_tokens=0, ctx_window=CTX)
    t.extend(_transcript(40))                            # regrow past budget
    history3, view3, r3 = reconcile(t, view1, summarize=summarize,
                                    fixed_overhead_tokens=0, ctx_window=CTX)
    assert r3.compressed and calls["n"] == 2
    assert view3.upto == 100
    assert history3 == view3.messages


def test_summarize_failure_degrades_to_truncated_and_still_persists():
    def boom(middle):
        raise RuntimeError("no summarizer")
    t = _transcript(60)
    history, view, result = reconcile(t, None, summarize=boom,
                                      fixed_overhead_tokens=0, ctx_window=CTX)
    assert result.compressed and result.method == "truncated"
    assert view is not None and view.upto == 60
    assert history == view.messages
