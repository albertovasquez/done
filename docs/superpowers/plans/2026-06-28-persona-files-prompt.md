# Teach the Agent Its Persona Files Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `# Persona files` section to the agent's base prompt naming its 3 persona files, their purpose, and the concrete absolute path of the active persona.

**Architecture:** `render_base_prompt` gains two optional args (`persona_id`, `persona_dir`); when both are present it appends the section. The active persona id + absolute path are resolved at the `acp_agent.py:360` call site from `state.workspace_dir` (the per-session workspace) and passed in — base_prompt stays pure (no I/O).

**Tech Stack:** Python 3.11, the ACP engine, pytest. Worktree: `.claude/worktrees/persona-files-prompt` (branch `persona-files-prompt`).

**Source spec:** `docs/superpowers/specs/2026-06-28-persona-files-prompt-design.md`.

## Global Constraints

- **base_prompt stays PURE:** no I/O, no `Path` ops, no `config_dir()` read inside `render_base_prompt`. Resolved values are passed IN.
- **New args OPTIONAL + omit-when-absent:** when `persona_id` or `persona_dir` is None, NO `# Persona files` section is rendered — byte-identical to today for callers that don't pass them.
- **No branch on "default":** one code path; the default persona renders like any other.
- **Call site uses `state.workspace_dir`** (the per-session workspace), NOT `self._workspace_dir` — so after a C2c persona switch the path matches the seat serving the turn (mirrors how persona/memory already resolve).
- **Additive only:** no change to persona content injection or the chat/agent base_block wiring.
- **Work in this worktree**; run pytest with the worktree as cwd (editable-install shadowing). Test command: `.venv/bin/python -m pytest tests/ -q` from the worktree root.

---

## File Structure

- **Modify** `harness/base_prompt.py` — `render_base_prompt` gains `persona_id`/`persona_dir` + the `# Persona files` section.
- **Modify** `harness/acp_agent.py:360` — pass the resolved persona id + abs path from `state.workspace_dir`.
- **Tests:** `tests/test_base_prompt.py` (the section render + omit-when-absent + default), `tests/test_acp_session_context.py` (the call site threads the active path — only if an existing prompt-driving test makes this easy to assert; otherwise the base_prompt unit tests + a focused call-site assertion suffice).

---

### Task 1: Persona-files section in `render_base_prompt` + call-site wiring

**Files:**
- Modify: `harness/base_prompt.py` (`render_base_prompt`, ~L47-58)
- Modify: `harness/acp_agent.py` (the `render_base_prompt` call, ~L360-362)
- Test: `tests/test_base_prompt.py`, `tests/test_acp_session_context.py`

**Interfaces:**
- Produces: `render_base_prompt(*, model_id, cwd, system_line, cutoff=…, persona_id: str | None = None, persona_dir: str | None = None) -> str` — appends a `# Persona files` section iff both persona args are truthy.

- [ ] **Step 1: Write the failing base_prompt tests**

```python
# tests/test_base_prompt.py — add (match the file's existing test style)
from harness.base_prompt import render_base_prompt


def _render(**kw):
    base = dict(model_id="m", cwd="/proj", system_line="TestOS")
    base.update(kw)
    return render_base_prompt(**base)


def test_persona_files_section_present_with_args():
    out = _render(persona_id="fred", persona_dir="/abs/agents/fred")
    assert "# Persona files" in out
    assert "fred" in out
    assert "/abs/agents/fred" in out
    assert "SOUL.md" in out and "IDENTITY.md" in out and "USER.md" in out


def test_persona_files_section_absent_without_args():
    out = _render()                       # no persona_id/persona_dir
    assert "# Persona files" not in out
    # the rest of the base block is intact
    assert "# Environment" in out


def test_persona_files_section_absent_if_only_one_arg():
    assert "# Persona files" not in _render(persona_id="fred")          # no dir
    assert "# Persona files" not in _render(persona_dir="/abs/fred")    # no id


def test_persona_files_section_renders_for_default():
    out = _render(persona_id="default", persona_dir="/abs/agents/default")
    assert "# Persona files" in out
    assert "default" in out               # no special-casing of "default"


def test_persona_files_section_is_byte_identical_no_args():
    # adding the optional args must not change the no-args render at all
    before = render_base_prompt(model_id="m", cwd="/p", system_line="OS")
    assert "# Persona files" not in before
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_base_prompt.py -q -k persona_files`
Expected: FAIL — `render_base_prompt() got an unexpected keyword argument 'persona_id'`.

- [ ] **Step 3: Implement the section in `render_base_prompt`**

Replace `harness/base_prompt.py:47-58`:

```python
def render_base_prompt(*, model_id: str, cwd: str, system_line: str,
                       cutoff: str = KNOWLEDGE_CUTOFF,
                       persona_id: str | None = None,
                       persona_dir: str | None = None) -> str:
    """Return the base block: the static policy, a runtime # Environment section,
    and (when persona_id + persona_dir are given) a # Persona files section naming
    the editable persona trio + its absolute path. Pure — no I/O, no globals read;
    the persona id + path are resolved by the caller and passed in."""
    env = (
        "\n\n# Environment\n"
        f"- Working directory: {cwd}\n"
        f"- Model: {model_id}\n"
        f"- Knowledge cutoff: {cutoff}\n"
        f"- OS: {system_line}\n"
    )
    persona = ""
    if persona_id and persona_dir:
        persona = (
            "\n\n# Persona files\n"
            f'You are running as the persona "{persona_id}". Its files live in '
            f"{persona_dir} :\n"
            "- SOUL.md — your tone, behavior, and boundaries\n"
            "- IDENTITY.md — your name, vibe, and emoji\n"
            "- USER.md — who the user is and how they want to be addressed\n"
            "When the user asks you to update your persona — your soul, identity, "
            "how you behave, or what you know about them — Read and then Edit the "
            "relevant file in that directory.\n"
        )
    return BASE_POLICY + env + persona
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_base_prompt.py -q`
Expected: PASS (new persona tests + the existing base_prompt tests — the no-args render is unchanged).

