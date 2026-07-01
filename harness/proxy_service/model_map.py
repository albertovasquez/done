"""Shared NeuralWatt model id map. LEAF: stdlib-only, no harness imports
(config_gen and model_ids both consume it; keep it cycle-free). We serve and
bind the real upstream id directly — no short aliases — so the proxy id, the
bound id, and the models.dev catalog id are all the same string. The map is
kept as (id, id) pairs (an identity map) so consumers of alias_to_upstream()/
upstream_to_alias() keep working unchanged. IDs confirmed against live
NeuralWatt /v1/models 2026-06-30."""
from __future__ import annotations

# (upstream model id, proxy id) — identical: we bind the real id, no alias.
NEURALWATT_MODELS: list[tuple[str, str]] = [
    ("glm-5.2", "glm-5.2"),
    ("qwen3.5-397b-fast", "qwen3.5-397b-fast"),
    ("glm-5.2-short-fast", "glm-5.2-short-fast"),
]


def alias_to_upstream() -> dict[str, str]:
    return {alias: up for up, alias in NEURALWATT_MODELS}


def upstream_to_alias() -> dict[str, str]:
    return {up: alias for up, alias in NEURALWATT_MODELS}
