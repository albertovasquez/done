"""ChatHandler: answer a chat_question with the USER's worker model (one-shot).

The router dispatches chat_question here — the router itself never answers. In
mock mode (no real worker model) we cannot answer, so we print an honest message
instead of feeding the prompt to the tool-call mock model.
"""

from __future__ import annotations

import os

# `litellm` is imported lazily inside answer() — at module scope it costs ~1s and
# this module is on the agent-startup path. Mock mode returns before needing it.


class ChatHandler:
    def __init__(self, worker_model_id: str | None):
        # None => mock mode (no chat-capable model available)
        self._model_id = worker_model_id

    def answer(self, prompt: str) -> str:
        if self._model_id is None:
            return ("[mock mode] classified as chat_question; chat answers require "
                    "--model vibeproxy. (Routing worked: this did not run the agent.)")
        import litellm  # lazy: keep the ~1s import out of startup (mock mode never hits this)
        resp = litellm.completion(
            model="openai/" + self._model_id,
            api_base=os.getenv("VIBEPROXY_BASE_URL", "http://localhost:8317/v1"),
            api_key=os.getenv("VIBEPROXY_API_KEY", "dummy-not-used"),
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1000,
        )
        return resp.choices[0].message.content or ""
