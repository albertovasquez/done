"""Living brand book / component gallery for the `dn` TUI design system.

Generates a single self-contained HTML page from the **live** design tokens and
widget render logic — so the page can never drift from the real system. Run it
after any token change:

    python -m harness.tui.styles.brandbook        # writes brandbook.html next to this file
    python -m harness.tui.styles.brandbook -o /tmp/bb.html   # custom output

The page shows, faithfully on the real navy terminal background:
  1. the palette (every Theme color + custom token), as swatches with role + hex
  2. the glyph map (tokens.GLYPH), grouped
  3. the status states — agent-state vs tool-status side by side, which makes the
     two-vocabulary split visible (see the design-system drift audit)
  4. the components that actually SHIP today, rendered from the SAME markup
     strings the widgets emit (translated to HTML), tagged honestly.

It is read-only: it imports theme/tokens/widget modules and never mutates them.
"""

from __future__ import annotations

import argparse
import html
import re
from pathlib import Path

from harness.tui.theme import HARNESS_THEME, COLORS, STATUS_COLOR
from harness.tui.tokens import GLYPH, STATUS_LABEL
from harness.tui.state import AgentState, ToolStatus
from harness.tui.widgets.status_chip import (
    StatusChip, _STATE_TOKEN, _STATE_GLYPH, state_color_token,
    TOOL_STATUS_TOKEN, TOOL_STATUS_LABEL,
)

# ── usage notes: SINGLE SOURCE = components.md ────────────────────────────────
# The "When to use" guidance lives ONLY in components.md. The brand book parses it
# out and renders it under each mock, so the two artifacts never diverge. If a
# component has no note in the markdown, the HTML simply omits it (never invents).

_COMPONENTS_MD = Path(__file__).with_name("components.md")
_HEADER = re.compile(r"^#{3,4}\s+(.*)$")
_NAME = re.compile(r"`([^`]+)`")
_WHEN = re.compile(r"^\s*-\s*\*\*When to use:\*\*\s*(.+)$")


def _parse_usage_notes(md_path: Path = _COMPONENTS_MD) -> dict[str, str]:
    """Map every backticked component name in a `### ` header → its 'When to use'
    text (collapsed to one paragraph). Names normalized: stripped of a leading
    `.for_yolo`-style suffix is kept as-is so 'StatusChip.for_yolo' resolves too.
    Shared headers (e.g. 'StateDot / ActivityGlyph') map BOTH names to the note."""
    notes: dict[str, str] = {}
    if not md_path.exists():
        return notes
    cur_names: list[str] = []
    buf: list[str] = []

    def flush():
        if cur_names and buf:
            text = " ".join(s.strip() for s in buf).strip()
            for n in cur_names:
                notes[n] = text

    for line in md_path.read_text(encoding="utf-8").splitlines():
        h = _HEADER.match(line)
        if h:
            flush()
            cur_names = _NAME.findall(h.group(1))
            buf = []
            continue
        w = _WHEN.match(line)
        if w:
            buf = [w.group(1)]
        elif buf:
            # Wrapped continuation of the bullet: indented prose, not a new
            # bullet / heading / fence / table. Anything else ends the note.
            if line.strip() and not line.lstrip().startswith(("-", "#", "```", "|")):
                buf.append(line)
            else:
                flush()
                buf = []
    flush()
    return notes


USAGE_NOTES = _parse_usage_notes()


def usage_html(name: str) -> str:
    """Render the 'When to use' note for a component, pulled from components.md."""
    note = USAGE_NOTES.get(name)
    if not note:
        return ""
    return (f'<p class="usage"><span class="usage-k">When to use</span> '
            f'{html.escape(note)}</p>')


# ── token → hex resolution ────────────────────────────────────────────────────
# One map from every semantic token name to its hex, pulled from the live theme.
# This is what makes the page faithful: the SAME token names the widgets use in
# their `[$token]` markup resolve to the SAME colors here.

def _build_token_hex() -> dict[str, str]:
    t = HARNESS_THEME
    tokens: dict[str, str] = {
        "primary": t.primary, "secondary": t.secondary, "accent": t.accent,
        "foreground": t.foreground, "background": t.background,
        "surface": t.surface, "panel": t.panel,
        "success": t.success, "warning": t.warning, "error": t.error,
    }
    # custom CSS variables ($muted, $code, $scheduled, $path, …)
    tokens.update({k: v for k, v in (t.variables or {}).items()})
    # COLORS (Rich-markup hexes) fill any gaps / aliases
    for k, v in COLORS.items():
        tokens.setdefault(k, v)
    return tokens


TOKEN_HEX = _build_token_hex()
BG = HARNESS_THEME.background        # navy terminal bg, used for every mock
FG = HARNESS_THEME.foreground


def hex_for(token: str) -> str:
    """Resolve a token name (or a literal #hex) to a hex string."""
    if token.startswith("#"):
        return token
    return TOKEN_HEX.get(token, FG)


