"""One-shot goal reviewer: a no-tools LiteLLM completion that judges whether a
goal is met. NEVER TracingAgent.query()/StreamingLitellmModel (which advertise
tools and raise FormatError on prose). Mirrors harness/tools/review.py's direct
litellm pattern; the completion caller is injectable for tests."""
from __future__ import annotations

from harness.goal_gate import Verdict

_PROMPT = """You are reviewing whether a stated GOAL has been achieved, given the
work transcript below. Answer on the FIRST line exactly `met: yes` or `met: no`,
then one short line explaining why.

GOAL:
{goal}

TRANSCRIPT (most recent work):
{transcript}
"""


def _default_caller(model: str):
    import litellm  # noqa: PLC0415
    from harness import vibeproxy  # noqa: PLC0415
    mid = vibeproxy.model_id(model)
    kwargs = vibeproxy.completion_kwargs()

    def call(prompt: str) -> str:
        resp = litellm.completion(
            model=mid, messages=[{"role": "user", "content": prompt}], **kwargs)
        return resp.choices[0].message.content or ""
    return call


def review_goal(goal: str, transcript_text: str, model: str, *, caller=None) -> Verdict:
    caller = caller or _default_caller(model)
    prompt = _PROMPT.format(goal=goal, transcript=transcript_text)
    raw = (caller(prompt) or "").strip()
    lines = raw.splitlines()
    first = lines[0].lower().replace(" ", "") if lines else ""
    rest = "\n".join(lines[1:]).strip() or raw
    if first.startswith("met:yes"):
        return Verdict(met=True, reason=rest)
    if first.startswith("met:no"):
        return Verdict(met=False, reason=rest)
    return Verdict(met=False, reason=raw)   # unparseable → keep working (bounded by retry cap)
