"""Subagent knobs read from done.conf WITHOUT touching AgentConfig's strict
round-trip. Two keys: [subagent].model / [subagent].max_concurrent (global) and
[agents.<id>].subagent_model (per-persona). All optional; unset => no-op."""
from __future__ import annotations

import tomllib

from harness.config import conf_path  # noqa: F401  (patched in tests)


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
    # A subagent worker is the "worker" role. Delegate to the one role ladder so
    # there's no second parallel resolver; the worker ladder includes the legacy
    # [agents.<id>].subagent_model / [subagent].model rungs, so this is
    # byte-identical to the previous inline implementation.
    if per_task:
        return per_task
    from harness.role_model import resolve_role_candidates
    # Parse via THIS module's _raw() (which reads sc.conf_path) so the resolver
    # sees the same config, and tests monkeypatching sc.conf_path still work.
    return resolve_role_candidates(agent_id, "worker", _raw(), parent_model)[0]


def subagent_max_concurrent(default: int = 4) -> int:
    sub = _raw().get("subagent")
    if isinstance(sub, dict):
        n = sub.get("max_concurrent")
        if isinstance(n, int) and n > 0:
            return n
    return default