# ── markup → HTML translator ──────────────────────────────────────────────────
# Translates the Textual/Rich markup the widgets actually produce
# (`[$token]…[/]`, `[token]…[/]`, `[b]…[/b]`) into HTML spans using TOKEN_HEX.
# Faithful by construction: we render the widgets' own strings, not re-invented ones.

_TAG = re.compile(r"\[(/?)(\$?[a-zA-Z0-9_\- ]+)\]")


def markup_to_html(s: str) -> str:
    """Best-effort Rich-markup → HTML. Handles color tokens and [b]. Nested
    spans close in order; good enough for the simple one/two-level markup the
    widgets emit."""
    out: list[str] = []
    stack: list[str] = []
    pos = 0
    for m in _TAG.finditer(s):
        out.append(html.escape(s[pos:m.start()]).replace("\n", "<br>"))
        pos = m.end()
        closing, name = m.group(1), m.group(2).strip()
        if closing:
            if stack:
                out.append("</span>")
                stack.pop()
            continue
        if name == "b":
            out.append('<span style="font-weight:700">')
            stack.append("b")
        else:
            color = hex_for(name[1:] if name.startswith("$") else name)
            out.append(f'<span style="color:{color}">')
            stack.append(name)
    out.append(html.escape(s[pos:]).replace("\n", "<br>"))
    out.extend("</span>" for _ in stack)
    return "".join(out)


# ── HTML building blocks ──────────────────────────────────────────────────────

def swatch(name: str, hexv: str, role: str) -> str:
    text = "#0A1524" if _is_light(hexv) else "#E3E3E3"
    return f"""<div class="swatch">
      <div class="chip" style="background:{hexv};color:{text}">{hexv}</div>
      <div class="meta"><span class="tok">${name}</span><span class="role">{html.escape(role)}</span></div>
    </div>"""


def _is_light(hexv: str) -> bool:
    try:
        r, g, b = (int(hexv[i:i+2], 16) for i in (1, 3, 5))
        return (0.299*r + 0.587*g + 0.114*b) > 140
    except Exception:
        return False


def term(inner_html: str, label: str = "") -> str:
    """Wrap mock content on the real terminal background."""
    cap = f'<div class="term-label">{html.escape(label)}</div>' if label else ""
    return f'{cap}<div class="term">{inner_html}</div>'


def component_card(name: str, status: str, desc: str, mock_html: str,
                   usage_key: str | None = None) -> str:
    badge = {"shipped": "✅ shipped", "unwired": "🟡 built · unwired",
             "designed": "📐 designed-only", "inlined": "◻ inlined in app.py"}.get(status, status)
    cls = {"shipped": "ok", "unwired": "warn", "designed": "dim", "inlined": "dim"}[status]
    # Usage note is pulled from components.md by name (single source). usage_key
    # lets a card whose title differs from the catalog name still resolve its note.
    note = usage_html(usage_key or _first_name(name))
    return f"""<div class="card">
      <div class="card-head"><h3>{html.escape(name)}</h3><span class="badge {cls}">{badge}</span></div>
      <p class="desc">{desc}</p>
      {mock_html}
      {note}
    </div>"""


def _first_name(title: str) -> str:
    """The catalog key for a card title: first identifier-ish token (so
    'SelectModal / PermissionModal' → 'SelectModal', 'Tool status pills' → 'Tool')."""
    return title.split()[0].split("/")[0].strip()


# ── faithful component mocks (from the widgets' OWN markup) ────────────────────

def _chip_markup(chip: StatusChip) -> str:
    """Rebuild the markup a StatusChip emits, from its stored label + token —
    the same f-string the widget's __init__ passes to .update()."""
    return f"[${chip._token}][b]{chip._label}[/b][/]"


def mock_status_chips() -> str:
    rows = []
    for st in AgentState:
        rows.append(markup_to_html(_chip_markup(StatusChip.from_state(st))))
    line = "&nbsp;&nbsp;".join(rows)
    return term(f'<div class="line">{line}</div>', "StatusChip.from_state — every AgentState")


def mock_tool_status_chips() -> str:
    rows = []
    for ts in ToolStatus:
        token = TOOL_STATUS_TOKEN.get(ts, "muted")
        label = TOOL_STATUS_LABEL.get(ts, "")
        rows.append(markup_to_html(f"[${token}][b]{label}[/b][/]"))
    return term('<div class="line">' + "&nbsp;&nbsp;".join(rows) + "</div>",
                "Tool status pills (ToolStatus)")


def mock_yolo() -> str:
    # Both footer toggles collapse to a bare glyph when ON, and spell themselves
    # out when OFF. Show ON (glyph) and OFF (labeled) for each.
    y_on = markup_to_html(_chip_markup(StatusChip.for_yolo(True, False)))
    y_off = markup_to_html(_chip_markup(StatusChip.for_yolo(False, False)))
    c_on = markup_to_html(_chip_markup(StatusChip.for_compress_aware(True, False)))
    c_off = markup_to_html(_chip_markup(StatusChip.for_compress_aware(False, False)))
    return term(f'<div class="line">{y_on}</div><div class="line">{y_off}</div>'
                f'<div class="line">{c_on}</div><div class="line">{c_off}</div>',
                "footer mode chips — glyph when ON, labeled when OFF")


