# AGENTS.md Instruction Layer Implementation Plan (#47)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Inject a three-tier `AGENTS.md` instruction layer (persona > project > global) into the agent's system prompt, read-only, content-gated, reaching BOTH the agent and chat paths, with a strict no-op when no AGENTS.md files exist.

**Architecture:** A new `harness/agents.py` resolver (mirrors `memory.py`'s content-gated pattern) composes the three tiers into one block. The block is folded into `base_block` via `render_base_prompt` — `base_block` is the policy block consumed by both the agent runner AND `ChatHandler`. Gate helpers (`_meaningful`/`_trim`/`_HTML_COMMENT`) move to a leaf `harness/textgate.py` to break the import cycle. `compose_context`/`TurnContext`/`tracing_agent` are untouched.

**Tech Stack:** Python 3.10+, pytest, the vendored mini-swe-agent engine. Test runner: `.venv/bin/python -m pytest tests/ -q` from the worktree root.

## Global Constraints

- Work ONLY in worktree `/Users/alberto/Work/Quiubo/harness/.claude/worktrees/agents-md-47` (branch `agents-md-47`). Run pytest with the worktree's own `.venv` (editable-install shadowing trap).
- ZERO upstream edits (`upstream/` untouched).
- Backward compatibility is a HARD requirement: with no AGENTS.md anywhere and `agents_block` defaulting to None, the prompt is byte-identical. Each task ends with a no-op assertion.
- Precedence: **persona > project > global**. Enforced by explicit scope headers + a precedence preamble sentence, NOT prompt position alone.
- Project AGENTS.md = single file at launch `cwd`, no upward walk (documented limitation).
- TDD: failing test first, minimal impl, green, commit. Frequent commits.

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `harness/textgate.py` | `_meaningful`, `_trim`, `_HTML_COMMENT` (leaf, no deps) | Create (move from persona) |
| `harness/persona.py` | persona content layer | Modify: import gate helpers from textgate |
| `harness/memory.py` | memory content layer | Modify: import gate helpers from textgate |
| `harness/agents.py` | three-tier AGENTS.md resolver | Create |
| `harness/base_prompt.py` | base system prompt | Modify: `agents_block` param |
| `harness/run_traced.py` | CLI dispatch | Modify: resolve + pass agents_block |
| `harness/acp_agent.py` | ACP dispatch | Modify: resolve + pass agents_block |
| `tests/test_textgate.py`, `test_agents.py`, `test_base_prompt.py` | tests | Create/modify |
| `docs/agents-md.md` | how it works | Create |

---

## Task 1: Extract gate helpers to `harness/textgate.py` (no-op refactor)

**Files:**
- Create: `harness/textgate.py`
- Modify: `harness/persona.py` (remove the 3 defs + `re` import if now unused; import from textgate), `harness/memory.py:17` (import from textgate)
- Test: `tests/test_textgate.py`

**Interfaces:**
- Produces: `harness.textgate._meaningful(raw: str) -> bool`, `_trim(text: str, limit: int) -> tuple[str, bool]`, `_HTML_COMMENT` (compiled regex).
- persona.py re-exports them (`from harness.textgate import _meaningful, _trim, _HTML_COMMENT`) so any `from harness.persona import _meaningful` keeps working.

- [ ] **Step 1: Write the failing test**

Create `tests/test_textgate.py`:
```python
import sys
sys.path.insert(0, "upstream/src"); sys.path.insert(0, ".")
from harness.textgate import _meaningful, _trim, _HTML_COMMENT

def test_meaningful_blank_and_comment_only():
    assert _meaningful("real text") is True
    assert _meaningful("   \n  ") is False
    assert _meaningful("<!-- only a comment -->\n") is False
    assert _meaningful("# Heading") is True          # '#' is markdown, not a comment

def test_trim_caps_and_flags():
    assert _trim("abc", 10) == ("abc", False)
    assert _trim("abcdef", 3) == ("abc", True)

def test_persona_reexports_for_backcompat():
    from harness.persona import _meaningful as pm, _trim as pt
    assert pm("x") is True and pt("xy", 1) == ("x", True)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_textgate.py -v`
Expected: FAIL — `No module named 'harness.textgate'`.

- [ ] **Step 3: Create the leaf module**

Create `harness/textgate.py`:
```python
"""Leaf content-gate helpers shared by persona/memory/agents content layers.
No harness imports — keeps the content-layer modules cycle-free."""

from __future__ import annotations

import re

_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def _meaningful(raw: str) -> bool:
    """True if the file has injectable content — anything but whitespace remains
    after HTML comments are removed. A comment-only template => False (skipped,
    never injected). HTML comments only: '#' is a Markdown heading, NOT a comment."""
    return bool(_HTML_COMMENT.sub("", raw).strip())


def _trim(text: str, limit: int) -> tuple[str, bool]:
    """Cap text at `limit` chars. Returns (text, was_trimmed)."""
    if len(text) <= limit:
        return text, False
    return text[:limit], True
```

- [ ] **Step 4: Update persona.py and memory.py to import from the leaf**

In `harness/persona.py`: remove the `_HTML_COMMENT = re.compile(...)` line (26), the `_meaningful` def (81-87), and the `_trim` def (134-138). Add after the existing imports:
```python
from harness.textgate import _meaningful, _trim, _HTML_COMMENT  # re-exported for back-compat
```
If `re` is no longer used elsewhere in persona.py, remove `import re` (grep first: `grep -n "re\." harness/persona.py`).

In `harness/memory.py:17`, change:
```python
from harness.persona import _meaningful, _trim
```
to:
```python
from harness.textgate import _meaningful, _trim
```

- [ ] **Step 5: Run textgate tests + the persona/memory suites (no-op proof)**

Run: `.venv/bin/python -m pytest tests/test_textgate.py tests/test_persona.py tests/test_memory.py -q`
Expected: PASS. Behavior identical — functions moved, not changed.

- [ ] **Step 6: Commit**

```bash
git add harness/textgate.py harness/persona.py harness/memory.py tests/test_textgate.py
git commit -m "refactor: extract content-gate helpers to harness/textgate.py (leaf, cycle-free)"
```

---

## Task 2: `harness/agents.py` resolver

**Files:**
- Create: `harness/agents.py`
- Test: `tests/test_agents.py`

**Interfaces:**
- Consumes: `textgate._meaningful`, `textgate._trim`.
- Produces: `AGENTS_FILE`, `MAX_AGENTS_CHARS`, `@dataclass AgentsLoad(block, injected, skipped)`, `resolve_agents(*, persona_dir, project_cwd, global_dir) -> AgentsLoad`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_agents.py`:
```python
import sys
sys.path.insert(0, "upstream/src"); sys.path.insert(0, ".")
from pathlib import Path
from harness.agents import resolve_agents, AgentsLoad, MAX_AGENTS_CHARS

def _write(d: Path, body: str):
    d.mkdir(parents=True, exist_ok=True)
    (d / "AGENTS.md").write_text(body, encoding="utf-8")

def test_none_present_is_empty_noop(tmp_path):
    load = resolve_agents(persona_dir=tmp_path/"p", project_cwd=tmp_path/"c", global_dir=tmp_path/"g")
    assert load == AgentsLoad()          # empty block, no injected, no skipped-as-error
    assert load.block == ""

def test_all_three_ordered_global_project_persona(tmp_path):
    _write(tmp_path/"g", "GLOBAL RULES")
    _write(tmp_path/"c", "PROJECT RULES")
    _write(tmp_path/"p", "PERSONA RULES")
    b = resolve_agents(persona_dir=tmp_path/"p", project_cwd=tmp_path/"c", global_dir=tmp_path/"g").block
    assert b.index("GLOBAL RULES") < b.index("PROJECT RULES") < b.index("PERSONA RULES")
    assert "persona over project over global" in b.lower()      # precedence preamble
    assert "## Global instructions" in b and "## Persona instructions" in b

def test_blank_tier_skipped(tmp_path):
    _write(tmp_path/"g", "<!-- nothing -->\n")
    _write(tmp_path/"p", "REAL")
    load = resolve_agents(persona_dir=tmp_path/"p", project_cwd=None, global_dir=tmp_path/"g")
    assert "REAL" in load.block and "Global instructions" not in load.block

def test_unreadable_recorded_not_raised(tmp_path):
    p = tmp_path/"p"; p.mkdir()
    (p/"AGENTS.md").write_bytes(b"\xff\xfe bad")
    load = resolve_agents(persona_dir=p, project_cwd=None, global_dir=None)
    assert load.skipped and load.block == ""

def test_over_cap_trimmed(tmp_path):
    _write(tmp_path/"p", "x" * (MAX_AGENTS_CHARS + 500))
    load = resolve_agents(persona_dir=tmp_path/"p", project_cwd=None, global_dir=None)
    assert "truncated" in load.block.lower()

def test_none_dirs_safe():
    assert resolve_agents(persona_dir=None, project_cwd=None, global_dir=None) == AgentsLoad()
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_agents.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `harness/agents.py`**

```python
"""Three-tier AGENTS.md instruction layer: compose global + project + persona
AGENTS.md into one content-gated block for the system prompt. Read-only; mirrors
memory.py's gate/trim/skip discipline. Never raises — a turn never fails on AGENTS.md."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from pathlib import Path

from harness.textgate import _meaningful, _trim

logger = logging.getLogger("harness.agents")

AGENTS_FILE = "AGENTS.md"
MAX_AGENTS_CHARS = 8000          # per-tier trim cap (memory's order of magnitude)

_PREAMBLE = ("# Instructions\n\n"
             "Standing instructions for this session. When they conflict, follow "
             "persona over project over global.\n")


@dataclass
class AgentsLoad:
    block: str = ""
    injected: list[str] = field(default_factory=list)            # scope labels read
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (label, reason)


def _read_tier(dir_: Path | None, label: str, load: AgentsLoad) -> str | None:
    """Read one tier's AGENTS.md; return '## <label> instructions\\n<body>' or None
    when the dir is None/absent or the file is missing/blank/inert/unreadable."""
    if dir_ is None:
        return None
    path = Path(dir_) / AGENTS_FILE
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as e:
        load.skipped.append((label, type(e).__name__))
        return None
    if not _meaningful(raw):
        load.skipped.append((label, "blank"))
        return None
    body, trimmed = _trim(raw, MAX_AGENTS_CHARS)
    if trimmed:
        body = body + "\n\n…[truncated]…"
    load.injected.append(label)
    return f"## {label} instructions\n{body}"


def resolve_agents(*, persona_dir: Path | None, project_cwd: Path | None,
                   global_dir: Path | None) -> AgentsLoad:
    """Compose global + project + persona AGENTS.md, content-gated, lowest-precedence
    first (so persona sits last/closest to the task). Precedence preamble is added
    only when at least one tier has content. No tier present => empty AgentsLoad."""
    load = AgentsLoad()
    sections = []
    for dir_, label in [(global_dir, "Global"), (project_cwd, "Project"),
                        (persona_dir, "Persona")]:
        section = _read_tier(dir_, label, load)
        if section is not None:
            sections.append(section)
    if load.injected:
        load.block = "\n\n" + _PREAMBLE + "\n" + "\n\n".join(sections)
    return load
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_agents.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add harness/agents.py tests/test_agents.py
git commit -m "feat(agents): three-tier AGENTS.md resolver (content-gated, precedence preamble)"
```

---

## Task 3: `render_base_prompt` gains `agents_block`

**Files:**
- Modify: `harness/base_prompt.py`
- Test: `tests/test_base_prompt.py`

**Interfaces:**
- Consumes: nothing new (takes a string).
- Produces: `render_base_prompt(..., agents_block: str | None = None)` appends it after `skills_menu`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_base_prompt.py`:
```python
def test_base_prompt_omits_agents_when_none():
    a = base_prompt.render_base_prompt(model_id="m", cwd="/x", system_line="os")
    b = base_prompt.render_base_prompt(model_id="m", cwd="/x", system_line="os", agents_block=None)
    assert a == b and "# Instructions" not in a

def test_base_prompt_appends_agents_after_menu():
    out = base_prompt.render_base_prompt(
        model_id="m", cwd="/x", system_line="os",
        skills_menu="\n\n# Skills\n\n- **a** — d",
        agents_block="\n\n# Instructions\n\nfollow persona...")
    assert out.index("# Skills") < out.index("# Instructions")    # agents after menu
    assert out.endswith("follow persona...")
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_base_prompt.py -k agents -v`
Expected: FAIL — unexpected `agents_block` kwarg.

- [ ] **Step 3: Implement**

In `harness/base_prompt.py`, add the param and append it last:
```python
def render_base_prompt(*, model_id: str, cwd: str, system_line: str,
                       cutoff: str = KNOWLEDGE_CUTOFF,
                       persona_id: str | None = None,
                       persona_dir: str | None = None,
                       skills_menu: str | None = None,
                       agents_block: str | None = None) -> str:
    ...
    return BASE_POLICY + env + persona + (skills_menu or "") + (agents_block or "")
```
Update the docstring to mention the AGENTS.md instructions block.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_base_prompt.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/base_prompt.py tests/test_base_prompt.py
git commit -m "feat(base_prompt): optional agents_block appended after skills menu"
```

---

## Task 4: Dispatch wiring (both paths) + repo-root test-leak audit

**Files:**
- Modify: `harness/run_traced.py` (~line 171), `harness/acp_agent.py` (~line 373)
- Test: audit + fix any prompt-asserting test that runs from this repo cwd; integration assertions

**Interfaces:**
- Consumes: `agents.resolve_agents`, `paths.config_dir()`.
- Produces: `base_block` carries AGENTS.md in both CLI and ACP; both runner and ChatHandler inherit it.

- [ ] **Step 1: Write the failing integration test**

Add to `tests/test_run_traced.py` (use its existing harness; assert on the built base_block / prompt). Minimal target:
```python
def test_agents_md_from_project_cwd_reaches_base_block(tmp_path, monkeypatch):
    (tmp_path / "AGENTS.md").write_text("PROJECT POLICY XYZ", encoding="utf-8")
    # build base_block the way run_traced does, with cwd=tmp_path
    from harness import agents, base_prompt, paths
    load = agents.resolve_agents(persona_dir=None, project_cwd=tmp_path,
                                 global_dir=paths.config_dir())
    bb = base_prompt.render_base_prompt(model_id="mock", cwd=str(tmp_path),
                                        system_line="os", agents_block=load.block)
    assert "PROJECT POLICY XYZ" in bb
```
(If a higher-fidelity dispatch test fixture exists, prefer asserting through it.)

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_run_traced.py -k agents_md -v`
Expected: FAIL until wiring exists (or PASS at unit level proving the contract — then wire dispatch).

- [ ] **Step 3: Wire `run_traced.py`**

At the `base_block` build (~line 171), before it add:
```python
    from harness import agents as _agents
    _agents_load = _agents.resolve_agents(
        persona_dir=workspace_dir, project_cwd=args.cwd,
        global_dir=_paths.config_dir())
```
and pass into `render_base_prompt`:
```python
        skills_menu=skills.compose_menu(_menu_metas),
        agents_block=_agents_load.block)
