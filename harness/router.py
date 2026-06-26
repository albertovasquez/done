"""Router: classify a request with a CHEAP model and decide how to dispatch it.

The router classifies and dispatches — it does NOT answer requests and does NOT
pick the worker model. It has its own fixed cheap model (gpt-5.4-mini) via the
injected `complete(system, user) -> str` wrapper (NOT LitellmModel.query, which
is tool-call shaped). Parse failures / unknown types degrade to 'ambiguous' so an
unclear request never silently runs the agent.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Callable

# NOTE: `litellm` is imported lazily inside complete() — importing it at module
# scope costs ~1s and pulls the whole agent-startup path down (router is imported
# eagerly by acp_main). It is only needed when a real classification call is made
# (never for --model mock, never when tests inject a stub).

TASK_TYPES = ["chat_question", "code_explain", "code_fix", "code_feature",
              "code_refactor", "ops_task", "ambiguous"]

ROUTER_MODEL = "openai/gpt-5.4-mini"


@dataclass
class Classification:
    task_type: str
    skills: list[str] = field(default_factory=list)
    confidence: float = 0.0
    reasoning: str = ""
    suggested_model: str | None = None
    needs_clarification: bool = False
    clarifying_question: str | None = None


def complete(system: str, user: str) -> str:
    """Thin cheap-model completion for classification. Used by the CLI; tests
    inject a stub instead. Plain text in, text out — no tool calls."""
    import litellm  # lazy: keep the ~1s import out of startup (see module note)
    resp = litellm.completion(
        model=ROUTER_MODEL,
        api_base=os.getenv("VIBEPROXY_BASE_URL", "http://localhost:8317/v1"),
        api_key=os.getenv("VIBEPROXY_API_KEY", "dummy-not-used"),
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
        max_tokens=300,
    )
    return resp.choices[0].message.content or ""


def _system_prompt(catalog: list[tuple[str, str]]) -> str:
    return (
        "You are a fast TRIAGE router for a coding agent harness. Read the user's "
        "request and classify it. You do NOT answer or chat; you only classify. "
        "Respond with ONLY a JSON object, no prose, with keys: "
        f"task_type (one of {TASK_TYPES}), skills (list of skill NAMES from the "
        "catalog that apply, may be empty), confidence (0.0-1.0), "
        "suggested_model (a model name or null; advisory only), "
        "reasoning (one short sentence).\n\nSkill catalog (name: description):\n"
        + "\n".join(f"  {n}: {d}" for n, d in catalog)
    )


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.removeprefix("```json").removeprefix("```").strip()
        if t.endswith("```"):
            t = t[: -3].strip()
    return t


class Router:
    def __init__(self, complete_fn: Callable[[str, str], str], *,
                 catalog: list[tuple[str, str]],
                 confidence_threshold: float = 0.6):
        self._complete = complete_fn
        self._catalog = catalog
        self._catalog_names = {n for n, _ in catalog}
        self._threshold = confidence_threshold

    def classify(self, prompt: str) -> Classification:
        raw = self._complete(_system_prompt(self._catalog), prompt)
        try:
            data = json.loads(_strip_fences(raw))
            if not isinstance(data, dict):
                raise ValueError("not an object")
        except Exception:
            return Classification(
                task_type="ambiguous", confidence=0.0, needs_clarification=True,
                reasoning="router output was not parseable JSON",
                clarifying_question="I couldn't interpret that. What concrete task "
                                    "should I do?")
        task_type = data.get("task_type", "ambiguous")
        if task_type not in TASK_TYPES:
            task_type = "ambiguous"
        raw_skills = data.get("skills")
        raw_skills = raw_skills if isinstance(raw_skills, list) else []  # a scalar/str isn't a skill list
        skills = [s for s in raw_skills if s in self._catalog_names]
        try:
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        reasoning = str(data.get("reasoning") or "")  # `or ""` so a null reasoning isn't "None"
        suggested = data.get("suggested_model") or None
        needs = confidence < self._threshold or task_type == "ambiguous"
        question = None
        if needs:
            question = (f"That request is unclear ({reasoning or 'low confidence'}). "
                        "What concrete task should I do?")
        return Classification(task_type=task_type, skills=skills, confidence=confidence,
                              reasoning=reasoning, suggested_model=suggested,
                              needs_clarification=needs, clarifying_question=question)
