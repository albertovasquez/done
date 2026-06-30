"""ReviewTool — the agent calls this to run a code review on an independent
model. Mirrors the load_skill/load_memory tool shape. The review runs as a
one-shot completion (not a full agent)."""
from __future__ import annotations

from harness import review

REVIEW_TOOL = {"type": "function", "function": {
    "name": "review",
    "description": (
        "Review code/diff on a separate model (independent review catches more "
        "than self-review). Pass the content to review; quick=true uses the "
        "faster quick-review model."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "the diff/code to review"},
            "quick": {"type": "boolean", "description": "use the quick-review model"},
            "model": {"type": "string", "description": "explicit model override"},
        },
        "required": ["content"],
    },
}}


def _build_call_model(model_name: str):
    """Return a (prompt: str) -> str callable backed by litellm/vibeproxy.

    Lazy-imports litellm so the module is importable in CI without it installed.
    """
    import litellm  # noqa: PLC0415
    from harness import vibeproxy  # noqa: PLC0415

    model = vibeproxy.model_id(model_name)
    kwargs = vibeproxy.completion_kwargs()

    def call_model(prompt: str) -> str:
        resp = litellm.completion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            **kwargs,
        )
        return resp.choices[0].message.content or ""

    return call_model


class ReviewTool:
    name = "review"
    schema = REVIEW_TOOL

    def display_label(self, args: dict) -> str:
        return f"review {'(quick)' if args.get('quick') else ''}".strip()

    def execute(self, args: dict, env) -> dict:
        content = args.get("content", "")
        quick = bool(args.get("quick", False))
        model_name = args.get("model") or review.resolve_review_model(quick=quick)
        if not model_name:
            key = "quick_review_model" if quick else "review_model"
            return {
                "output": (
                    f"no review model: set [harness] {key} in done.conf or pass model="
                ),
                "returncode": 1,
                "exception_info": None,
            }
        try:
            findings = review.run_review(
                content, quick=quick, call_model=_build_call_model(model_name)
            )
        except ValueError as e:
            return {"output": str(e), "returncode": 1, "exception_info": None}
        return {"output": findings, "returncode": 0, "exception_info": None}
