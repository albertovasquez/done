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
import threading
from typing import Iterator

from harness.interruptible import run_interruptible

# `litellm` is imported lazily inside answer_stream() — at module scope it costs
# ~1s and this module is on the agent-startup path. Mock mode returns first.

# A capability/meta question is about the agent's OWN skills/abilities, not a
# request to USE one. Match on "skill(s)" or an explicit "what can you do"-style
# phrasing; the `\bskills?\b` anchor keeps "debug this skillfully" from matching.
_SKILL_WORD = re.compile(r"\bskills?\b", re.IGNORECASE)
_ABILITY_Q = re.compile(
    r"what (can|do) you (do|offer)|what are your (capabilities|abilities)",
    re.IGNORECASE)

# A tools/commands question is about the agent's OWN tool surface, answerable
# from the live registry (no model). POSSESSIVE-ONLY by design: it must be
# self-directed ("what tools do YOU have", "your tools", "tools you can use") so
# a request to BUILD or USE a tool ("write a tool to parse logs", "what tools
# should I use in Rust") falls through to the model untouched. A false negative
# is harmless (status quo); a false positive would hijack real work — so this
# biases hard to precision.
_TOOLS_Q = re.compile(
    r"\b(?:what|which)\s+(?:tools?|commands?)\s+(?:do|can)\s+you\b"
    r"|\byour\s+(?:tools?|commands?)\b"
    r"|\b(?:tools?|commands?)\s+you\s+(?:have|can|use)\b",
    re.IGNORECASE)


def is_tools_question(prompt: str) -> bool:
    """True when `prompt` asks what tools/commands the agent itself has."""
    return bool(_TOOLS_Q.search(prompt))


def is_capability_question(prompt: str) -> bool:
    """True when `prompt` asks what skills/tools/abilities the agent has
    (answerable deterministically, no model). Narrow by design: a false negative
    just falls through to the model; a false positive would hijack legitimate
    chat."""
    return bool(_ABILITY_Q.search(prompt) or _SKILL_WORD.search(prompt)
                or is_tools_question(prompt))


def _deterministic_skills_list_enabled() -> bool:
    """Whether the legacy DETERMINISTIC skills-list path is enabled. OFF by
    default; set HARNESS_DETERMINISTIC_SKILLS_LIST=1 (or true) to turn it on.

    WHY OFF BY DEFAULT: `is_capability_question` is a bare keyword match — it
    fires on the word "skill(s)" alone, so it cannot tell "list my skills"
    (enumeration) from a real question that merely mentions skills ("are these
    descriptions sent to the model's context?", "how are skills loaded?"). When
    it fired, it short-circuited the model and dumped the catalog, giving the
    wrong answer to genuine questions. It also acted as a hidden SECOND
    classifier downstream of the LLM router, which is brittle as caching /
    routing optimizations are added.

    With the flag OFF, every skill question goes to the model, which already has
    the full skills MENU (names + descriptions) in its system prompt via
    `compose_menu` -> `base_block`, so it answers from real intent. The
    deterministic path (catalog dump + skipped/shadowed surfacing) is preserved
    but dormant; whether to improve or remove it is a deferred decision.
    """
    return os.getenv("HARNESS_DETERMINISTIC_SKILLS_LIST", "").lower() in ("1", "true")


# User-facing header + path hint per origin (bundled is hidden, so it has none).
# 'unknown' has no path hint (it's a root we couldn't classify).
_ORIGIN_HEADERS = {
    "global": ("Global skills", "~/.claude"),
    "user": ("User skills", "~/.config/harness"),
    "project": ("Project skills", "this repo"),
    "unknown": ("Other skills", ""),
}


