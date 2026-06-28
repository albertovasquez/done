# harness/compaction.py
"""Pure, provider-agnostic context compaction.

Bounds the cross-turn transcript before it is sent to the model: protects a head
and a recent tail, summarizes the middle via an INJECTED callable, and repairs
tool-call/tool-result pairs orphaned by the cut. Imports nothing model-related —
all model/provider access arrives as callables (see harness.tracing_agent for
the adapter that wires them). See docs/superpowers/specs/2026-06-28-context-
compressor-design.md.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger(__name__)

MIN_BUDGET_FLOOR = 1000  # tokens; keeps the tail target from going negative


@dataclass
class CompactResult:
    messages: list[dict]
    compressed: bool
    method: str            # "none" | "summary" | "truncated"
    before_tokens: int
    after_tokens: int
    before_msgs: int
    after_msgs: int


def render(messages: list[dict]) -> str:
    return "\n".join(f"{m.get('role','')}: {m.get('content','')}" for m in messages)


def _split(prior, *, count_tokens, budget, protect_head_n, protect_last_n, target_ratio):
    head = prior[:protect_head_n]
    rest = prior[protect_head_n:]
    tail: list[dict] = []
    tail_target = max(int(target_ratio * budget), 0)
    for m in reversed(rest):
        if len(tail) >= protect_last_n and count_tokens(render(tail)) >= tail_target:
            break
        tail.insert(0, m)
    middle = rest[: len(rest) - len(tail)]
    return head, middle, tail


def compress(prior, *, summarize: Callable[[list[dict]], str],
             count_tokens: Callable[[str], int], fixed_overhead_tokens: int,
             ctx_window: int, threshold: float = 0.5, target_ratio: float = 0.2,
             protect_head_n: int = 0, protect_last_n: int = 20) -> CompactResult:
    prior = prior or []
    before_msgs = len(prior)
    before_tokens = count_tokens(render(prior))

    def noop(method="none"):
        return CompactResult(prior, False, method, before_tokens, before_tokens,
                             before_msgs, before_msgs)

    budget = int(threshold * ctx_window) - fixed_overhead_tokens
    if budget <= 0:
        log.warning("compaction: fixed overhead (%d) >= budget; cannot compact",
                    fixed_overhead_tokens)
        return noop()
    budget = max(budget, MIN_BUDGET_FLOOR)

    if before_tokens <= budget:
        return noop()

    head, middle, tail = _split(prior, count_tokens=count_tokens, budget=budget,
                                protect_head_n=protect_head_n,
                                protect_last_n=protect_last_n, target_ratio=target_ratio)
    if not middle:
        return noop()

    try:
        text = summarize(middle)
        summary = {"role": "user",
                   "content": "[Earlier conversation summarized to save context]\n" + text}
        new = head + [summary] + tail
        method = "summary"
    except Exception:                       # noqa: BLE001 — never crash the turn
        log.warning("compaction: summarize failed; falling back to truncation",
                    exc_info=True)
        new = head + tail
        method = "truncated"

    new = _sanitize_tool_pairs(new)
    return CompactResult(new, True, method, before_tokens,
                         count_tokens(render(new)), before_msgs, len(new))


def _sanitize_tool_pairs(messages: list[dict]) -> list[dict]:
    return messages  # implemented in Task 2
