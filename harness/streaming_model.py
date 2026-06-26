# harness/streaming_model.py
"""StreamingLitellmModel: a LitellmModel that streams prose deltas to a callback
while still returning the complete-response shape upstream query() requires.

Overrides ONLY _query. query() is inherited unchanged, so all of upstream's
post-call logic (tool-call parsing, cost, FormatError persistence) runs on the
response rebuilt by litellm.stream_chunk_builder. on_delta is None => blocking
path (mock/tests/CLI), byte-identical to upstream.
"""

from __future__ import annotations

from collections.abc import Callable

import litellm

from minisweagent.models.litellm_model import LitellmModel
from minisweagent.models.utils.actions_toolcall import BASH_TOOL


def _extract_delta(chunk) -> str:
    """The prose piece from one stream chunk; '' when the chunk carries none."""
    choices = getattr(chunk, "choices", None) or []
    if not choices:
        return ""
    delta = getattr(choices[0], "delta", None)
    return getattr(delta, "content", None) or ""


class StreamingLitellmModel(LitellmModel):
    def __init__(self, *, on_delta: Callable[[str], None] | None = None, **kwargs):
        super().__init__(**kwargs)
        self.on_delta = on_delta   # set/cleared per run by the caller

    def _query(self, messages, **kwargs):
        if self.on_delta is None:
            return super()._query(messages, **kwargs)   # blocking path
        chunks = []
        try:
            stream = litellm.completion(
                model=self.config.model_name,
                messages=messages,
                tools=[BASH_TOOL],
                stream=True,
                **(self.config.model_kwargs | kwargs),
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
            # nothing was emitted and reassembly produced nothing → safe to fall
            # back to one blocking call (no discarded generation shown to the user).
            return super()._query(messages, **kwargs)
        return rebuilt
