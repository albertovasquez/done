"""Pure render core for the TUI. No Textual, no acp connection, no async —
turns ACP update objects into display-ready values, and reads our custom
field_meta["harness"] stream into chip strings (the bit generic clients drop).
Duck-types acp update objects via attributes so tests can pass plain stubs."""

from __future__ import annotations

from dataclasses import dataclass

from harness.tui.tokens import GLYPH


@dataclass(frozen=True)
class RenderedItem:
    kind: str                 # "message" | "thought" | "user" | "tool" | "tool_update" | "plan"
    text: str = ""            # message/thought/user body
    id: str = ""              # tool_call_id (tool / tool_update correlation)
    title: str = ""           # "$ <command>" (tool)
    status: str = ""          # pending|in_progress|completed|failed
    body: str = ""            # tool output (tool_update)
    entries: tuple[tuple[str, str], ...] = ()   # plan: ((content, status), …)


_STATUS_COLORS = {
    "pending": "yellow",
    "in_progress": "blue",
    "completed": "green",
    "failed": "red",
}


def status_style(status) -> str:
    s = str(status)
    if "." in s:                     # "ToolCallStatus.failed" -> "failed"
        s = s.rsplit(".", 1)[-1]
    return _STATUS_COLORS.get(s, "white")


def render_update(update) -> RenderedItem | None:
    name = type(update).__name__
    if name in ("AgentMessageChunk", "UserMessageChunk", "AgentThoughtChunk"):
        kind = {"AgentMessageChunk": "message",
                "UserMessageChunk": "user",
                "AgentThoughtChunk": "thought"}[name]
        text = getattr(getattr(update, "content", None), "text", "") or ""
        return RenderedItem(kind=kind, text=text)
    if name == "ToolCallStart":
        return RenderedItem(kind="tool",
                            id=getattr(update, "tool_call_id", ""),
                            title=getattr(update, "title", ""),
                            status=str(getattr(update, "status", "")))
    if name == "ToolCallProgress":
        body = ""
        content = getattr(update, "content", None) or []
        if content:
            inner = getattr(content[0], "content", None)
            body = getattr(inner, "text", "") or ""
        return RenderedItem(kind="tool_update",
                            id=getattr(update, "tool_call_id", ""),
                            status=str(getattr(update, "status", "")),
                            body=body)
    if name == "AgentPlanUpdate":
        entries = tuple(
            (getattr(e, "content", "") or "", str(getattr(e, "status", "")))
            for e in (getattr(update, "entries", None) or [])
        )
        return RenderedItem(kind="plan", entries=entries)
    return None                      # current_mode_update, etc. — forward-compat


def harness_chips(field_meta: dict | None) -> list[str]:
    if not isinstance(field_meta, dict):
        return []
    harness = field_meta.get("harness")
    if not isinstance(harness, dict):
        return []
    chips: list[str] = []
    tc = harness.get("task_classified")
    if isinstance(tc, dict):
        task_type = tc.get("task_type", "?")
        skills = tc.get("skills") or []
        skills_str = ", ".join(skills) if skills else "—"
        conf = tc.get("confidence", 0.0) or 0.0
        chips.append(f"classified: {task_type} · skills: {skills_str} · conf: {conf:.2f}")
    sl = harness.get("skill_load")
    if isinstance(sl, dict):
        injected = sl.get("injected") or []
        skipped = sl.get("skipped") or []
        chips.append(f"skills: {len(injected)} loaded, {len(skipped)} skipped")
    return chips


def format_cwd(cwd: str, home: str | None = None, max_width: int = 48) -> str:
    """Two-tone, home-relative cwd for the status bar (Textual content markup).

    `$HOME` collapses to `~`; parent segments render dim ($path-dim), the current
    dir bright ($path) so the eye lands on where you are. If the result exceeds
    `max_width` columns, leading segments are dropped behind an `…/` ellipsis —
    the current dir is never truncated. A leading location glyph anchors it.

    Pure (no Textual / no os calls): `home` is injected so it stays unit-testable.
    """
    glyph = GLYPH["path"]
    path = cwd or ""
    if home and (path == home or path.startswith(home.rstrip("/") + "/")):
        path = "~" + path[len(home.rstrip("/")):]

    segs = [s for s in path.split("/") if s]
    if not segs:                                  # "/" or empty → just the root
        return f"[$path-dim]{glyph} [/][$path]{path or '/'}[/]"

    base = segs[-1]
    rooted = path.startswith("/")
    # budget: total minus glyph+space and the bright basename
    budget = max_width - len(glyph) - 1 - len(base)
    parent = segs[:-1]
    prefix = ""
    parent_str = ("/".join(parent) + "/") if parent else ""
    if rooted and parent:
        parent_str = "/" + parent_str
    # left-truncate parent segments until the dim prefix fits the budget
    while parent and len(prefix + parent_str) > max(budget, 0):
        parent = parent[1:]
        parent_str = ("/".join(parent) + "/") if parent else ""
        prefix = "…/"
    dim = f"{prefix}{parent_str}"
    return f"[$path-dim]{glyph} {dim}[/][$path]{base}[/]"
