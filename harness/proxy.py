"""Single source of truth for CLIProxyAPI/proxy connection settings.

The proxy seam (CLIProxyAPI via VibeProxy or similar OpenAI-compatible proxy)
provides base URL, API key, and default worker model. These were copy-pasted
across five call sites (router, chat_handler, acp_main, run_traced, plus error
echoes); this module collapses them to one definition so a change lands in one
place.

NOTE: this module must NOT import litellm — several callers import it lazily on
purpose (litellm costs ~1s at import and they sit on the startup path). This
module only reads env plus the provisioned client-key file and shapes
strings/dicts; callers own the litellm call.
"""

from __future__ import annotations

import os

# Defaults live here, ONCE. Process env overrides each.
_DEFAULT_BASE_URL = "http://localhost:8317/v1"
_DEFAULT_API_KEY = "dummy-not-used"        # VibeProxy ignores it; litellm requires a value
DEFAULT_MODEL = "gpt-5.4"                   # default WORKER model (not the router model)

# precedence: PROXY_* is the new canonical name and wins over the legacy name.
_MODEL_ENVS = ("PROXY_MODEL", "VIBEPROXY_MODEL")


def model_set_in(env) -> bool:
    """True if a worker-model env var (either name) is present AND non-empty."""
    return any(env.get(k) for k in _MODEL_ENVS)


def model_value(env):
    """The worker-model value under either name, PROXY_MODEL first. None if absent."""
    for k in _MODEL_ENVS:
        v = env.get(k)
        if v:
            return v
    return None


def base_url() -> str:
    return (os.getenv("PROXY_BASE_URL") or os.getenv("VIBEPROXY_BASE_URL")
            or _DEFAULT_BASE_URL)


def api_key() -> str:
    v = os.getenv("PROXY_API_KEY") or os.getenv("VIBEPROXY_API_KEY")
    if v:
        return v
    # Fall back to the key install()/upgrade() provisioned into the proxy's
    # config.yaml. Sending it matters even though the bind is localhost:
    # CLIProxyAPI derives the codex upstream's prompt_cache_key from the
    # inbound key — without it, no prompt-cache hits (#139).
    try:
        from harness.proxy_service import paths as _proxy_paths
        p = _proxy_paths.client_key_path()
        if p.exists():
            k = p.read_text().strip()
            if k:
                return k
    except OSError:
        pass
    return _DEFAULT_API_KEY


def default_model() -> str:
    """The configured worker model (env override of DEFAULT_MODEL)."""
    return model_value(os.environ) or DEFAULT_MODEL


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