def _format_catalog(catalog: "list[skills.SkillMeta]",
                    skipped: "list[tuple[str, str]] | None" = None,
                    shadowed: "list[tuple[str, str]] | None" = None) -> str:
    """A markdown answer listing the user's skills, GROUPED BY ORIGIN
    (global/user/project) so the user sees where each skill comes from. Skills
    whose origin is 'bundled' (the harness's curated spine) are NOT enumerated —
    they are used silently. Empty origin groups are omitted. Dropped skills
    (skipped) and overridden skills (shadowed) are listed regardless of origin,
    so the user still learns why a skill they added is unselectable or which copy
    is active."""
    from harness import skills as _skills
    skipped = skipped or []
    shadowed = shadowed or []
    visible = [m for m in catalog if getattr(m, "origin", "unknown") != "bundled"]
    if not visible:
        head = ("I currently have **no skills** loaded — none are configured "
                "in your skills directories.")
        lines = [head]
    else:
        n = len(visible)
        lines = [f"I have **{n} skill{'s' if n != 1 else ''}** available:"]
        for origin, group in _skills.group_by_origin(visible):
            title, hint = _ORIGIN_HEADERS.get(origin, (origin.title() + " skills", ""))
            header = f"### {title}  ({hint})" if hint else f"### {title}"
            lines += ["", header]
            lines += [f"- **{m.name}** — {m.description}" for m in group]
    if skipped:
        k = len(skipped)
        lines += ["", f"⚠️ **{k} skill{'s' if k != 1 else ''} skipped** (won't load):"]
        lines += [f"- `{name}` — {reason}" for name, reason in skipped]
    if shadowed:
        s = len(shadowed)
        lines += ["", f"ℹ️ **{s} skill{'s' if s != 1 else ''} overridden** (a higher-precedence root won):"]
        lines += [f"- `{name}` — using the copy in `{root}`" for name, root in shadowed]
    return "\n".join(lines)


def _format_tools(catalog: list[tuple[str, str]]) -> str:
    """A markdown answer describing the agent's full capability surface: the live
    tools (from the registry), the loaded skills (the same catalog), and the
    `plan` checklist command. Read from build_registry() so this answer cannot
    drift from the agent's real tools."""
    from harness.tools.registry import build_registry

    tools = [
        (t.name, t.schema.get("function", {}).get("description", ""))
        for t in build_registry()
    ]
    nt = len(tools)
    lines = [f"I have **{nt} tool{'s' if nt != 1 else ''}** available:", ""]
    lines += [f"- **{name}** — {desc}" for name, desc in tools]
    lines += ["", _format_catalog(catalog), ""]
    lines += ["Plus a `plan` command that drives an on-screen checklist for "
              "multi-step work."]
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
                      history: list[dict] | None = None,
                      cancel_flag: threading.Event | None = None) -> Iterator[str]:
        """Yield the answer in pieces (one message_chunk per delta downstream).
        `history` (plain {role, content} turns) is prepended for context.

        `cancel_flag` (threading.Event | None): when set, ESC aborts the answer.
        Two checkpoints, because the chat path makes its OWN litellm.completion
        call (NOT via StreamingLitellmModel): (1) run_interruptible wraps the
        blocking `completion()` open so a stalled / pre-first-token call is
        killable; (2) a per-piece check in the loop aborts once tokens flow.
        None => CLI/mock => runs inline, unchanged.

        A self-directed TOOLS question is always answered deterministically from
        the live registry (`_format_tools`); its regex is possessive-only, so it
        can't hijack a build-a-tool request. SKILL questions, by contrast, go to
        the model by default — the legacy catalog-dump path is gated behind
        HARNESS_DETERMINISTIC_SKILLS_LIST (OFF by default; see
        `_deterministic_skills_list_enabled`)."""
        # Tools first: a tools question is a SUBSET of capability, so the gated
        # skills branch below would otherwise catch it (when the flag is on) and
        # drop the tools. The tools answer is always on (precision-safe regex).
        if is_tools_question(prompt):
            yield _format_tools(self._catalog)
            return
        if _deterministic_skills_list_enabled() and is_capability_question(prompt):
            yield _format_catalog(self._catalog, self._skipped, self._shadowed)
            return
        if self._model_id is None:
            yield ("[mock mode] classified as chat_question; chat answers require "
                   "--model vibeproxy. (Routing worked: this did not run the agent.)")
            return
        import litellm  # lazy: keep the ~1s import out of startup (mock never hits this)
        from harness import vibeproxy
        from minisweagent.exceptions import UserInterruption
        system_content = self._base_block + self._persona_block
        # Wrap the blocking completion() open: a stalled call that never yields a
        # first token is aborted here (the per-piece check below can't reach it).
        stream = run_interruptible(
            lambda: litellm.completion(
                model=vibeproxy.model_id(self._model_id),
                **vibeproxy.completion_kwargs(),
                messages=(([{"role": "system", "content": system_content}]
                           if system_content else [])
                          + (history or []) + [{"role": "user", "content": prompt}]),
                max_tokens=1000,
                stream=True,
            ),
            cancel_flag,
        )
        for chunk in stream:
            if cancel_flag is not None and cancel_flag.is_set():
                raise UserInterruption({
                    "role": "exit", "content": "Cancelled by user.",
                    "extra": {"exit_status": "cancelled", "submission": ""}})
            piece = (chunk.choices[0].delta.content or "") if chunk.choices else ""
            if piece:
                yield piece
