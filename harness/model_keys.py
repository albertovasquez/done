"""Two-source key/auth presence: OAuth/browser providers come from the proxy's
get-auth-status; api_key providers (neuralwatt, gemini) come from env-var
presence (the same NEURALWATT_API_KEY config_gen reads). Pure: both sources are
passed in, so tests need neither the proxy nor real env."""
from __future__ import annotations

from harness.proxy_service.providers import PROVIDERS


def keys_present(*, auth_status: dict, environ: dict) -> dict[str, bool]:
    out: dict[str, bool] = {}
    for p in PROVIDERS:
        if p.mechanism == "api_key":
            out[p.id] = any(environ.get(name) for name in p.env)
        else:
            st = auth_status.get(p.id, {})
            out[p.id] = bool(st) and st.get("status") not in (None, "", "unauthenticated")
    return out
