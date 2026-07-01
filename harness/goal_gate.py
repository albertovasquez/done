"""Pure stop/continue/escape policy for the /goal stop-gate. No LLM, no I/O — the
engine computes the reviewer Verdict and passes it here. reviewer_attempts is the
gate's OWN budget (separate from the worker's n_calls, per spec §4.1).

GoalContext is the per-session goal state (lives on SessionState); it is co-located
here so the policy and its state share one leaf."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Verdict:
    met: bool
    reason: str = ""


@dataclass(frozen=True)
class GateLimits:
    max_attempts: int


@dataclass(frozen=True)
class GateDecision:
    action: str          # "stop" | "continue" | "escape"
    reason: str = ""


@dataclass
class GoalContext:
    text: str
    reviewer_model: str
    max_attempts: int = 3
    attempts: int = 0


def decide(*, goal: str | None, verdict: "Verdict | None",
           reviewer_attempts: int, limits: GateLimits) -> GateDecision:
    if not goal:
        return GateDecision("stop")
    if reviewer_attempts >= limits.max_attempts:
        return GateDecision("escape", f"goal not met after {reviewer_attempts} attempts")
    if verdict is None:
        return GateDecision("escape", "reviewer unavailable")
    if verdict.met:
        return GateDecision("stop")
    return GateDecision("continue", verdict.reason)
