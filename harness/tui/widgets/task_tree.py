"""TaskTree — the live checklist (✓ done / ▣ in-progress / □ pending / ✗ failed),
updated in place. Reads a tuple of TaskItem. See spec §6 / components.md C."""

from __future__ import annotations

import shlex

from textual.widgets import Static

from harness.tui.state import TaskItem
from harness.tui.tokens import GLYPH

# GLYPH has no task-status glyph for "in_progress" or "pending"; keep literals.
_GLYPH = {
    "done": (GLYPH["done"], "success"),
    "failed": (GLYPH["failed"], "error"),
    "in_progress": ("▣", "accent"),
    "pending": ("□", "muted"),
}

# Programs that act as navigation/labels in a chain, not the "real" action.
_NOISE = {"cd", "echo", "ls", "source", "export"}
# Pattern-search tools: their first quoted string IS the meaningful subject.
_SEARCH = {"grep", "egrep", "fgrep", "rg", "ag"}
_WIDTH_CAP = 60      # final visible summary length
_PATTERN_CAP = 18    # quoted pattern length before it is itself elided


def _strip_tail(seg: str) -> str:
    """Drop a trailing pipe/redirect from a command segment, ignoring any that
    sit inside quotes (e.g. a `\\|` alternation inside a grep pattern)."""
    quote = ""
    for i, ch in enumerate(seg):
        if quote:
            if ch == quote:
                quote = ""
        elif ch in ('"', "'"):
            quote = ch
        elif ch in "|<>":
            cut = i
            # include a numeric fd prefix like the `2` in `2>` in what we drop
            while cut > 0 and seg[cut - 1].isdigit():
                cut -= 1
            return seg[:cut].strip()
    return seg.strip()


def _first_quoted(seg: str) -> str | None:
    """Return the first single/double-quoted substring's contents, or None."""
    for q in ('"', "'"):
        i = seg.find(q)
        if i != -1:
            j = seg.find(q, i + 1)
            if j != -1:
                return seg[i + 1:j]
    return None


def _summarize_segment(seg: str) -> str:
    """Summarize one command segment. Search tools -> first quoted pattern;
    otherwise program + leading flags + first non-flag operand. '' if empty."""
    seg = _strip_tail(seg)
    if not seg:
        return ""
    tokens = seg.split()
    if not tokens:
        return ""
    prog = tokens[0]

    if prog in _SEARCH:
        patt = _first_quoted(seg)
        if patt is not None:
            short = patt if len(patt) <= _PATTERN_CAP else patt[:_PATTERN_CAP] + "..."
            return f'{prog} "{short}"'
        # no quoted pattern: fall through to operand logic below

    out = [prog]
    for idx, tok in enumerate(tokens[1:], start=1):
        if tok.startswith("-"):
            out.append(tok)
            continue
        # first non-flag operand: if it directly follows a single-dash short flag
        # (e.g. `-c "code"`), it is that flag's VALUE -> stop at the flag, not the value.
        prev = tokens[idx - 1]
        if prev.startswith("-") and not prev.startswith("--") and len(prev) == 2:
            return " ".join(out)
        out.append(tok)
        return " ".join(out)
    return " ".join(out)            # only flags followed prog


def _cap(text: str) -> str:
    return text if len(text) <= _WIDTH_CAP else text[: _WIDTH_CAP - 1] + "…"


def summarize_command(cmd: str) -> str:
    """Summarize a (possibly &&-chained) shell command to one scannable line:
    first real command + '(+N more)'. Falls back to the width-capped full command
    when no real (non-noise) segment is found. Display-only; pure."""
    raw = cmd.strip()
    if not raw:
        return ""
    segments = [s.strip() for s in raw.split("&&") if s.strip()]
    real = []
    for seg in segments:
        head = _strip_tail(seg).split()
        prog = head[0] if head else ""
        if prog and prog not in _NOISE:
            real.append(seg)
    if not real:
        return _cap(raw)
    summary = _summarize_segment(real[0])
    if not summary:
        return _cap(raw)
    extra = len(real) - 1
    if extra > 0:
        summary = f"{summary}  (+{extra} more)"
    return _cap(summary)


class TaskTree(Static):
    def __init__(self, **kwargs) -> None:
        super().__init__("", markup=True, **kwargs)

    def lines_for(self, tasks: tuple[TaskItem, ...]) -> list[str]:
        out = []
        for t in tasks:
            glyph, token = _GLYPH.get(t.status, ("□", "muted"))
            label = t.label[2:] if t.label.startswith("$ ") else t.label
            out.append(f"[${token}]{glyph}[/] [$foreground]{label}[/]")
        return out

    def update_tasks(self, tasks: tuple[TaskItem, ...]) -> None:
        self.update("\n".join(self.lines_for(tasks)))
