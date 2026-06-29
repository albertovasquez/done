"""The worker's task string: goal + context + the structured-summary contract.
Borrowed from Hermes — the worker finishes by summarizing what it did, found,
modified, and any issues, so the parent's digest is predictable."""
from __future__ import annotations

_SUMMARY_CONTRACT = (
    "\n\nWhen done, finish with a short structured summary covering:\n"
    "1. what you did,\n"
    "2. what you found,\n"
    "3. any files you modified,\n"
    "4. any issues you hit.\n"
    "Submit that summary as your final answer."
)


def build_worker_task(goal: str, context: str) -> str:
    return f"Goal: {goal}\n\nContext:\n{context}{_SUMMARY_CONTRACT}"