def mock_state_dots() -> str:
    cells = []
    for st in AgentState:
        g = GLYPH[_STATE_GLYPH.get(st, "idle")]
        color = hex_for(state_color_token(st))
        cells.append(f'<span style="color:{color}">{html.escape(g)}</span>'
                     f'<span class="dim">&nbsp;{st.value}</span>')
    return term('<div class="line">' + "&nbsp;&nbsp;&nbsp;".join(cells) + "</div>",
                "StateDot — leading state indicator")


def mock_activity_status() -> str:
    acc, fg, muted = hex_for("accent"), hex_for("foreground"), hex_for("muted")
    line = (f'<span style="color:{acc}">◐</span> '
            f'<span style="color:{fg}">Asking clarifying questions…</span>'
            f'<span style="color:{muted}">&nbsp;· 2 done</span> '
            f'<span style="color:{muted}">(1m 18s · ↓ 4.0k tokens)</span>')
    return term(f'<div class="line">{line}</div>',
                "ActivityStatus — the live work line (one looping ◐)")


def mock_tool_call_row() -> str:
    edit, fg, done, code = (hex_for("accent"), hex_for("foreground"),
                            hex_for("success"), hex_for("code"))
    collapsed = (f'<span style="color:{edit}">{GLYPH["edit"]}</span> '
                 f'<span style="color:{fg}">harness/api.ts</span>&nbsp;&nbsp;&nbsp;'
                 f'<span style="color:{edit};font-weight:700">RUNNING</span>')
    expanded = (f'<span style="color:{done}">{GLYPH["read"]}</span> '
                f'<span style="color:{fg}">app.py</span>&nbsp;&nbsp;&nbsp;'
                f'<span style="color:{done};font-weight:700">COMPLETED</span>'
                f'<br><span style="color:{code}">  def compose(self) -> ComposeResult:'
                f'<br>      yield Header()<br>  … (+6 more lines)</span>')
    return term(f'<div class="line">{collapsed}</div>'
                f'<div class="line" style="margin-top:.5em">{expanded}</div>',
                "ToolCallRow — collapsed line + expanded capped body")


def mock_tool_edit_summary() -> str:
    """Desired look (from a concept screenshot): an edit tool summarized as a
    +N/-N diff-stat. NOT shipped — ToolView has no line counts yet."""
    fg, muted, done, err = (hex_for("foreground"), hex_for("muted"),
                            hex_for("success"), hex_for("error"))
    head = (f'<span style="color:{done}">●</span> '
            f'<span style="color:{fg};font-weight:700">Update</span>'
            f'<span style="color:{fg}">(docs/superpowers/specs/2026-06-27-persona-C2b-rail-design.md)</span>')
    summary = (f'&nbsp;&nbsp;<span style="color:{muted}">└ Added </span>'
               f'<span style="color:{done};font-weight:700">42</span>'
               f'<span style="color:{muted}"> lines, removed </span>'
               f'<span style="color:{err};font-weight:700">11</span>'
               f'<span style="color:{muted}"> lines</span>')
    return term(f'<div class="line">{head}</div><div class="line">{summary}</div>',
                "ToolCallRow / ToolResultBlock — desired edit summary (+N/−N diff-stat). NOT shipped: ToolView has no line counts.")


def mock_user_message() -> str:
    acc = hex_for("accent")
    line = (f'<span style="color:{acc};font-weight:700">▌ '
            f'Build me a brand-book page for the design system</span>')
    return term(f'<div class="line">{line}</div>',
                "User message — accent ▌ bar + bold")


def mock_answer_stream() -> str:
    """AnswerStream + chips — inlined in app.py (no widget), shown for completeness."""
    fg, acc, muted, code = (hex_for("foreground"), hex_for("accent"),
                            hex_for("muted"), hex_for("code"))
    prose = (f'<span style="color:{fg}">Here\'s the fix. The bug was an off-by-one '
             f'in the loop bound:</span>')
    codeln = (f'<span style="color:{code}">  for i in range(n + 1):  # was range(n)</span>')
    chips = (f'<span style="color:{acc}">code_fix</span>'
             f'<span style="color:{muted}"> · skills: debugging, tdd</span>')
    return term(f'<div class="line">{prose}</div>'
                f'<div class="line">{codeln}</div>'
                f'<div class="line" style="margin-top:.4em">{chips}</div>',
                "AnswerStream + chips — streamed markdown (inlined in app.py)")


