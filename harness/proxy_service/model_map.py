"""Shared NeuralWatt alias<->upstream id map. LEAF: stdlib-only, no harness
imports (config_gen and model_ids both consume it; keep it cycle-free). The
alias is what the proxy serves and what gets bound; the upstream id is what
models.dev lists. IDs confirmed against live NeuralWatt /v1/models 2026-06-30."""
from __future__ import annotations

# (upstream model id, proxy alias)
NEURALWATT_MODELS: list[tuple[str, str]] = [
    ("glm-5.2", "glm"),
    ("qwen3.5-397b-fast", "qwen"),
    ("glm-5.2-short-fast", "glm-fast"),
]


def alias_to_upstream() -> dict[str, str]:
    return {alias: up for up, alias in NEURALWATT_MODELS}


def upstream_to_alias() -> dict[str, str]:
    return {up: alias for up, alias in NEURALWATT_MODELS}
