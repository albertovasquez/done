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


def _format_catalog(catalog: "list[skills.SkillMeta]",
                    skipped: "list[tuple[str, str]] | None" = None,
                    shadowed: "list[tuple[str, str]] | None" = None) -> str:
    """A markdown answer listing the user's skills (name + description). Skills
    whose origin is 'bundled' (the harness's curated spine) are NOT enumerated —
    they are used silently. Dropped skills (skipped) and overridden skills
    (shadowed) are listed regardless of origin, so the user still learns why a
    skill they added is unselectable or which copy is active."""
    skipped = skipped or []
    shadowed = shadowed or []
    visible = [m for m in catalog if getattr(m, "origin", "unknown") != "bundled"]
    if not visible:
        head = ("I currently have **no skills** loaded — none are bundled or "
                "configured in your skills directories.")
        lines = [head]
    else:
        n = len(visible)
        lines = [f"I have **{n} skill{'s' if n != 1 else ''}** available:", ""]
        lines += [f"- **{m.name}** — {m.description}" for m in visible]
    if skipped:
        k = len(skipped)
        lines += ["", f"⚠️ **{k} skill{'s' if k != 1 else ''} skipped** (won't load):"]
        lines += [f"- `{name}` — {reason}" for name, reason in skipped]
    if shadowed:
        s = len(shadowed)
        lines += ["", f"ℹ️ **{s} skill{'s' if s != 1 else ''} overridden** (a higher-precedence root won):"]
        lines += [f"- `{name}` — using the copy in `{root}`" for name, root in shadowed]
    return "\n".join(lines)


class ChatHandler:
    def __init__(self, worker_model_id: str | None,
                 catalog: list[tuple[str, str]] | None = None,
                 persona_block: str = "", base_block: str = "",
                 skipped: "list[tuple[str, str]] | None" = None,
                 shadowed: "list[tuple[str, str]] | None" = None):
        # None => mock mode (no chat-capable model available)
        self._model_id = worker_model_id
        # The skill catalog (name, description) — used to answer capability
        # questions from data instead of the model. Empty/None => not available.
        self._catalog = catalog or []
        # Skills dropped during catalog load (dir_name, reason) — surfaced in the
        # capability answer so the user learns why a skill is unselectable. [] => none.
        self._skipped = skipped or []
        # Skills overridden across roots (name, winning_root) — surfaced so a name
        # clash is visible (which copy is active). [] => none.
        self._shadowed = shadowed or []
        # Persona context (identity trio). Prepended as a system message on every
        # turn when non-empty; "" => no system message (byte-identical to before).
        self._persona_block = persona_block
        # Authored base system prompt (rendered by base_prompt.render_base_prompt).
        # Combined with persona_block as a single system message (base first).
        # "" => no additional system content (preserves byte-identical no-op).
        self._base_block = base_block

    def answer_stream(self, prompt: str,
                      history: list[dict] | None = None) -> Iterator[str]:
        """Yield the answer in pieces (one message_chunk per delta downstream).
        `history` (plain {role, content} turns) is prepended for context.
        A capability question is answered from the catalog (one piece, no model);
        mock mode otherwise yields one honest piece."""
        if is_capability_question(prompt):
            yield _format_catalog(self._catalog, self._skipped, self._shadowed)
            return
        if self._model_id is None:
            yield ("[mock mode] classified as chat_question; chat answers require "
                   "--model vibeproxy. (Routing worked: this did not run the agent.)")
            return
        import litellm  # lazy: keep the ~1s import out of startup (mock never hits this)
        from harness import vibeproxy
        system_content = self._base_block + self._persona_block
        stream = litellm.completion(
            model=vibeproxy.model_id(self._model_id),
            **vibeproxy.completion_kwargs(),
            messages=(([{"role": "system", "content": system_content}]
                       if system_content else [])
                      + (history or []) + [{"role": "user", "content": prompt}]),
            max_tokens=1000,
            stream=True,
        )
        for chunk in stream:
            piece = (chunk.choices[0].delta.content or "") if chunk.choices else ""
            if piece:
                yield piece