def mock_select_modal() -> str:
    fg, muted, acc = hex_for("foreground"), hex_for("muted"), hex_for("accent")
    title = f'<span style="color:{fg};font-weight:700">Select model</span>&nbsp;&nbsp;&nbsp;<span style="color:{muted}">esc</span>'
    search = f'<span style="color:{muted}">Search</span>'
    rows = (f'<div class="line"><span style="color:{acc}">●</span> claude-opus-4-8</div>'
            f'<div class="line">&nbsp;&nbsp;claude-sonnet-4-6</div>'
            f'<div class="line">&nbsp;&nbsp;mock</div>')
    footer = f'<span style="color:{muted}">↑↓ move · enter select · esc cancel</span>'
    return term(f'<div class="line">{title}</div>'
                f'<div class="line" style="margin-top:.3em">{search}</div>'
                f'{rows}'
                f'<div class="line" style="margin-top:.3em">{footer}</div>',
                "SelectModal — searchable picker (● = current). PermissionModal is a sibling (Allow / Reject).")


def mock_slash_menu() -> str:
    acc, muted = hex_for("accent"), hex_for("muted")
    def row(name, desc):
        return (f'<div class="line"><span style="color:{acc}">/{name}</span>'
                f'&nbsp;&nbsp;&nbsp;<span style="color:{muted}">{desc}</span></div>')
    return term(row("model", "switch the model") + row("yolo", "toggle permission bypass")
                + row("reload", "reload the agent"),
                "SlashMenu — filtered command list (typed after '/')")


def mock_status_bar() -> str:
    acc, muted, err = hex_for("accent"), hex_for("muted"), hex_for("error")
    path_dim, path = hex_for("path-dim"), hex_for("path")
    bypass = GLYPH["bypass"]
    mode = (f'<span style="color:{acc};font-weight:700">Build</span>'
            f'<span style="color:{muted}"> · claude-opus-4-8 vibeproxy</span>')
    yolo = f'<span style="color:{err};font-weight:700">{bypass} bypass permissions on</span>'
    cwd = (f'<span style="color:{path_dim}">~/Work/Quiubo/</span>'
           f'<span style="color:{path}">harness</span>')
    return term(f'<div class="line">{mode}</div>'
                f'<div class="line">{yolo}&nbsp;&nbsp;&nbsp;&nbsp;{cwd}</div>',
                "Status bar / footer meta — mode · model · bypass · cwd (inlined in app.py)")


def mock_persona_indicator() -> str:
    """C2a — the SHIPPED status-bar persona chip."""
    acc, muted = hex_for("accent"), hex_for("muted")
    chip = f'<span style="color:{muted}">persona: </span><span style="color:{acc};font-weight:700">fred</span>'
    return term(f'<div class="line">{chip}</div>',
                "PersonaIndicator — which agent you're addressing (shipped, C2a)")


def mock_fleet_header() -> str:
    acc, muted, fg = hex_for("accent"), hex_for("muted"), hex_for("foreground")
    succ, sched = hex_for("success"), hex_for("scheduled")
    wm = f'<span style="color:{acc};font-weight:700">DoneDone</span><span style="color:{muted}"> v0.5.0</span>'
    dots = (f'<span style="color:{succ}">●</span><span style="color:{acc}">●</span>'
            f'<span style="color:{sched}">●</span>')
    pill = (f'<span style="color:{fg}">fred</span> <span style="color:{muted}">·</span> '
            f'{dots} <span style="color:{fg}">3 running</span> <span style="color:{muted}">▾</span>')
    right = f'<span style="color:{acc}">Build</span> <span style="color:{muted}">· Vibeproxy</span>'
    return term(f'<div class="line">{wm}&nbsp;&nbsp;&nbsp;&nbsp;[ {pill} ]&nbsp;&nbsp;&nbsp;&nbsp;{right}</div>',
                "FleetHeader — wordmark + fleet dropdown pill (derived dots/counts) + mode")


def mock_agent_rail() -> str:
    fg, muted, acc = hex_for("foreground"), hex_for("muted"), hex_for("accent")
    succ, sched = hex_for("success"), hex_for("scheduled")
    label = f'<span style="color:{muted}">AGENTS&nbsp;&nbsp;&nbsp;&nbsp;5 · esc to close</span>'
    def row(arrow, dot_color, name, status, status_color, sub, cron=False):
        marker = f'<span style="color:{acc}">▸</span> ' if arrow else '&nbsp;&nbsp;'
        cglyph = f'<span style="color:{sched}">↻</span> ' if cron else ''
        return (f'<div class="line">{marker}{cglyph}'
                f'<span style="color:{dot_color}">●</span> '
                f'<span style="color:{fg}">{name}</span>'
                f'&nbsp;&nbsp;&nbsp;<span style="color:{status_color}">{status}</span>'
                f'<br>&nbsp;&nbsp;&nbsp;&nbsp;<span style="color:{muted}">{sub}</span></div>')
    rows = (row(True, acc, "fred", "active", acc, "editing harness/api.ts")
            + row(False, succ, "sam", "running", succ, "tracing auth regression")
            + row(False, "#b58cf0", "nova", "running", "#b58cf0", "generating migration")
            + row(False, sched, "robbie", "cron", sched, "nightly-sync · syncing", cron=True)
            + row(False, muted, "dex", "idle", muted, "queued: lint sweep"))
    footer = f'<span style="color:{muted}">↑↓ select · ⏎ switch · n new</span>'
    return term(f'<div class="line">{label}</div>{rows}'
                f'<div class="line" style="margin-top:.4em">{footer}</div>',
                "AgentRail — agents list / drawer (active highlighted, cron row, state dots)")


