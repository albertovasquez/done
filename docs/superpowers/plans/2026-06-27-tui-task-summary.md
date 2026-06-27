# TUI TaskTree Smart Command Summary — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render each TaskTree checklist line as a short summary (first real shell command + `(+N more)`) instead of the full chained command that wraps across many lines.

**Architecture:** Add a pure helper `summarize_command(cmd) -> str` to `task_tree.py` and apply it to `t.label` at the single render site (`lines_for`). Display-only; `TaskItem` state is unchanged (rows match by `tool_id`). A safe fallback returns the width-capped full label when no real command is found.

**Tech Stack:** Python 3.11, Textual, pytest. No new dependencies.

## Global Constraints

- Display-only: do NOT change `TaskItem` (`state.py:40-44`) or any state/reducer.
- Do NOT touch `ToolCallRow` / the `Ctrl+O` view (owned by merged PR #43).
- Noise programs (first token of an `&&` segment): `cd`, `echo`, `ls`, `source`, `export`.
- Width cap: 60 chars on the final visible summary text, ellipsis tail `…`.
- Quoted-pattern cap: 24 chars inside the quotes, ellipsis `…`.
- Test command from worktree root: `PYTHONPATH="$PWD" <primary>/.venv/bin/python -m pytest tests/test_tui_widgets.py -q`
  where `<primary>` is `/Users/alberto/Work/Quiubo/harness`. (Worktree has no `.venv`; `PYTHONPATH=$PWD` makes imports resolve to the worktree code — verify with `python -c "import harness.tui.widgets.task_tree as m; print(m.__file__)"`.)

---

## File Structure

- `harness/tui/widgets/task_tree.py` — add `summarize_command` (pure helper) + apply in `lines_for`. Currently 34 lines; stays small.
- `tests/test_tui_widgets.py` — add table-driven tests for `summarize_command` and a `lines_for` rendering assertion.

---

### Task 1: `summarize_command` pure helper

**Files:**
- Modify: `harness/tui/widgets/task_tree.py` (add module-level function + constants)
- Test: `tests/test_tui_widgets.py` (add tests)

**Interfaces:**
- Produces: `summarize_command(cmd: str) -> str` — returns the display summary
  (NO glyph, NO markup tokens; just the text, e.g. `cat persona.py  (+1 more)`).
  The `(+N more)` suffix is plain text here; Task 2 decides markup. Width-capped
  to 60 chars. Empty/all-noise input → width-capped full `cmd` (fallback).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_tui_widgets.py` (import near the other widget imports):

```python
from harness.tui.widgets.task_tree import summarize_command


def test_summarize_first_real_command_plus_count():
    cmd = 'cd harness && cat persona.py && echo "PERSONA_CONFIG" && cat persona_config.py'
    assert summarize_command(cmd) == "cat persona.py  (+1 more)"


def test_summarize_skips_leading_noise():
    cmd = 'ls -la && echo "---" && git log --oneline -5 2>/dev/null'
    assert summarize_command(cmd) == "git log"


def test_summarize_first_non_flag_token_is_arg():
    cmd = "cd harness && find templates -type f | head && echo CONFIG && cat config.py | head -80"
    assert summarize_command(cmd) == "find templates  (+1 more)"


def test_summarize_quoted_pattern_wins_over_flags():
    cmd = 'cd harness && grep -rn "system_prompt\\|system prompt\\|x" *.py | head -40'
    assert summarize_command(cmd) == 'grep "system_prompt..."'


def test_summarize_single_real_command_no_count():
    cmd = "cd harness && nl -ba tracing_agent.py | head -90"
    assert summarize_command(cmd) == "nl -ba tracing_agent.py"


def test_summarize_python_inline_flag():
    cmd = 'cd harness && python3 -c "import yaml,sys; print(1)"'
    assert summarize_command(cmd) == "python3 -c"


def test_summarize_all_noise_falls_back_to_full():
    assert summarize_command("cd harness") == "cd harness"


def test_summarize_empty_falls_back():
    assert summarize_command("") == ""
    assert summarize_command("   ") == ""


def test_summarize_width_capped_with_ellipsis():
    cmd = "cat " + "x" * 200
    out = summarize_command(cmd)
    assert len(out) <= 60
    assert out.endswith("…")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH="$PWD" /Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_tui_widgets.py -k summarize -q`
Expected: FAIL — `ImportError: cannot import name 'summarize_command'`.

- [ ] **Step 3: Write minimal implementation**

Add to `harness/tui/widgets/task_tree.py` (above the `TaskTree` class):

```python
import shlex

_NOISE = {"cd", "echo", "ls", "source", "export"}
_WIDTH_CAP = 60
_PATTERN_CAP = 24


def _strip_tail(seg: str) -> str:
    """Drop pipes and redirects from a single command segment."""
    seg = seg.split("|", 1)[0]
    for redir in (" 2>", " >", " <"):
        seg = seg.split(redir, 1)[0]
    return seg.strip()


def _summarize_segment(seg: str) -> str:
    """program + first non-flag token (quoted arg wins). Returns '' if empty."""
    seg = _strip_tail(seg)
    if not seg:
        return ""
    try:
        tokens = shlex.split(seg)
    except ValueError:
        tokens = seg.split()
    if not tokens:
        return ""
    prog = tokens[0]
    # quoted pattern wins: first token that looked quoted in the raw segment
    for tok in tokens[1:]:
        if (f'"{tok}"' in seg or f"'{tok}'" in seg) and not tok.startswith("-"):
            patt = tok if len(tok) <= _PATTERN_CAP else tok[:_PATTERN_CAP] + "..."
            return f'{prog} "{patt}"'
    for tok in tokens[1:]:
        if not tok.startswith("-"):
            return f"{prog} {tok}"
    # only flags after prog (e.g. python3 -c, nl -ba <file> handled above)
    return seg if len(tokens) > 1 and seg == f"{prog} {tokens[1]}" else prog + (
        f" {tokens[1]}" if len(tokens) > 1 and tokens[1].startswith("-") else "")


def _cap(text: str) -> str:
    return text if len(text) <= _WIDTH_CAP else text[: _WIDTH_CAP - 1] + "…"


def summarize_command(cmd: str) -> str:
    """Summarize a (possibly &&-chained) shell command to one scannable line:
    first real command + '(+N more)'. Falls back to the capped full command
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
```

NOTE on `nl -ba tracing_agent.py`: `nl` is real (not noise); `-ba` is a flag,
`tracing_agent.py` is the first non-flag token → `nl -ba tracing_agent.py`? No —
the rule returns `prog + first non-flag token` = `nl tracing_agent.py`. The test
expects `nl -ba tracing_agent.py`. Adjust `_summarize_segment` so flags BETWEEN
prog and the first non-flag token are preserved:

```python
def _summarize_segment(seg: str) -> str:
    seg = _strip_tail(seg)
    if not seg:
        return ""
    try:
        tokens = shlex.split(seg)
    except ValueError:
        tokens = seg.split()
    if not tokens:
        return ""
    prog = tokens[0]
    out = [prog]
    for tok in tokens[1:]:
        is_quoted = (f'"{tok}"' in seg or f"'{tok}'" in seg)
        if is_quoted and not tok.startswith("-"):
            patt = tok if len(tok) <= _PATTERN_CAP else tok[:_PATTERN_CAP] + "..."
            return f'{prog} "{patt}"'
        out.append(tok)
        if not tok.startswith("-"):
            return " ".join(out)   # stop at first non-flag, keep flags before it
    return " ".join(out)            # only flags (e.g. python3 -c)
```

This yields: `nl -ba tracing_agent.py` (flag `-ba` kept, stops at file);
`python3 -c` (only a flag follows); `git log` (`log` is first non-flag → `git log`,
`--oneline` never reached); `find templates` (`templates` first non-flag);
`cat persona.py`. Use THIS version of `_summarize_segment` and delete the first.

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH="$PWD" /Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_tui_widgets.py -k summarize -q`
Expected: PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
git add harness/tui/widgets/task_tree.py tests/test_tui_widgets.py
git commit -m "feat(tui): add summarize_command helper for TaskTree

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Apply summary in `lines_for`

**Files:**
- Modify: `harness/tui/widgets/task_tree.py:24-30` (`lines_for`)
- Test: `tests/test_tui_widgets.py`

**Interfaces:**
- Consumes: `summarize_command` from Task 1.
- Produces: rendered line uses the summary; the `(+N more)` portion is wrapped in
  the `$muted` token so it reads as secondary.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_tui_widgets.py`:

```python
def test_lines_for_uses_summary_and_muted_count():
    from harness.tui.state import TaskItem
    from harness.tui.widgets.task_tree import TaskTree
    tasks = (TaskItem(
        label='cd harness && cat persona.py && echo X && cat persona_config.py',
        status="done", tool_id="t1"),)
    line = TaskTree().lines_for(tasks)[0]
    assert "cat persona.py" in line
    assert "persona_config.py" not in line       # full command not shown
    assert "(+1 more)" in line
    assert "$muted" in line                        # count is muted
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="$PWD" /Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_tui_widgets.py::test_lines_for_uses_summary_and_muted_count -q`
Expected: FAIL — full label still rendered (`persona_config.py` present, no `(+1 more)`).

- [ ] **Step 3: Modify `lines_for`**

Replace the body of `lines_for` (`task_tree.py:24-30`). The current line is:

```python
            label = t.label[2:] if t.label.startswith("$ ") else t.label
            out.append(f"[${token}]{glyph}[/] [$foreground]{label}[/]")
```

Change to summarize, and split off the `(+N more)` so it can be muted:

```python
            label = t.label[2:] if t.label.startswith("$ ") else t.label
            summary = summarize_command(label)
            head, sep, tail = summary.partition("  (+")
            if sep:
                body = (f"[$foreground]{head}[/] "
                        f"[$muted](+{tail}[/]")
            else:
                body = f"[$foreground]{summary}[/]"
            out.append(f"[${token}]{glyph}[/] {body}")
```

(`partition("  (+")` splits `cat persona.py  (+1 more)` into
head=`cat persona.py`, tail=`1 more)`; the muted span re-prepends `(+`.)

- [ ] **Step 4: Run the test + full widget suite**

Run: `PYTHONPATH="$PWD" /Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_tui_widgets.py -q`
Expected: PASS (all existing + new). If any existing `task_tree` test asserted on
a full command string, update it to the summarized form (none currently do —
`test_tool_call_row_*` and `activity_region` tests don't read TaskTree labels).

- [ ] **Step 5: Commit**

```bash
git add harness/tui/widgets/task_tree.py tests/test_tui_widgets.py
git commit -m "feat(tui): render TaskTree lines as command summaries

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

- **Spec coverage:** heuristic (split/noise/first-real/summarize) → Task 1;
  `(+N more)` count → Task 1; muted count + apply site → Task 2; fallback → Task 1
  (`_cap(raw)`); width cap → Task 1 (`_cap`); quoted-pattern cap → Task 1
  (`_PATTERN_CAP`). All spec rows covered.
- **Placeholder scan:** none — full code in every code step.
- **Type consistency:** `summarize_command(str) -> str` used identically in both
  tasks; `_summarize_segment` final version is the one to keep (Task 1 Step 3
  explicitly says delete the first draft).
- **Final full-suite gate:** after Task 2, run the whole `tests/` suite once
  (`PYTHONPATH="$PWD" <primary>/.venv/bin/python -m pytest tests/ -q`) before shipping.
