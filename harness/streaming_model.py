# harness/streaming_model.py
"""StreamingLitellmModel: a LitellmModel that (1) advertises a multi-tool
registry instead of the single upstream bash tool, (2) parses tool calls by name
routing each to its registered tool, and (3) streams prose deltas to a callback
while still returning the complete-response shape upstream query() requires.

Overrides _query (both the streaming and blocking branches — neither may call
super()._query, which re-hardcodes tools=[BASH_TOOL]) and _parse_actions. query()
is inherited unchanged, so all of upstream's post-call logic (cost, FormatError
persistence) runs on the response. on_delta is None => blocking path
(mock/tests/CLI). The blocking branch duplicates upstream's AuthenticationError
hint (litellm_model.py:64-74); that duplication is intentional and pinned to
upstream v2.4.2 — verify before upgrading.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import litellm
from jinja2 import StrictUndefined, Template

from minisweagent.exceptions import FormatError
from minisweagent.models.litellm_model import LitellmModel
from minisweagent.models.utils.actions_toolcall import parse_toolcall_actions

from harness.interruptible import run_interruptible
from harness.tools.registry import build_registry


def _extract_delta(chunk) -> str:
    """The prose piece from one stream chunk; '' when the chunk carries none."""
    choices = getattr(chunk, "choices", None) or []
    if not choices:
        return ""
    delta = getattr(choices[0], "delta", None)
    return getattr(delta, "content", None) or ""


class StreamingLitellmModel(LitellmModel):
    def __init__(self, *, on_delta: Callable[[str], None] | None = None, registry=None,
                 cancel_flag=None, **kwargs):
        super().__init__(**kwargs)
        self.on_delta = on_delta   # set/cleared per run by the caller
        # cancel_flag (threading.Event | None): bound per-run by acp_agent. When
        # set mid-flight, run_interruptible aborts the blocking litellm.completion
        # call (the stalled / pre-first-token case that emit_delta can't catch,
        # since emit_delta only fires once a token arrives). None => CLI/mock =>
        # calls run inline (byte-identical to before).
        self.cancel_flag = cancel_flag
        # Fresh registry per construction — never a shared module-global.
        self.registry = registry if registry is not None else build_registry()

    def _tool_schemas(self) -> list[dict]:
        return [t.schema for t in self.registry]

    def _query(self, messages, **kwargs):
        if self.on_delta is None:
            # Blocking path. NOT super()._query — that re-hardcodes tools=[BASH_TOOL].
            # Re-issue here with the full registry so mock/CLI/non-streaming see every tool.
            # run_interruptible: ESC aborts a stalled blocking call (cancel_flag=None
            # => runs inline, unchanged).
            try:
                return run_interruptible(
                    lambda: litellm.completion(
                        model=self.config.model_name,
                        messages=messages,
                        tools=self._tool_schemas(),
                        **(self.config.model_kwargs | kwargs),
                    ),
                    self.cancel_flag,
                )
            except litellm.exceptions.AuthenticationError as e:
                e.message += " You can permanently set your API key with `mini-extra config set KEY VALUE`."
                raise
        chunks = []
        try:
            # Only the initial completion() call blocks until the first chunk; the
            # per-token emit_delta abort handles the rest of the stream. Wrap just
            # the opening call so a stall BEFORE the first token is still killable.
            stream = run_interruptible(
                lambda: litellm.completion(
                    model=self.config.model_name,
                    messages=messages,
                    tools=self._tool_schemas(),
                    stream=True,
                    **(self.config.model_kwargs | kwargs),
                ),
                self.cancel_flag,
            )
            for chunk in stream:
                chunks.append(chunk)
                piece = _extract_delta(chunk)
                if piece:
                    self.on_delta(piece)
        except litellm.exceptions.AuthenticationError as e:
            e.message += " You can permanently set your API key with `mini-extra config set KEY VALUE`."
            raise
        rebuilt = litellm.stream_chunk_builder(chunks, messages=messages)
        if rebuilt is None and not chunks:
            # Nothing emitted and reassembly produced nothing → fall back to one
            # blocking call WITH the full tool list. Clear on_delta so the recursive
            # _query takes the blocking branch (NOT super(), which re-hardcodes bash).
            saved, self.on_delta = self.on_delta, None
            try:
                return self._query(messages, **kwargs)
            finally:
                self.on_delta = saved
        return rebuilt

    def _parse_actions(self, response) -> list[dict]:
        """Parse tool calls, routing each by name to a registered tool. Unknown
        name or malformed args raise FormatError (response persisted by query())."""
        tool_calls = response.choices[0].message.tool_calls or []
        if not tool_calls:
            # Reuse upstream's "no tool calls" FormatError (with finish_reason).
            return parse_toolcall_actions(
                tool_calls,
                format_error_template=self.config.format_error_template,
                template_kwargs={"finish_reason": response.choices[0].finish_reason},
            )
        by_name = {t.name: t for t in self.registry}
        actions = []
        for tc in tool_calls:
            name = tc.function.name
            err = ""
            try:
                args = json.loads(tc.function.arguments)
            except Exception as e:
                args, err = {}, f"Error parsing arguments for tool '{name}': {e}."
            if name not in by_name:
                err += f"Unknown tool '{name}'. Available: {', '.join(by_name)}."
            if not isinstance(args, dict):
                err += f"Arguments for tool '{name}' must be a JSON object."
            if err:
                raise FormatError({
                    "role": "user",
                    "content": Template(self.config.format_error_template, undefined=StrictUndefined).render(
                        actions=[], error=err.strip(), finish_reason=response.choices[0].finish_reason),
                    "extra": {"interrupt_type": "FormatError"},
                })
            action = {"tool_name": name, "args": args, "tool_call_id": tc.id}
            if name == "bash":
                action["command"] = args.get("command", "")
            actions.append(action)
        return actions