def mock_progress_row() -> str:
    fg, muted, acc = hex_for("foreground"), hex_for("muted"), hex_for("accent")
    bar_done = "▓" * 8
    bar_left = "░" * 5
    row = (f'<span style="color:{acc}">●</span> '
           f'<span style="color:{fg};font-weight:700">Index repo dependencies</span>'
           f'&nbsp;&nbsp;&nbsp;<span style="color:{acc};font-weight:700">RUNNING</span>'
           f'<br>&nbsp;&nbsp;<span style="color:{muted}">Scanning package graphs and lockfiles</span>'
           f'<br>&nbsp;&nbsp;<span style="color:{acc}">{bar_done}</span>'
           f'<span style="color:{muted}">{bar_left}  64% · 18:42</span>')
    return term(f'<div class="line">{row}</div>',
                "ProgressRow — task with known % (StateDot + StatusChip + ProgressBar + elapsed)")


def mock_cron_row() -> str:
    fg, muted, sched = hex_for("foreground"), hex_for("muted"), hex_for("scheduled")
    task = (f'<span style="color:{muted}">□</span> '
            f'<span style="color:{fg};font-weight:700">Weekly report cron</span>'
            f'<span style="color:{muted}"> · emails reports</span>'
            f'&nbsp;&nbsp;&nbsp;<span style="color:{sched}">in 2d 14h</span> '
            f'<span style="color:{sched};font-weight:700">SCHEDULED</span>')
    done = (f'<span style="color:{hex_for("success")}">□</span> '
            f'<span style="color:{muted}">2 completed · customer import, background audit</span>')
    return term(f'<div class="line">{task}</div>'
                f'<div class="line" style="margin-top:.3em">{done}</div>',
                "Cron task row + ScheduleBadge (a task that is scheduled) · completed count")


def mock_task_tree() -> str:
    """Desired TaskTree look (from the concept screenshot): an ActivityStatus line
    over a checklist that STRIKES THROUGH completed items. Currently unwired."""
    sched, fg, muted, succ, acc = (hex_for("scheduled"), hex_for("foreground"),
                                   hex_for("muted"), hex_for("success"), hex_for("accent"))
    status = (f'<span style="color:{sched}">◦</span> '
              f'<span style="color:{sched};font-weight:700">Stewing…</span> '
              f'<span style="color:{muted}">(4m 45s · ↓ 17.0k tokens)</span>')
    def done(t):
        return (f'<div class="line">&nbsp;&nbsp;<span style="color:{succ}">✓</span> '
                f'<span style="color:{muted};text-decoration:line-through">{t}</span></div>')
    prog = (f'<div class="line">&nbsp;&nbsp;<span style="color:{acc}">▣</span> '
            f'<span style="color:{fg}">Render mocks in brand book</span></div>')
    pend = (f'<div class="line">&nbsp;&nbsp;<span style="color:{muted}">□ Wire it up</span></div>')
    return term(f'<div class="line">{status}</div>'
                f'<div class="line">&nbsp;<span style="color:{muted}">└</span></div>'
                + done("Save concept mockups into repo")
                + done("Add agent/fleet/cron components to catalog")
                + prog + pend,
                "TaskTree — desired look: activity line + checklist, completed items struck through (unwired)")


def mock_activity_region() -> str:
    acc, fg, muted = hex_for("accent"), hex_for("foreground"), hex_for("muted")
    rule = f'<span style="color:{muted}">{"─"*44}</span>'
    status = (f'<span style="color:{acc}">◐</span> '
              f'<span style="color:{fg}">Running test…</span>'
              f'<span style="color:{muted}">&nbsp;· 1 done (4s)</span>'
              f'<span style="color:{muted}">&nbsp;&nbsp;&nbsp;&nbsp;ctrl+o details</span>')
    return term(f'<div class="line">{rule}</div><div class="line">{status}</div>',
                "ActivityRegion — pinned transient zone (status-only default)")


# ── sections ──────────────────────────────────────────────────────────────────

