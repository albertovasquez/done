"""Flows: a flow is a named family of skills (by SkillMeta.flows tag). Enabling
flows per-persona (persona.toml `flows`) scopes which skills the router/agent see,
so context stays lean as skill families grow. New flow families need NO router
edits — they are data (a frontmatter tag + a persona.toml line). Pure functions."""

from __future__ import annotations

from harness.skills import SkillMeta


def scope_catalog(metas: list[SkillMeta], enabled_flows: list[str]) -> list[SkillMeta]:
    """Keep global skills (no flow tag) plus skills in an enabled flow. With no
    enabled flows this returns only global skills — callers that want the full,
    ungated catalog (the no-op) skip scoping when enabled_flows is empty."""
    enabled = set(enabled_flows)
    return [m for m in metas if not m.flows or (set(m.flows) & enabled)]


def render_map(metas: list[SkillMeta], enabled_flows: list[str]) -> str:
    """The /ask-done narrative: in-scope skills grouped by flow (untagged under
    'general'), each with its description; a user-only skill (disable-model-
    invocation) is marked with /name since that's the only way to reach it."""
    scoped = scope_catalog(metas, enabled_flows)
    groups: dict[str, list[SkillMeta]] = {}
    for m in scoped:
        key = m.flows[0] if m.flows else "general"
        groups.setdefault(key, []).append(m)
    out = ["# Flows and skills\n"]
    for flow in sorted(groups):
        out.append(f"\n## {flow}\n")
        for m in sorted(groups[flow], key=lambda x: x.name):
            tag = f" (use /{m.name})" if not m.model_invocable else ""
            out.append(f"- **{m.name}** — {m.description}{tag}")
    return "\n".join(out)
