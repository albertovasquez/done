"""Reconcile the static catalog against what the proxy serves and which keys are
present. Never silently swaps a configured model. See spec: three-id discipline
(bind=proxy id, display=catalog name, match=canonical)."""
from __future__ import annotations

from dataclasses import dataclass

from harness import model_ids


@dataclass(frozen=True)
class ModelStatus:
    provider: str
    display_name: str
    bind_id: str | None      # proxy id to send; None until available
    status: str              # "available" | "login_needed" | "stale_config"


def reconcile(providers, proxy_ids, keys_present) -> list[ModelStatus]:
    out: list[ModelStatus] = []
    for prov in providers:
        has_key = bool(keys_present.get(prov.id, False))
        for m in prov.models:
            served = next((pid for pid in proxy_ids if model_ids.matches(pid, m.id)), None)
            if served is not None:
                status, bind = "available", served
            elif has_key:
                status, bind = "stale_config", None
            else:
                status, bind = "login_needed", None
            out.append(ModelStatus(prov.id, m.name, bind, status))
    return out


def resolve_or_warn(configured_model, statuses):
    """Return (model, warning|None). Never substitutes: returns the configured
    model verbatim; if it isn't an available bind_id, returns a warning string."""
    for s in statuses:
        if s.status == "available" and s.bind_id is not None and model_ids.matches(s.bind_id, configured_model):
            return configured_model, None
    warning = (f"Configured model '{configured_model}' is not available from the "
               f"proxy right now — it may need login or a proxy config refresh.")
    return configured_model, warning