def section_palette() -> str:
    brand = ["primary", "accent", "foreground", "secondary", "background",
             "surface", "error"]
    product = ["success", "warning", "scheduled"]
    derived = [k for k in (HARNESS_THEME.variables or {})
               if k not in product]
    def group(title, keys, src):
        cards = []
        for k in keys:
            hexv = src.get(k) if isinstance(src, dict) else getattr(HARNESS_THEME, k, None)
            hexv = hexv or TOKEN_HEX.get(k)
            if not hexv:
                continue
            role = _ROLE.get(k, "")
            cards.append(swatch(k, hexv, role))
        return f'<h3>{title}</h3><div class="swatches">{"".join(cards)}</div>'
    theme_attrs = {k: getattr(HARNESS_THEME, k) for k in
                   ["primary", "accent", "foreground", "secondary", "background",
                    "surface", "panel", "success", "warning", "error"]}
    return ("<section><h2>Palette</h2>"
            "<p class='note'>Brand core + sanctioned product-status + derived tokens. "
            "Generated from <code>theme.py</code>; do not hand-edit.</p>"
            + group("Brand core", brand, theme_attrs)
            + group("Product status (sanctioned brand extension)", product, theme_attrs)
            + group("Derived / functional tokens", derived, HARNESS_THEME.variables or {})
            + "</section>")


_ROLE = {
    "primary": "accent · running · wordmark", "accent": "accent bar · active",
    "foreground": "body text", "secondary": "secondary / muted text",
    "background": "app background (navy)", "surface": "boxes (user msg, compose)",
    "panel": "panels", "error": "error / failed / bypass-on",
    "success": "done / completed", "warning": "caution (amber)",
    "scheduled": "cron / scheduled / attention", "muted": "placeholders · hints · meta",
    "code": "inline code / shell (blue tint)", "wordmark-dim": "wordmark left half",
    "wordmark-bright": "wordmark right half", "accent-bar": "accent bars",
    "path-dim": "cwd parent segments", "path": "cwd current dir",
}


def section_glyphs() -> str:
    groups = {
        "State dots": ["idle", "active", "responding", "tool", "done", "failed",
                       "scheduled", "awaiting"],
        "Tool subtypes (inferred, display-only)": ["edit", "test", "read", "shell", "search"],
        "Footer / brand": ["bypass", "path"],
    }
    out = ["<section><h2>Glyph map</h2><p class='note'>From "
           "<code>tokens.GLYPH</code>. Status is always carried by "
           "<em>color + glyph + weight</em> together, so meaning survives "
           "monochrome terminals.</p>"]
    for title, keys in groups.items():
        cells = []
        for k in keys:
            g = GLYPH.get(k)
            if g is None:
                continue
            cells.append(f'<div class="glyph"><span class="g">{html.escape(g)}</span>'
                         f'<span class="gk">{k}</span></div>')
        out.append(f'<h3>{title}</h3><div class="term"><div class="glyphs">'
                   + "".join(cells) + "</div></div>")
    out.append("</section>")
    return "".join(out)


def section_status() -> str:
    """The two-vocabulary split, made visible side by side."""
    agent_rows = []
    for st in AgentState:
        label = STATUS_LABEL.get(st.value, st.value.upper())
        color = hex_for(state_color_token(st))
        glyph = GLYPH[_STATE_GLYPH.get(st, "idle")]
        agent_rows.append(
            f'<tr><td class="mono">{st.value}</td>'
            f'<td><span style="color:{color}">{html.escape(glyph)} <b>{label}</b></span></td></tr>')
    tool_rows = []
    for ts in ToolStatus:
        token = TOOL_STATUS_TOKEN.get(ts, "muted")
        label = TOOL_STATUS_LABEL.get(ts, "")
        color = hex_for(token)
        tool_rows.append(
            f'<tr><td class="mono">{ts.value}</td>'
            f'<td><span style="color:{color};font-weight:700">{label}</span></td></tr>')
    return (f"""<section><h2>Status states</h2>
      <p class='note'>Two vocabularies coexist today — <b>agent state</b>
      (<code>tokens.STATUS_LABEL</code>) and <b>tool status</b>
      (<code>STATUS_COLOR</code> / <code>TOOL_STATUS_LABEL</code>). They overlap
      (running↔in_progress, queued↔pending) but are <em>not</em> unified — this
      table makes the split visible (a known design-system cleanup item).</p>
      <div class="two-col">
        <div><h3>Agent state</h3><table class="status">{"".join(agent_rows)}</table></div>
        <div><h3>Tool status</h3><table class="status">{"".join(tool_rows)}</table></div>
      </div></section>""")


