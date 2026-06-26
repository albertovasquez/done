"""ChatHandler: answer a chat_question with the USER's worker model, streamed.

The router dispatches chat_question here — the router itself never answers. The
answer is yielded in pieces so the agent emits one message_chunk per delta (live
streaming to the TUI). In mock mode (no real worker model) we cannot answer, so
we yield one honest message instead of feeding the prompt to the mock model.
"""

from __future__ import annotations

import os
from typing import Iterator

# `litellm` is imported lazily inside answer_stream() — at module scope it costs
# ~1s and this module is on the agent-startup path. Mock mode returns first.


class ChatHandler:
    def __init__(self, worker_model_id: str | None):
        # None => mock mode (no chat-capable model available)
        self._model_id = worker_model_id

    def answer_stream(self, prompt: str) -> Iterator[str]:
        """Yield the answer in pieces (one message_chunk per delta downstream).
        Mock mode yields exactly one honest piece."""
        if self._model_id is None:
            yield ("[mock mode] classified as chat_question; chat answers require "
                   "--model vibeproxy. (Routing worked: this did not run the agent.)")
            return
        import litellm  # lazy: keep the ~1s import out of startup (mock never hits this)
        stream = litellm.completion(
            model="openai/" + self._model_id,
            api_base=os.getenv("VIBEPROXY_BASE_URL", "http://localhost:8317/v1"),
            api_key=os.getenv("VIBEPROXY_API_KEY", "dummy-not-used"),
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1000,
            stream=True,
        )
        for chunk in stream:
            piece = (chunk.choices[0].delta.content or "") if chunk.choices else ""
            if piece:
                yield piece