- [ ] **Step 5: Write the failing call-site test**

The call site must pass the ACTIVE session workspace. Add a focused test that the rendered base block (via the prompt path) contains the active persona path. Read `tests/test_acp_session_context.py` first for its prompt-driving harness (`_FakeConn`/`_build`/`_prompt` or similar). If that harness can capture the base_block / system prompt sent to the model, assert it contains the active workspace path. If capturing the base_block is awkward in that harness, instead add a direct unit test of the call-site behavior:

```python
# tests/test_acp_agent.py — a focused assertion that the call site resolves from state.workspace_dir.
# Build a HarnessAgent with a named workspace, drive a prompt, and assert the base_prompt
# render received the workspace path. The cleanest seam: monkeypatch base_prompt.render_base_prompt
# to capture its kwargs, then run a prompt and assert persona_id/persona_dir match the session workspace.
import asyncio
from pathlib import Path
from harness import base_prompt


def test_base_prompt_receives_active_persona_path(monkeypatch, tmp_path):
    captured = {}
    real = base_prompt.render_base_prompt
    def spy(**kw):
        captured.update(kw)
        return real(**kw)
    monkeypatch.setattr(base_prompt, "render_base_prompt", spy)
    # build an agent whose session workspace is tmp_path/agents/fred
    ws = tmp_path / "agents" / "fred"; ws.mkdir(parents=True)
    # (reuse the test's existing agent-construction + prompt-driving helper; drive ONE
    #  chat or agent prompt so the base_block render runs)
    # ... construct agent with workspace_dir=ws, _cwd set, run a prompt ...
    # then:
    assert captured.get("persona_id") == "fred"
    assert captured.get("persona_dir") == str(ws)
```

**IMPORTANT for the implementer:** this test sketch needs the file's real agent/prompt-driving harness. Read `tests/test_acp_session_context.py` (the `_build`/`_prompt` helpers) and `tests/test_acp_agent.py` (the `_make_agent` + ext_method patterns) FIRST, then write the test using whichever harness most directly drives a `prompt()` so the `render_base_prompt` call at acp_agent.py:360 executes with a real `state.workspace_dir`. The assertion is: the spy captured `persona_id == ws.name` and `persona_dir == str(ws)`. Mock mode (`backend="mock"`) avoids needing a real model.

- [ ] **Step 6: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/ -q -k "base_prompt_receives_active_persona_path"`
Expected: FAIL — the call site doesn't pass persona_id/persona_dir yet (captured values are None).

- [ ] **Step 7: Implement the call-site wiring**

Replace `harness/acp_agent.py:360-362`:

```python
        ws = state.workspace_dir
        base_block = base_prompt.render_base_prompt(
            model_id=(model_id or "mock"),
            cwd=state.cwd, system_line=platform.platform(),
            persona_id=(ws.name if ws else None),
            persona_dir=(str(ws) if ws else None))
```

(`state.workspace_dir` is a `Path | None`; `ws.name` is the persona id, `str(ws)` the
absolute path. In the live app `ws` is always set; the `if ws else None` keeps the section
omitted if a workspace is ever absent.)

- [ ] **Step 8: Run to verify pass + full suite**

Run: `.venv/bin/python -m pytest tests/ -q -k "base_prompt or persona_path"` then the full suite `.venv/bin/python -m pytest tests/ -q`.
Expected: PASS. The existing prompt-driving + base_prompt tests stay green; the new tests pass. (Historical Textual flake `test_pilot_streams_deltas_into_one_markdown_widget` — re-run alone if it's the only failure.)

- [ ] **Step 9: Confirm primary checkout clean**

Run: `git -C /Users/alberto/Work/quiubo/harness status --short`
Expected: empty.

- [ ] **Step 10: Commit**

```bash
git add harness/base_prompt.py harness/acp_agent.py tests/
git commit -m "feat(persona): base prompt names the persona files + active path (persona-files-prompt)"
```

---

## Self-Review

**1. Spec coverage:**
- §3 `render_base_prompt(persona_id, persona_dir)` + the `# Persona files` section → Task 1 steps 1-4. ✓
- §3 call site passes resolved values from `state.workspace_dir` → Task 1 steps 5-7. ✓
- §5 omit-when-absent (byte-identical) → tests in step 1. ✓
- §2/§5 default included, no branch → `test_persona_files_section_renders_for_default`. ✓
- §8 crux (uses `state.workspace_dir`, not `self._workspace_dir`) → the call-site code + the spy test asserting `persona_dir == str(ws)`. ✓
- §6 purity → the function has no Path/I/O; existing pure-render tests stay green. ✓

**2. Placeholder scan:** Step 5's call-site test is a sketch that says "reuse the file's real harness" — deliberate: the prompt-driving harness names are the truth and must be matched, and the assertion (spy captured persona_id==ws.name, persona_dir==str(ws)) is concrete. Everything else is complete code.

**3. Type consistency:** `render_base_prompt(..., persona_id: str | None, persona_dir: str | None)`; call site passes `ws.name`/`str(ws)` from `state.workspace_dir: Path | None`. Consistent.

**Crux for Codex review:** the call-site change (must use `state.workspace_dir`, the per-session workspace, so a C2c-switched persona gets the right path) + the purity preservation (no I/O added to base_prompt).
