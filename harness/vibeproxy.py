"""Single source of truth for VibeProxy connection settings.

VibeProxy is the OpenAI-compatible proxy the harness talks to. Its base URL, the
(unused) dummy API key, and the default worker model were copy-pasted across
five call sites (router, chat_handler, acp_main, run_traced, plus error echoes);
this module collapses them to one definition so a change lands in one place.

NOTE: this module must NOT import litellm — several callers import it lazily on
purpose (litellm costs ~1s at import and they sit on the startup path). This
module only reads env and shapes strings/dicts; callers own the litellm call.
"""

from __future__ import annotations

import os

# Defaults live here, ONCE. Process env overrides each.
_DEFAULT_BASE_URL = "http://localhost:8317/v1"
_DEFAULT_API_KEY = "dummy-not-used"        # VibeProxy ignores it; litellm requires a value
DEFAULT_MODEL = "gpt-5.4"                   # default WORKER model (not the router model)


def base_url() -> str:
    return os.getenv("VIBEPROXY_BASE_URL", _DEFAULT_BASE_URL)


def api_key() -> str:
    return os.getenv("VIBEPROXY_API_KEY", _DEFAULT_API_KEY)


def default_model() -> str:
    """The configured worker model (env override of DEFAULT_MODEL)."""
    return os.getenv("VIBEPROXY_MODEL", DEFAULT_MODEL)


def model_id(name: str) -> str:
    """litellm's OpenAI-compatible model id: the 'openai/' provider prefix."""
    return "openai/" + name


def completion_kwargs() -> dict:
    """Connection kwargs for a raw `litellm.completion(...)` call (router,
    chat_handler). Merge with model/messages/etc. at the call site."""
    return {"api_base": base_url(), "api_key": api_key()}


def model_kwargs() -> dict:
    """The `model_kwargs` dict for a LitellmModel / StreamingLitellmModel
    (acp_main, run_traced). LitellmModelConfig has no top-level api_base/api_key
    fields, so they live here."""
    return {"api_base": base_url(), "api_key": api_key()}
