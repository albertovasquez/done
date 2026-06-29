"""Subagent knobs read from done.conf WITHOUT touching AgentConfig's strict
round-trip. Two keys: [subagent].model / [subagent].max_concurrent (global) and
[agents.<id>].subagent_model (per-persona). All optional; unset => no-op."""
from __future__ import annotations

import tomllib

from harness.config import RESERVED_KEY, conf_path  # noqa: F401  (conf_path patched in tests)


def _raw() -> dict:
    try:
        data = conf_path().read_bytes()
    except OSError:
        return {}
    if not data.strip():
        return {}
    try:
        return tomllib.loads(data.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        return {}


def resolve_subagent_model(agent_id: str, *, per_task: str | None = None,
                           parent_model: str) -> str:
    if per_task:
        return per_task
    data = _raw()
    agents = data.get("agents")
    if isinstance(agents, dict):
        table = agents.get(agent_id)
        if isinstance(table, dict):
            m = table.get("subagent_model")
            if isinstance(m, str) and m:
                return m
    sub = data.get("subagent")
    if isinstance(sub, dict):
        m = sub.get("model")
        if isinstance(m, str) and m:
            return m
    return parent_model


def subagent_max_concurrent(default: int = 4) -> int:
    sub = _raw().get("subagent")
    if isinstance(sub, dict):
        n = sub.get("max_concurrent")
        if isinstance(n, int) and n > 0:
            return n
    return default