def section_components() -> str:
    cards = [
        component_card("StatusChip", "shipped",
            "Uppercase status pill. The atomic status atom, reused everywhere.",
            mock_status_chips()),
        component_card("Tool status pills", "shipped",
            "ToolStatus → QUEUED / RUNNING / COMPLETED / FAILED.",
            mock_tool_status_chips()),
        component_card("StatusChip.for_yolo", "shipped",
            "Footer permission-mode line. Red = active bypass (loudest signal).",
            mock_yolo()),
        component_card("StateDot", "unwired",
            "Leading state indicator (defined in status_chip.py, not yet mounted).",
            mock_state_dots()),
        component_card("ActivityStatus", "shipped",
            "The live work line: one looping ◐ + label · elapsed · tokens · N done.",
            mock_activity_status()),
        component_card("ActivityRegion", "shipped",
            "Pinned transient zone above the composer; status-only by default.",
            mock_activity_region()),
        component_card("TaskTree", "unwired",
            "Live plan checklist — desired look: activity line + items struck "
            "through as they finish. Built but unwired (status-only decision).",
            mock_task_tree()),
        component_card("ToolCallRow", "shipped",
            "One tool call: subtype glyph + title + status (collapsed) / capped body (expanded).",
            mock_tool_call_row()),
        component_card("ToolCallRow — edit summary (desired)", "designed",
            "Desired look: an edit tool summarized as a +N/−N diff-stat (seed of "
            "ToolResultBlock). Needs line counts on ToolView from the engine first.",
            mock_tool_edit_summary(), usage_key="ToolCallRow"),
        component_card("UserMessage", "shipped",
            "Accent ▌ bar + bold text in the transcript (rendered inline in app.py).",
            mock_user_message()),
    ]
    return ("<section><h2>Components (shipped today)</h2>"
            "<p class='note'>Rendered from the widgets' <em>own</em> markup "
            "strings, translated to HTML — so these match what the TUI draws. "
            "Tags: ✅ shipped · 🟡 built-but-unwired · 📐 designed-only · "
            "◻ inlined in app.py.</p>"
            '<div class="cards">' + "".join(cards) + "</div></section>")


def section_surfaces() -> str:
    """The larger surfaces we actually use — modals, the composer menu, the
    footer, and the inlined response stream — beyond the atomic primitives."""
    cards = [
        component_card("AnswerStream + chips", "inlined",
            "The streamed agent response + DoneDone's request-type / skills chips. "
            "Drawn directly in app.py (no standalone widget).",
            mock_answer_stream()),
        component_card("SelectModal / PermissionModal", "shipped",
            "Searchable picker (● = current). PermissionModal is a sibling with "
            "Allow / Reject options; both shipped & wired.",
            mock_select_modal()),
        component_card("SlashMenu", "shipped",
            "Filtered command list shown as you type after '/'. Shipped & wired.",
            mock_slash_menu()),
        component_card("Status bar / footer", "inlined",
            "Mode · model · permission-bypass posture · cwd. Inlined in app.py.",
            mock_status_bar()),
    ]
    return ("<section><h2>Surfaces (modals · composer · footer)</h2>"
            "<p class='note'>The larger surfaces beyond the atomic primitives — "
            "what you actually see every session. Mocks are faithful to the "
            "widgets' markup / layout.</p>"
            '<div class="cards">' + "".join(cards) + "</div></section>")


def section_fleet() -> str:
    """The agent / fleet / cron surfaces (from the concept mockups). Only the
    persona indicator ships today; the rest are designed-only and composed from
    the primitives above."""
    cards = [
        component_card("PersonaIndicator", "shipped",
            "Which agent/persona you're talking to — the engine's real resolved id.",
            mock_persona_indicator(), usage_key="PersonaIndicator"),
        component_card("FleetHeader", "designed",
            "Wordmark + a fleet dropdown pill (derived dots + counts) + mode. "
            "Composed; counts derived from FleetSnapshot.",
            mock_fleet_header(), usage_key="FleetHeader"),
        component_card("AgentRail (rail / drawer)", "designed",
            "The agents list — left rail or right drawer. Each row = StateDot + "
            "name + status word + sub-line; active highlighted; cron rows get ↻.",
            mock_agent_rail(), usage_key="AgentRail"),
        component_card("ProgressRow", "designed",
            "A task with a known % — StateDot + StatusChip + ProgressBar + elapsed.",
            mock_progress_row(), usage_key="ProgressRow"),
        component_card("Cron task row + ScheduleBadge", "designed",
            "A scheduled task. No cron backend exists yet — this is the target look.",
            mock_cron_row(), usage_key="ScheduleBadge"),
    ]
    return ("<section><h2>Agents · fleet · crons</h2>"
            "<p class='note'>From the fleet/drawer/cron concept mockups "
            "(2026-06-27). Only <b>PersonaIndicator</b> ships today (C2a); the rest "
            "are <b>📐 designed-only</b> — and are <em>composed from the primitives "
            "above</em> (a rail row is a StateDot + chip; the header derives its "
            "dots from the same <code>FleetSnapshot</code>). See "
            "<code>persona-C2-drawer-arc-design.md</code>.</p>"
            '<div class="cards">' + "".join(cards) + "</div></section>")


# ── page assembly ─────────────────────────────────────────────────────────────

