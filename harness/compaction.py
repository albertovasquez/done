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
DEFAULT_CONTEXT_WINDOW = 32000

# Done's shipped models -> real context windows (authoritative as of 2026-06-29).
# litellm.get_max_tokens is WRONG for these (it predates them: returns 128000 for
# gpt-5.4 and claude-opus-4-8, which are really 400k/1M), so this table wins over it.
# Maintain as models change.
CONTEXT_WINDOWS = {
    "gpt-5.4": 400_000,
    "gpt-5.4-mini": 400_000,
    "claude-opus-4-8": 1_000_000,
    "claude-opus-4-7": 1_000_000,
    "claude-opus-4-6": 1_000_000,
    "claude-sonnet-4-6": 1_000_000,
    "claude-haiku-4-5": 200_000,
    "claude-fable-5": 1_000_000,
}


def _get_max_tokens(name: str):
    """Lazy litellm fallback for unknown models. Imported here (not at module top)
    because litellm import costs ~1s on the startup path; this is only called when
    compaction builds an adapter for a model absent from CONTEXT_WINDOWS."""
    try:
        from litellm import get_max_tokens
        return get_max_tokens(name)
    except Exception:
        return None


def resolve_ctx_window(model_name, cfg_override=None) -> int:
    """Resolve the model's context window:
    config override > curated table > litellm.get_max_tokens > floor.
    `model_name` is normalized by stripping a leading 'openai/' provider prefix."""
    if cfg_override:
        return int(cfg_override)
    name = (model_name or "").split("/", 1)[-1]
    if name in CONTEXT_WINDOWS:
        return CONTEXT_WINDOWS[name]
    n = _get_max_tokens(name)
    if n:
        return int(n)
    return DEFAULT_CONTEXT_WINDOW

COMPRESS_SYSTEM = (
    "You are a context compaction assistant. You will be given a section of a "
    "conversation transcript that needs to be summarized to save context space. "
    "Produce a concise summary that preserves key facts, decisions, code changes, "
    "and tool outputs. The summary will be inserted back into the conversation so "
    "the agent can continue without losing important context. Be factual and brief."
)


@dataclass
class CompactResult:
    messages: list[dict]
    compressed: bool
    method: str            # "none" | "summary" | "truncated"
    before_tokens: int
    after_tokens: int
    before_msgs: int
    after_msgs: int


