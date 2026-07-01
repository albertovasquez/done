"""Router: classify a request with a CHEAP model and decide how to dispatch it.

The router classifies and dispatches — it does NOT answer requests and does NOT
pick the worker model. It has its own fixed cheap model (gpt-5.4-mini) via the
injected `complete(system, user) -> str` wrapper (NOT LitellmModel.query, which
is tool-call shaped). Parse failures / unknown types degrade to 'ambiguous' so an
unclear request never silently runs the agent.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Callable

from harness.transcript import router_preamble

logger = logging.getLogger("harness.router")

# NOTE: `litellm` is imported lazily inside complete() — importing it at module
# scope costs ~1s and pulls the whole agent-startup path down (router is imported
# eagerly by acp_main). It is only needed when a real classification call is made
# (never for --model mock, never when tests inject a stub).

TASK_TYPES = ["chat_question", "code_explain", "code_fix", "code_feature",
              "code_refactor", "ops_task", "ambiguous"]

ROUTER_MODEL = "openai/gpt-5.4-mini"

# Fallback cheap model used when the primary router model is rate-limited /
# cooling down (e.g. a personal ChatGPT/Codex account hits its limit). Defaults to
# a model served by a DIFFERENT provider so one provider's cooldown can't brick
# the router. Override either via env.
ROUTER_FALLBACK_MODEL = "openai/claude-haiku-4-5-20251001"


def _router_models() -> list[str]:
    """Ordered list of router models to try: env override (or default) first,
    then the fallback. De-duplicated, env-overridable. The 'openai/' prefix is
    litellm's provider tag for the OpenAI-compatible proxy — it does NOT mean the
    model is an OpenAI model (the proxy serves Claude/etc. under the same tag)."""
    primary = os.getenv("ROUTER_MODEL", ROUTER_MODEL)
    fallback = os.getenv("ROUTER_FALLBACK_MODEL", ROUTER_FALLBACK_MODEL)
    out = []
    for m in (primary, fallback):
        if m and m not in out:
            out.append(m)
    return out


@dataclass
class Classification:
    task_type: str
    skills: list[str] = field(default_factory=list)
    confidence: float = 0.0
    reasoning: str = ""
    suggested_model: str | None = None
    needs_clarification: bool = False
    clarifying_question: str | None = None
    options: list[tuple[str, str]] = field(default_factory=list)  # (title, rationale)


def _is_rate_limit(exc: Exception) -> bool:
    """True if the exception is a provider rate-limit / cooldown (so we should try
    the next model rather than give up). Matches litellm.RateLimitError by type
    name and the CLIProxyAPI 'cooling down' / 'model_cooldown' message, without a
    hard litellm import at module scope."""
    name = type(exc).__name__
    msg = str(exc).lower()
    return ("ratelimit" in name.lower()
            or "cooling down" in msg or "model_cooldown" in msg
            or "rate limit" in msg or " 429" in msg or "status 429" in msg)


def _is_unknown_model(exc: Exception) -> bool:
    """True if the proxy rejected the model id itself (e.g. a stale ROUTER_MODEL
    alias like `qwen` after PR #260 removed it → CLIProxyAPI 502 'unknown provider
    for model qwen'). Retrying the SAME dead id is pointless, but the fallback slot
    holds a DIFFERENT id, so we should try it rather than brick the whole turn."""
    msg = str(exc).lower()
    return "unknown provider for model" in msg or "unknown model" in msg


def _should_try_next(exc: Exception) -> bool:
    """A model-specific failure (this id is throttled or unknown) that a different
    fallback id might survive — as opposed to a malformed-request error that would
    fail identically on every id and must propagate."""
    return _is_rate_limit(exc) or _is_unknown_model(exc)


def complete(system: str, user: str) -> str:
    """Thin cheap-model completion for classification. Used by the CLI; tests
    inject a stub instead. Plain text in, text out — no tool calls.

    Tries the router models in order (primary, then fallback). If a model is
    rate-limited / cooling down OR its id is unknown to the proxy (a stale alias),
    falls through to the next so one dead or throttled id does not brick every
    turn. Malformed-request errors (which would fail identically on any id)
    propagate."""
    import litellm  # lazy: keep the ~1s import out of startup (see module note)
    from harness import vibeproxy
    models = _router_models()
    last_exc: Exception | None = None
    for i, model in enumerate(models):
        try:
            resp = litellm.completion(
                model=model,
                **vibeproxy.completion_kwargs(),
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user}],
                max_tokens=300,
            )
            return resp.choices[0].message.content or ""
        except Exception as e:
            last_exc = e
            if _should_try_next(e) and i < len(models) - 1:
                logger.warning("router model %s failed (%s); falling back to %s",
                               model, type(e).__name__, models[i + 1])
                continue
            raise
    raise last_exc        # pragma: no cover - loop always returns or raises above


def _system_prompt(catalog: "list[skills.SkillMeta]") -> str:
    return (
        "You are a fast TRIAGE router for a coding agent harness. Read the user's "
        "request and classify it. You do NOT answer or chat; you only classify. "
        "The agent runs IN a real project directory (the user's current working "
        "directory) and CAN inspect it — list files, read source, run commands. "
        "So a question about THIS project, code, app, repo, or directory (e.g. "
        "\"what kind of application is this?\", \"how does X work?\", \"what's the "
        "tech stack?\") is NOT ambiguous: classify it as code_explain and let the "
        "agent look. "
        "But a general-knowledge or conceptual question with NO project referent — "
        "one that could be answered without looking at this repo (e.g. \"how does "
        "OAuth work?\", \"what is a monad?\", \"explain TCP\") — is NOT code_explain: "
        "classify it as chat_question so the persona answers directly, instead of "
        "spinning up a full agent turn. Route \"how does X work?\" to code_explain "
        "only when X plausibly names something in THIS project (a file, module, "
        "feature, or the app itself). "
        "Within ops_task, distinguish OBSERVE-intent (\"check\", \"is X working\", "
        "\"show status\", \"did Y fire\", \"is the cron firing\") from FIX-intent (a "
        "reported failure, error, or \"X is broken\"). For an observe-only request, "
        "do NOT attach debugging skills (e.g. systematic-debugging) — only attach "
        "them when the user reports a failing behavior. "
        "Creating a PERSONA or AGENT (\"create a persona named Robbie\", \"make a "
        "new agent\", \"add an agent called X\") is a real ACTION the agent performs "
        "with its create_persona tool — it is NOT a scheduled cron job and NOT an "
        "observe task. Classify it as code_feature so the agent can act; do NOT "
        "route persona/agent creation to create-job or ops_task. "
        "A greeting or social/conversational message (\"hi\", \"hello\", \"hey\", "
        "\"good morning\", \"how are you\", \"thanks\", \"what can you do\") is NOT "
        "ambiguous: classify it as chat_question so the agent can respond in "
        "character. Do NOT mark a friendly greeting as ambiguous — the agent has a "
        "persona and should answer warmly. "
        "Reserve 'ambiguous' ONLY for requests that name no task, give no project "
        "referent the agent could investigate, AND are not a greeting or social "
        "message. "
        "Respond with ONLY a JSON object, no prose, with keys: "
        f"task_type (one of {TASK_TYPES}), skills (list of skill NAMES from the "
        "catalog that apply, may be empty), confidence (0.0-1.0), "
        "suggested_model (a model name or null; advisory only), "
        "reasoning (one short sentence). When the request is ambiguous or "
        "low-confidence, ALSO return options: a list of 2-4 objects {title, "
        "rationale}, each a concrete interpretation the agent could act on "
        "(title = the rephrased task, rationale = one short why). Omit options "
        "or use [] when the request is clear."
        "\n\nSkill catalog (name: description):\n"
        + "\n".join(f"  {m.name}: {m.description}"
                    for m in catalog if m.model_invocable)
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
                 catalog: "list[skills.SkillMeta]",
                 confidence_threshold: float = 0.6):
        self._complete = complete_fn
        self._catalog = catalog
        # Only model-invocable skills are selectable by the router — a
        # disable-model-invocation skill must never be auto-picked, even if the
        # cheap model names it.
        self._catalog_names = {m.name for m in catalog if m.model_invocable}
        self._threshold = confidence_threshold

    @property
    def catalog(self) -> list[tuple[str, str]]:
        """The (name, description) skill catalog the router classifies against.
        Exposed so the chat path can answer capability questions from it."""
        return self._catalog

    def classify(self, prompt: str, history: list[dict] | None = None) -> Classification:
        user = prompt
        if history:
            preamble = router_preamble(history)
            if preamble:
                user = ("Recent context (for reference only):\n" + preamble +
                        "\n\nClassify THIS request: " + prompt)
        raw = self._complete(_system_prompt(self._catalog), user)
        try:
            data = json.loads(_strip_fences(raw))
            if not isinstance(data, dict):
                raise ValueError("not an object")
        except Exception as e:
            # The router is best-effort triage, NOT a hard gate. When the cheap
            # classifier model is unavailable or returns garbage (e.g. a chatty
            # fallback model that ignores "JSON only", or every cheap provider
            # cooling down), DON'T refuse the turn — degrade to the worker: route
            # to chat_question so the active persona (the user's real model)
            # handles the raw message in character. Log the reason + a bounded
            # preview so an incident is diagnosable.
            logger.warning("router classification unparseable (%s); routing to "
                           "worker as chat_question; raw=%r", e, raw[:200])
            return Classification(
                task_type="chat_question", confidence=0.0,
                needs_clarification=False,
                reasoning="router output was not parseable; degraded to worker")
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
        raw_opts = data.get("options")
        raw_opts = raw_opts if isinstance(raw_opts, list) else []   # scalar/str isn't a list
        options = [(str(o["title"]), str(o.get("rationale", "")))
                   for o in raw_opts
                   if isinstance(o, dict) and o.get("title")]
        needs = confidence < self._threshold or task_type == "ambiguous"
        question = None
        if needs:
            question = (f"That request is unclear ({reasoning or 'low confidence'}). "
                        "What concrete task should I do?")
        return Classification(task_type=task_type, skills=skills, confidence=confidence,
                              reasoning=reasoning, suggested_model=suggested,
                              needs_clarification=needs, clarifying_question=question,
                              options=options)
