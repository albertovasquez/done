"""Model-bound code review: resolve a review model, run the caveman-review
prompt + content as a ONE-SHOT completion, return terse findings.

The point is independence — a model different from the one that wrote the code
catches more. Independence is the user's responsibility; this module does NOT
enforce it (no same-as-author check)."""
from __future__ import annotations

import os

from harness import config

# Verbatim copy of the caveman-review prompt (the skill we kept; this is the
# bundled, model-bound copy). Keep in sync intentionally — this is a fork.
REVIEW_PROMPT = """\
Write code review comments terse and actionable. One line per finding. \
Location, problem, fix. No throat-clearing.

Format: `L<line>: <problem>. <fix>.` — or `<file>:L<line>: ...` for multi-file diffs.
Severity prefix when mixed: `🔴 bug:` broken behavior · `🟡 risk:` fragile · \
`🔵 nit:` style/micro · `❓ q:` genuine question.
Drop: "I noticed that…", "it seems…", "you might consider…", hedging, restating \
the line, "great work". Keep: exact line numbers, exact symbol names in backticks, \
a concrete fix (not "consider refactoring"), the *why* when non-obvious.
Auto-clarity: write a normal paragraph for security findings / architectural \
disagreements, then resume terse.
Reviews only — do not write the fix, do not approve/request-changes."""

_KEYS = {
    False: ("review_model", "REVIEW_MODEL"),
    True: ("quick_review_model", "QUICK_REVIEW_MODEL"),
}


def resolve_review_model(*, quick: bool) -> str | None:
    """done.conf [harness] <key> -> <ENV> -> None (signal: propose a model)."""
    conf_key, env_key = _KEYS[quick]
    return config.harness_setting(conf_key) or os.environ.get(env_key) or None


def run_review(content: str, *, quick: bool, call_model) -> str:
    """Run the caveman-review prompt + content as one completion via call_model
    (prompt: str) -> str. quick is accepted for symmetry/logging; the prompt is
    the same. Raises ValueError on empty content."""
    if not content or not content.strip():
        raise ValueError("nothing to review")
    prompt = f"{REVIEW_PROMPT}\n\nReview the following:\n\n{content}"
    return call_model(prompt)
