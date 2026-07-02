"""Episodic compacted view of a session transcript (#105, PR 3 of #139).

The store's transcript is append-only raw truth. Re-summarizing FROM THE FULL
TRANSCRIPT every turn (what the engine-side per-turn compaction does once over
budget) rewrites the history head every turn — permanently cache-cold. This
module persists each compaction episode as a CompactView so between episodes
the effective history is byte-stable and append-only:
``view.messages + transcript[view.upto:]``. Pure — no I/O; the summarize LLM
closure is injected by the caller."""

from __future__ import annotations

from dataclasses import dataclass

from harness import compaction as _compaction


@dataclass
class CompactView:
    upto: int               # transcript prefix length this view replaces
    messages: list[dict]    # compacted stand-in for transcript[:upto]


def effective_history(transcript: list[dict], view: CompactView | None) -> list[dict]:
    """The history consumers should send: compacted episodes + live tail."""
    if view is None:
        return list(transcript)
    return list(view.messages) + list(transcript[view.upto:])


def reconcile(transcript: list[dict], view: CompactView | None, *,
              summarize, fixed_overhead_tokens: int, ctx_window: int,
              on_event=None):
    """Return ``(history, view', result)``.

    Episodic-never-sliding: compress() fires only when the effective history
    exceeds the budget; when it fires, the result is PERSISTED as a new view
    anchored at the current transcript length, so the next turn appends to the
    compacted head instead of re-summarizing (one deliberate cache miss per
    episode). Under budget, compress() returns the same list untouched."""
    history = effective_history(transcript, view)
    result = _compaction.compress(
        history,
        summarize=summarize,
        count_tokens=_compaction.estimate_tokens,
        fixed_overhead_tokens=fixed_overhead_tokens,
        ctx_window=ctx_window,
        on_event=on_event,
    )
    if not result.compressed:
        return history, view, result
    new_view = CompactView(upto=len(transcript), messages=list(result.messages))
    return list(result.messages), new_view, result