def estimate_tokens(text: str) -> int:
    """Cheap token estimator: 4 chars ≈ 1 token. Always ≥ 1."""
    return max(1, len(text) // 4)


@dataclass
class Compaction:
    """Bound configuration + callables for one compress() invocation.

    ``summarize`` is the LLM closure built by ``build_compaction``.
    ``params()`` returns all keyword arguments for ``compress()`` except ``prior``
    (which is supplied per-turn by the caller).
    """
    summarize: Callable[[list[dict]], str]
    count_tokens: Callable[[str], int]
    fixed_overhead_tokens: int
    ctx_window: int
    threshold: float = 0.5
    target_ratio: float = 0.2
    protect_head_n: int = 0
    protect_last_n: int = 20
    enabled: bool = True
    on_event: "Callable | None" = None

    def params(self) -> dict:
        """Return kwargs for compress() (everything except ``prior``)."""
        return {
            "summarize": self.summarize,
            "count_tokens": self.count_tokens,
            "fixed_overhead_tokens": self.fixed_overhead_tokens,
            "ctx_window": self.ctx_window,
            "threshold": self.threshold,
            "target_ratio": self.target_ratio,
            "protect_head_n": self.protect_head_n,
            "protect_last_n": self.protect_last_n,
            "on_event": self.on_event,
        }


def build_compaction(cfg, *, model, fixed_overhead_tokens: int,
                     add_cost: Callable[[float], None],
                     model_name: str = "",
                     on_event=None,
                     now=None) -> "Compaction | None":
    """Build a ``Compaction`` adapter from a config dict and live model/cost hooks.

    Returns ``None`` when compaction is disabled or ``cfg`` is falsy.

    ``model`` must implement ``query(messages: list[dict]) -> dict`` where the
    returned dict has ``"content"`` (str) and ``"extra": {"cost": float}``.
    ``add_cost`` is called with each summarize call's cost so the session can
    track it alongside normal turn costs.
    ``model_name`` is used to resolve ``ctx_window`` via ``resolve_ctx_window``
    when no explicit ``ctx_window`` is set in ``cfg``.
    ``on_event`` and ``now`` are stored for future observability emission (Task 3).
    """
    if not cfg or not cfg.get("enabled"):
        return None

    ctx_window: int = resolve_ctx_window(model_name, cfg.get("ctx_window"))
    threshold: float = float(cfg.get("threshold", 0.5))
    target_ratio: float = float(cfg.get("target_ratio", 0.2))
    protect_head_n: int = int(cfg.get("protect_head_n", 0))
    protect_last_n: int = int(cfg.get("protect_last_n", 20))

    def summarize(middle: list[dict]) -> str:
        user_content = render(middle)
        start = now() if now else None
        msg = model.query([
            {"role": "system", "content": COMPRESS_SYSTEM},
            {"role": "user", "content": user_content},
        ])
        cost = msg.get("extra", {}).get("cost", 0.0)
        add_cost(cost)
        text = msg.get("content") or ""
        if on_event:
            on_event("context.compaction.summarize", {
                "in_tokens": estimate_tokens(user_content),
                "out_tokens": estimate_tokens(text),
                "cost": cost,
                "elapsed_s": round((now() - start), 3) if (now and start is not None) else 0.0,
            })
        return text

    return Compaction(
        summarize=summarize,
        count_tokens=estimate_tokens,
        fixed_overhead_tokens=fixed_overhead_tokens,
        ctx_window=ctx_window,
        threshold=threshold,
        target_ratio=target_ratio,
        protect_head_n=protect_head_n,
        protect_last_n=protect_last_n,
        enabled=True,
        on_event=on_event,
    )


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
             protect_head_n: int = 0, protect_last_n: int = 20,
             on_event=None) -> CompactResult:
    prior = prior or []
    before_msgs = len(prior)
    before_tokens = count_tokens(render(prior))
    budget = int(threshold * ctx_window) - fixed_overhead_tokens  # pre-clamp: reported in eval

    def _emit_eval(decision):
        if on_event:
            on_event("context.compaction.eval", {
                "prior_tokens": before_tokens, "budget": budget,
                "ctx_window": ctx_window, "fixed_overhead": fixed_overhead_tokens,
                "decision": decision,
            })

    def noop(method="none"):
        _emit_eval(method)
        return CompactResult(prior, False, method, before_tokens, before_tokens,
                             before_msgs, before_msgs)

    if budget <= 0:
        log.warning("compaction: fixed overhead (%d) >= budget; cannot compact",
                    fixed_overhead_tokens)
        return noop()
    clamped_budget = max(budget, MIN_BUDGET_FLOOR)

    if before_tokens <= clamped_budget:
        return noop()

    head, middle, tail = _split(prior, count_tokens=count_tokens, budget=clamped_budget,
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
    _emit_eval(method)
    return CompactResult(new, True, method, before_tokens,
                         count_tokens(render(new)), before_msgs, len(new))


def _sanitize_tool_pairs(messages: list[dict]) -> list[dict]:
    """Repair tool-call/tool-result pairs orphaned when the middle of the transcript is dropped.

    Rules:
    - A tool result whose call id is not present among surviving assistant tool_calls → DROP.
    - An assistant tool_calls[].id with no surviving result → INJECT a stub tool message
      immediately after that assistant message.
    """
    # Collect all call ids present in surviving assistant messages
    surviving_call_ids: set[str] = set()
    for m in messages:
        if m.get("role") == "assistant":
            for tc in m.get("tool_calls") or []:
                tc_id = tc.get("id")
                if tc_id:
                    surviving_call_ids.add(tc_id)

    # Collect all tool result ids that survive
    surviving_result_ids: set[str] = set()
    for m in messages:
        if m.get("role") == "tool":
            tc_id = m.get("tool_call_id")
            if tc_id and tc_id in surviving_call_ids:
                surviving_result_ids.add(tc_id)

    # Build output: drop orphan tool results, inject stubs for orphan calls
    out: list[dict] = []
    for m in messages:
        if m.get("role") == "tool":
            # Drop if no matching call survives
            if m.get("tool_call_id") not in surviving_call_ids:
                continue
            out.append(m)
        elif m.get("role") == "assistant":
            out.append(m)
            # Inject a stub for each call whose result is absent
            for tc in m.get("tool_calls") or []:
                tc_id = tc.get("id")
                if tc_id and tc_id not in surviving_result_ids:
                    out.append({
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": "[result omitted during context compaction]",
                    })
        else:
            out.append(m)
    return out
