"""Role -> model resolution with ordered fallbacks (done.conf [agents.<id>.roles]).

Split from I/O so the ladder is pure and unit-testable: load_role_tables() reads
TOML; resolve_role_candidates() is a pure function over the parsed dict. Always
returns a list[str] (never a bare str). resolve_subagent_model wraps this with
role='worker' (see subagent_config.py) so legacy subagent config stays byte-identical."""
from __future__ import annotations

import tomllib

from harness.config import conf_path


def load_role_tables() -> dict:
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


def _str(v) -> str | None:
    return v if isinstance(v, str) and v else None


def _str_list(v) -> list[str]:
    if not isinstance(v, list):
        return []
    return [x for x in v if isinstance(x, str) and x]


def _roles_of(parsed: dict, agent_id: str) -> dict:
    agents = parsed.get("agents")
    if not isinstance(agents, dict):
        return {}
    table = agents.get(agent_id)
    if not isinstance(table, dict):
        return {}
    roles = table.get("roles")
    return roles if isinstance(roles, dict) else {}


def _primary_and_fallbacks(roles: dict, role: str) -> list[str]:
    out: list[str] = []
    p = _str(roles.get(role))
    if p:
        out.append(p)
    fb = roles.get("fallback")
    if isinstance(fb, dict):
        out.extend(_str_list(fb.get(role)))
    return out


def _legacy_worker_rungs(parsed: dict, agent_id: str) -> list[str]:
    out: list[str] = []
    agents = parsed.get("agents")
    if isinstance(agents, dict):
        table = agents.get(agent_id)
        if isinstance(table, dict):
            m = _str(table.get("subagent_model"))
            if m:
                out.append(m)
    sub = parsed.get("subagent")
    if isinstance(sub, dict):
        m = _str(sub.get("model"))
        if m:
            out.append(m)
    return out


def resolve_role_candidates(agent_id: str, role: str, parsed: dict,
                            parent_model: str) -> list[str]:
    cands: list[str] = []
    cands += _primary_and_fallbacks(_roles_of(parsed, agent_id), role)
    cands += _primary_and_fallbacks(_roles_of(parsed, "default"), role)
    if role == "worker":
        cands += _legacy_worker_rungs(parsed, agent_id)
    cands.append(parent_model)
    # order-preserving dedup
    seen: set[str] = set()
    out: list[str] = []
    for m in cands:
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out
