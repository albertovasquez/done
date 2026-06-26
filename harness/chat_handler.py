"""ChatHandler: answer a chat_question with the USER's worker model, streamed.

The router dispatches chat_question here — the router itself never answers. The
answer is yielded in pieces so the agent emits one message_chunk per delta (live
streaming to the TUI). In mock mode (no real worker model) we cannot answer, so
we yield one honest message instead of feeding the prompt to the mock model.

One class of chat_question we CAN answer without a model: "what skills do we
have?" / "what can you do?". The router already holds the skill catalog when it
classifies, so we hand that same catalog here and answer such questions
deterministically from it — a real, full list rather than a model's guess (and
it works in mock mode too).
"""

from __future__ import annotations

import os
import re
from typing import Iterator

# `litellm` is imported lazily inside answer_stream() — at module scope it costs
# ~1s and this module is on the agent-startup path. Mock mode returns first.

# A capability/meta question is about the agent's OWN skills/abilities, not a
# request to USE one. Match on "skill(s)" or an explicit "what can you do"-style
# phrasing; the `\bskills?\b` anchor keeps "debug this skillfully" from matching.
_SKILL_WORD = re.compile(r"\bskills?\b", re.IGNORECASE)
_ABILITY_Q = re.compile(
    r"what (can|do) you (do|offer)|what are your (capabilities|abilities)",
    re.IGNORECASE)


def is_capability_question(prompt: str) -> bool:
    """True when `prompt` asks what skills/abilities the agent has (answerable
    from the catalog, no model). Narrow by design: a false negative just falls
    through to the model; a false positive would hijack legitimate chat."""
    return bool(_ABILITY_Q.search(prompt) or _SKILL_WORD.search(prompt))


def _format_catalog(catalog: list[tuple[str, str]]) -> str:
    """A markdown answer listing every skill (name + description)."""
    if not catalog:
        return ("I currently have **no skills** loaded — none are bundled or "
                "configured in your skills directories.")
    n = len(catalog)
    lines = [f"I have **{n} skill{'s' if n != 1 else ''}** available:", ""]
    lines += [f"- **{name}** — {desc}" for name, desc in catalog]
    return "\n".join(lines)


class ChatHandler:
    def __init__(self, worker_model_id: str | None,
                 catalog: list[tuple[str, str]] | None = None,
                 persona_block: str = ""):
        # None => mock mode (no chat-capable model available)
        self._model_id = worker_model_id
        # The skill catalog (name, description) — used to answer capability
        # questions from data instead of the model. Empty/None => not available.
        self._catalog = catalog or []
        # Persona context (identity trio). Prepended as a system message on every
        # turn when non-empty; "" => no system message (byte-identical to before).
        self._persona_block = persona_block

    def answer_stream(self, prompt: str,
                      history: list[dict] | None = None) -> Iterator[str]:
        """Yield the answer in pieces (one message_chunk per delta downstream).
        `history` (plain {role, content} turns) is prepended for context.
        A capability question is answered from the catalog (one piece, no model);
        mock mode otherwise yields one honest piece."""
        if is_capability_question(prompt):
            yield _format_catalog(self._catalog)
            return
        if self._model_id is None:
            yield ("[mock mode] classified as chat_question; chat answers require "
                   "--model vibeproxy. (Routing worked: this did not run the agent.)")
            return
        import litellm  # lazy: keep the ~1s import out of startup (mock never hits this)
        stream = litellm.completion(
            model="openai/" + self._model_id,
            api_base=os.getenv("VIBEPROXY_BASE_URL", "http://localhost:8317/v1"),
            api_key=os.getenv("VIBEPROXY_API_KEY", "dummy-not-used"),
            messages=(([{"role": "system", "content": self._persona_block}]
                       if self._persona_block else [])
                      + (history or []) + [{"role": "user", "content": prompt}]),
            max_tokens=1000,
            stream=True,
        )
        for chunk in stream:
            piece = (chunk.choices[0].delta.content or "") if chunk.choices else ""
            if piece:
                yield piece