_CSS = """
:root{ --bg:#0A1524; --fg:#E3E3E3; --muted:#5B6577; --surface:#16243A; --accent:#286CE9; }
*{box-sizing:border-box}
body{margin:0;background:#070e1a;color:var(--fg);
  font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:48px 28px 96px}
header.top{border-bottom:1px solid var(--surface);padding-bottom:24px;margin-bottom:8px}
.mark{font-size:13px;letter-spacing:.32em;text-transform:uppercase;color:var(--muted)}
h1{font-size:30px;margin:.3em 0 .15em;font-weight:650}
.sub{color:var(--muted);font-size:14px}
.sub code{background:var(--surface);padding:1px 6px;border-radius:4px;color:#9DB8E8}
section{margin-top:52px}
h2{font-size:21px;border-left:3px solid var(--accent);padding-left:12px;margin:0 0 6px}
h3{font-size:13px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);
  margin:26px 0 12px;font-weight:600}
.note{color:var(--muted);font-size:13.5px;max-width:70ch;margin:.2em 0 0}
.note code,code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.92em;
  background:var(--surface);padding:1px 5px;border-radius:4px;color:#9DB8E8}
/* swatches */
.swatches{display:grid;grid-template-columns:repeat(auto-fill,minmax(168px,1fr));gap:12px}
.swatch{border:1px solid var(--surface);border-radius:8px;overflow:hidden;background:#0c1626}
.chip{height:62px;display:flex;align-items:flex-end;justify-content:flex-end;
  padding:6px 8px;font-family:ui-monospace,monospace;font-size:11px;font-weight:600}
.swatch .meta{padding:8px 10px;display:flex;flex-direction:column;gap:2px}
.tok{font-family:ui-monospace,monospace;font-size:12.5px;color:var(--fg)}
.role{font-size:11.5px;color:var(--muted)}
/* terminal mocks */
.term{background:var(--bg);border:1px solid var(--surface);border-radius:8px;
  padding:16px 18px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
  font-size:14px;line-height:1.7;overflow-x:auto}
.term-label{font-size:11.5px;color:var(--muted);margin:0 0 6px 2px}
.term .line{white-space:pre}
.term .dim,.dim{color:var(--muted)}
.glyphs{display:grid;grid-template-columns:repeat(auto-fill,minmax(96px,1fr));gap:14px}
.glyph{display:flex;flex-direction:column;align-items:center;gap:4px}
.glyph .g{font-size:24px;color:var(--fg)}
.glyph .gk{font-size:11px;color:var(--muted)}
/* status tables */
.two-col{display:grid;grid-template-columns:1fr 1fr;gap:28px}
table.status{width:100%;border-collapse:collapse;font-size:13.5px}
table.status td{padding:5px 8px;border-bottom:1px solid var(--surface)}
td.mono{font-family:ui-monospace,monospace;color:var(--muted)}
/* component cards */
.cards{display:grid;gap:18px}
.card{border:1px solid var(--surface);border-radius:10px;padding:18px 20px;background:#0c1626}
.card-head{display:flex;align-items:center;justify-content:space-between;gap:12px}
.card h3{margin:0;color:var(--fg);text-transform:none;letter-spacing:0;font-size:15.5px}
.card .desc{color:var(--muted);font-size:13px;margin:.35em 0 12px}
.usage{margin:12px 0 0;font-size:13px;line-height:1.5;color:#c3cdda;
  border-left:2px solid var(--accent);padding:6px 0 6px 12px;background:rgba(40,108,233,.06)}
.usage-k{display:inline-block;font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;
  color:var(--accent);font-weight:700;margin-right:8px}
.badge{font-size:11px;padding:3px 9px;border-radius:999px;white-space:nowrap}
.badge.ok{background:rgba(126,231,135,.12);color:#7ee787}
.badge.warn{background:rgba(227,179,65,.14);color:#e3b341}
.badge.dim{background:var(--surface);color:var(--muted)}
footer{margin-top:64px;padding-top:20px;border-top:1px solid var(--surface);
  color:var(--muted);font-size:12.5px}
@media(max-width:720px){.two-col{grid-template-columns:1fr}}
"""


def build_html(stamp: str) -> str:
    n_colors = len(TOKEN_HEX)
    n_glyphs = len(GLYPH)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>dn TUI — living brand book</title>
<style>{_CSS}</style></head>
<body><div class="wrap">
<header class="top">
  <div class="mark">DoneDone · dn TUI</div>
  <h1>Living brand book &amp; component gallery</h1>
  <p class="sub">Generated from <code>theme.py</code> + <code>tokens.py</code> —
     never hand-edit this file. Refresh with
     <code>python -m harness.tui.styles.brandbook</code>.</p>
</header>
{section_palette()}
{section_glyphs()}
{section_status()}
{section_components()}
{section_surfaces()}
{section_fleet()}
<footer>
  {n_colors} tokens · {n_glyphs} glyphs · {len(list(AgentState))} agent states ·
  {len(list(ToolStatus))} tool statuses. Source of truth:
  <code>harness/tui/theme.py</code>, <code>harness/tui/tokens.py</code>.
  Companion: <code>harness/tui/styles/components.md</code>. {stamp}
</footer>
</div></body></html>"""


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Generate the dn TUI brand-book HTML.")
    ap.add_argument("-o", "--out", type=Path,
                    default=Path(__file__).with_name("brandbook.html"))
    ap.add_argument("--stamp", default="",
                    help="optional build stamp text (e.g. a date) for the footer")
    args = ap.parse_args(argv)
    args.out.write_text(build_html(args.stamp), encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