```
(`args.cwd` and `_paths` are already in scope here.)

- [ ] **Step 4: Wire `acp_agent.py`**

At the per-turn `base_block` build (~line 373), before it add (using the already-computed `ws` = `state.workspace_dir` and `state.cwd`):
```python
        from harness import agents as _agents
        from harness import paths as _paths
        _agents_load = _agents.resolve_agents(
            persona_dir=ws, project_cwd=Path(state.cwd) if state.cwd else None,
            global_dir=_paths.config_dir())
```
and add `agents_block=_agents_load.block` to the `render_base_prompt(...)` call. Because `base_block` already flows to BOTH the chat branch (`ChatHandler(base_block=base_block)`) and the agent branch, both inherit AGENTS.md with no further change.

- [ ] **Step 5: Audit prompt-asserting tests for the repo-root AGENTS.md leak**

Run: `.venv/bin/python -m pytest tests/ -q` from the worktree (cwd = worktree root, which HAS an `AGENTS.md`). Any test that builds a real dispatch prompt from cwd and asserts exact/absent content may now see the repo-root AGENTS.md. For each failure: pass an explicit temp or `None` `project_cwd`/`cwd` so the test stays hermetic. Document each change in its commit.

- [ ] **Step 6: Full suite green**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS (731 baseline + new; any leak-audited test fixed).

- [ ] **Step 7: Commit**

```bash
git add harness/run_traced.py harness/acp_agent.py tests/
git commit -m "feat(dispatch): resolve + inject AGENTS.md into base_block (both agent + chat paths)"
```

---

## Task 5: Docs + Codex diff review + ship

**Files:**
- Create: `docs/agents-md.md`

- [ ] **Step 1: Write `docs/agents-md.md`** — the three tiers (persona/project/global) + dirs, precedence (persona > project > global, enforced by headers + preamble), the no-op guarantee, the launch-cwd limitation (no upward walk), and that it reaches both agent and chat. Cross-link from `docs/router-flows.md`.

- [ ] **Step 2: Full suite green**

Run: `.venv/bin/python -m pytest tests/ -q`

- [ ] **Step 3: Smoke** — build a base_block with a temp project AGENTS.md and confirm the instructions block appears; confirm byte-identical with none present.

- [ ] **Step 4: Codex diff review** — `git diff origin/main...agents-md-47 -- harness/` to codex:codex-rescue; verify findings against live code; fold blockers/majors. Caveman-review the diff for terse quality signal.

- [ ] **Step 5: Commit docs + open PR + ship**

```bash
git add docs/agents-md.md
git commit -m "docs: AGENTS.md three-tier instruction layer"
git push -u origin agents-md-47
gh pr create --repo albertovasquez/done --base main --title "AGENTS.md three-tier instruction layer (#47)" --body "..."
```
Then ship (test → merge --squash --delete-branch → reconcile main).

---

## Self-Review

**Spec coverage:** Task 1 = textgate extraction (fixes import cycle, Codex #1). Task 2 = resolver (precedence headers + preamble, Codex #4). Task 3 = base_prompt arg. Task 4 = dispatch into base_block reaching both paths (Codex #2) + repo-root leak audit (Codex #5). Task 5 = docs (launch-cwd limitation, Codex #3) + review + ship. All spec sections mapped.

**Placeholder scan:** No TBD/TODO; every code step shows code. PR body `"..."` filled at Task 5 Step 5.

**Type consistency:** `AgentsLoad(block, injected, skipped)`, `resolve_agents(persona_dir, project_cwd, global_dir)`, `_read_tier(dir_, label, load)`, `render_base_prompt(..., agents_block=)` consistent across tasks. `textgate._meaningful/_trim` used by persona/memory/agents identically.

**No-op:** asserted in Tasks 1 (refactor identical), 2 (none-present empty), 3 (agents_block None byte-identical), 4 (absent => unchanged).
