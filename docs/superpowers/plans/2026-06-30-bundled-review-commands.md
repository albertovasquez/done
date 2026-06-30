# Bundled `/review` + `/quick-review` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two bundled commands that run a caveman-review-style review on a configurable, independent model via a one-shot model sub-call, returning terse findings inline.

**Architecture:** A `ReviewTool` agent tool (the `load_skill`/`load_memory` pattern) resolves a model from `done.conf [harness]` → env → (signal "propose"), runs a **one-shot `litellm.completion`** (the caveman-review prompt + the passed-in content) on that model — NOT a full agent runner — and returns findings. Two thin bundled skills (`/review`, `/quick-review`) tell the agent to call the tool. Model setting reuses a new generic `config.set_harness_setting` writer paired with the existing `config.harness_setting` reader.

**Tech Stack:** Python 3.11+, stdlib `tomllib`, litellm via `harness.vibeproxy`, pytest. Reuses `harness/compress_cli.py`'s one-shot-completion pattern and `harness/config.py`'s `[harness]`/preserve-on-write plumbing.

**Spec:** `docs/superpowers/specs/2026-06-30-bundled-review-commands-design.md`

## Global Constraints

- Python `>=3.11`. Test command from worktree root: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/ -q` (worktree shares the main checkout's `.venv`; `tests/conftest.py` resolves imports to this worktree).
- The review sub-call is a **one-shot completion** (mirror `compress_cli._build_call_model`'s `litellm.completion`), NOT `build_persona_agent`. Lazy-import litellm inside the function (vibeproxy must not import litellm at module load).
- Model resolution per command: `done.conf [harness] <key>` → `<ENV>` → `None` (signal propose). Keys: `review_model` / `quick_review_model`. Env: `REVIEW_MODEL` / `QUICK_REVIEW_MODEL`.
- **No independence enforcement** — no same-as-author check, no warning, no block. The resolved model runs as-is.
- Findings return **inline** (the tool's output string). No file writing, no git/PR gathering (content is passed in by the agent), no PR posting.
- Tests inject a fake model callable (no live LLM); never call a real model in CI.
- The review prompt is a **copy of the caveman-review prompt** (verbatim from `~/.agents/skills/caveman-review/SKILL.md` body — reproduced in Task 2).

## File Structure

- **Create** `harness/review.py` — model resolution (`resolve_review_model`), the caveman-review prompt constant, and `run_review(content, *, quick, call_model)` (one-shot dispatch). Pure-ish; `call_model` injected for tests.
- **Create** `harness/tools/review.py` — `ReviewTool` (agent tool, `load_skill`-style) that builds the real `call_model` (litellm/vibeproxy) and calls `run_review`.
- **Modify** `harness/config.py` — add generic `set_harness_setting(key, value)` (top-level `[harness]` writer, preserve-on-write).
- **Modify** `harness/tools/registry.py` — register `ReviewTool` so the agent can call it.
- **Create** `harness/skills/review/SKILL.md` + `harness/skills/quick-review/SKILL.md` — thin bundled skills instructing the agent to call the `review` tool.
- **Tests:** `tests/test_review.py`, `tests/test_review_tool.py`, append to `tests/test_config.py`.

Build order: config writer → review core (resolve + prompt + run_review) → tool → registry → skills.

---

## Task 1: Generic `[harness]` config writer

**Files:**
- Modify: `harness/config.py` (add `set_harness_setting` near `harness_setting` at line ~67 and `_serialize` at ~124)
- Test: `tests/test_config.py` (append)

**Interfaces:**
- Consumes: existing `config.harness_setting(key) -> str | None`, `config._load_raw() -> dict`, `config._serialize(agents, *, preserve=None, partial=None) -> str`, `config.conf_path()`, `config.load()`.
- Produces: `config.set_harness_setting(key: str, value: str) -> None` — sets a top-level `[harness]` key, preserving schema_version, all agent tables, and other top-level sections.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py  (append)
def test_set_harness_setting_roundtrip(isolated_config):
    from harness import config
    config.set_harness_setting("review_model", "claude-opus-4-8")
    assert config.harness_setting("review_model") == "claude-opus-4-8"


def test_set_harness_setting_preserves_agents_and_other_sections(isolated_config):
    from harness import config
    config.update_agent("default", backend="vibeproxy", model="m-x")
    conf = config.conf_path()
    conf.write_text(conf.read_text() + "\n[harness]\ndebug = true\n")
    config.set_harness_setting("review_model", "claude-opus-4-8")
    agents = config.load()
    assert agents["default"].model == "m-x"          # agent table survived
    assert "debug = true" in conf.read_text()         # other [harness] key survived
    assert config.harness_setting("review_model") == "claude-opus-4-8"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_config.py -q -k set_harness_setting`
Expected: FAIL — `AttributeError: module 'harness.config' has no attribute 'set_harness_setting'`.

- [ ] **Step 3: Write minimal implementation**

In `harness/config.py`, after `harness_setting`:

```python
def set_harness_setting(key: str, value: str) -> None:
    """Set a top-level [harness] string key in done.conf, preserving
    schema_version, all agent tables, and every other top-level section.
    Routes through _serialize(preserve=) so there is one serializer."""
    raw = _load_raw()
    harness = raw.get("harness")
    if not isinstance(harness, dict):
        harness = {}
    harness = {**harness, key: value}
    raw = {**raw, "harness": harness}
    text = _serialize(load(), preserve=raw)
    path = conf_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    import os
    os.replace(tmp, path)
```

> Note: `_serialize(load(), preserve=raw)` re-emits agent tables from `load()` and the `[harness]` section (now carrying `key`) from `preserve`. Confirm `_serialize` excludes `schema_version`/`agents` from the preserve re-emit (it does — `_OWNED`), so no duplication.

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_config.py -q`
Expected: PASS (existing + 2 new).

- [ ] **Step 5: Commit**

```bash
git add harness/config.py tests/test_config.py
git commit -m "feat(config): generic set_harness_setting writer for [harness] keys"
```

---

## Task 2: Review core — model resolution + prompt + one-shot dispatch

**Files:**
- Create: `harness/review.py`
- Test: `tests/test_review.py`

**Interfaces:**
- Consumes: `config.harness_setting(key)` (Task 1 reader, already exists).
- Produces:
  - `REVIEW_PROMPT: str` — the caveman-review instruction (verbatim copy).
  - `resolve_review_model(*, quick: bool) -> str | None` — `done.conf [harness] review_model|quick_review_model` → `REVIEW_MODEL|QUICK_REVIEW_MODEL` env → None.
  - `run_review(content: str, *, quick: bool, call_model) -> str` — builds the prompt (`REVIEW_PROMPT` + content), calls `call_model(prompt)`, returns findings. Raises `ValueError` on empty content.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_review.py
import pytest
from harness import review


def _isolate(monkeypatch, tmp_path, body=None):
    from harness import config
    d = tmp_path / "cfg"; d.mkdir()
    monkeypatch.setattr(config.paths, "config_dir", lambda: d)
    if body is not None:
        (d / "done.conf").write_text(body)


def test_resolve_prefers_done_conf_then_env_then_none(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path,
             'schema_version = 1\n\n[harness]\nreview_model = "from-conf"\n')
    monkeypatch.setenv("REVIEW_MODEL", "from-env")
    assert review.resolve_review_model(quick=False) == "from-conf"   # conf wins
    monkeypatch.delenv("REVIEW_MODEL", raising=False)
    _isolate(monkeypatch, tmp_path, 'schema_version = 1\n')          # empty conf
    monkeypatch.setenv("REVIEW_MODEL", "from-env")
    assert review.resolve_review_model(quick=False) == "from-env"    # env next
    monkeypatch.delenv("REVIEW_MODEL", raising=False)
    assert review.resolve_review_model(quick=False) is None          # then None


def test_resolve_quick_uses_quick_keys(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path,
             'schema_version = 1\n\n[harness]\nquick_review_model = "fast-m"\n')
    assert review.resolve_review_model(quick=True) == "fast-m"
    assert review.resolve_review_model(quick=False) is None          # non-quick key absent


def test_run_review_passes_prompt_and_content():
    seen = {}
    def fake_model(prompt: str) -> str:
        seen["prompt"] = prompt
        return "L42: bug: null deref. guard."
    out = review.run_review("- foo()\n+ foo(x)", quick=False, call_model=fake_model)
    assert out == "L42: bug: null deref. guard."
    assert "one line per finding" in seen["prompt"].lower()   # the caveman prompt is present
    assert "foo(x)" in seen["prompt"]                         # content is included


def test_run_review_rejects_empty_content():
    with pytest.raises(ValueError):
        review.run_review("   ", quick=False, call_model=lambda p: "x")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_review.py -q`
Expected: FAIL — `ModuleNotFoundError: harness.review`.

- [ ] **Step 3: Write minimal implementation**

```python
# harness/review.py
"""Model-bound code review: resolve a review model, run the caveman-review
prompt + content as a ONE-SHOT completion, return terse findings.

The point is independence — a model different from the one that wrote the code
catches more. Independence is the user's responsibility; this module does NOT
enforce it (no same-as-author check)."""
from __future__ import annotations

import os

from harness import config

# Verbatim copy of the caveman-review prompt (the skill we kept; this is the
# bundled, model-bound copy). Keep in sync intentionally — this is a fork.
REVIEW_PROMPT = """\
Write code review comments terse and actionable. One line per finding. \
Location, problem, fix. No throat-clearing.

Format: `L<line>: <problem>. <fix>.` — or `<file>:L<line>: ...` for multi-file diffs.
Severity prefix when mixed: `🔴 bug:` broken behavior · `🟡 risk:` fragile · \
`🔵 nit:` style/micro · `❓ q:` genuine question.
Drop: "I noticed that…", "it seems…", "you might consider…", hedging, restating \
the line, "great work". Keep: exact line numbers, exact symbol names in backticks, \
a concrete fix (not "consider refactoring"), the *why* when non-obvious.
Auto-clarity: write a normal paragraph for security findings / architectural \
disagreements, then resume terse.
Reviews only — do not write the fix, do not approve/request-changes."""

_KEYS = {
    False: ("review_model", "REVIEW_MODEL"),
    True: ("quick_review_model", "QUICK_REVIEW_MODEL"),
}


def resolve_review_model(*, quick: bool) -> str | None:
    """done.conf [harness] <key> -> <ENV> -> None (signal: propose a model)."""
    conf_key, env_key = _KEYS[quick]
    return config.harness_setting(conf_key) or os.environ.get(env_key) or None


def run_review(content: str, *, quick: bool, call_model) -> str:
    """Run the caveman-review prompt + content as one completion via call_model
    (prompt: str) -> str. quick is accepted for symmetry/logging; the prompt is
    the same. Raises ValueError on empty content."""
    if not content or not content.strip():
        raise ValueError("nothing to review")
    prompt = f"{REVIEW_PROMPT}\n\nReview the following:\n\n{content}"
    return call_model(prompt)
```

> The prompt is condensed from the caveman-review SKILL.md body. The test only
> asserts "one line per finding" is present — keep that phrase.

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_review.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add harness/review.py tests/test_review.py
git commit -m "feat(review): model resolution + caveman prompt + one-shot run_review"
```

---

## Task 3: ReviewTool (agent tool)

**Files:**
- Create: `harness/tools/review.py`
- Test: `tests/test_review_tool.py`

**Interfaces:**
- Consumes: `review.resolve_review_model(quick=)`, `review.run_review(content, quick=, call_model=)` (Task 2); `harness.vibeproxy` for the real model.
- Produces: `ReviewTool` with `name = "review"`, a tool schema accepting `{content: str, quick: bool=False, model: str|None=None}`, and `execute(self, args: dict, env) -> dict` returning `{"output": findings, "returncode": 0, "exception_info": None}`. On no-model-resolvable returns `{"output": "<no review model: set [harness] review_model in done.conf or pass model=>", "returncode": 1, ...}` (no crash).
- Produces: `_build_call_model(model_name: str)` — one-shot litellm completion (mirror `compress_cli._build_call_model`), lazy-import litellm.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_review_tool.py
from harness.tools.review import ReviewTool


class _Env: pass


def test_tool_runs_review_with_explicit_model(monkeypatch):
    # explicit model arg -> _build_call_model is used; stub it to avoid litellm
    import harness.tools.review as rt
    monkeypatch.setattr(rt, "_build_call_model", lambda name: (lambda p: f"[{name}] L1: nit: x."))
    tool = ReviewTool()
    out = tool.execute({"content": "- a\n+ b", "model": "sonnet"}, _Env())
    assert out["returncode"] == 0
    assert "[sonnet]" in out["output"]


def test_tool_resolves_model_from_config_when_no_arg(monkeypatch, tmp_path):
    from harness import config
    import harness.tools.review as rt
    d = tmp_path / "cfg"; d.mkdir()
    monkeypatch.setattr(config.paths, "config_dir", lambda: d)
    (d / "done.conf").write_text('schema_version = 1\n\n[harness]\nreview_model = "conf-m"\n')
    monkeypatch.setattr(rt, "_build_call_model", lambda name: (lambda p: f"[{name}] ok"))
    out = ReviewTool().execute({"content": "x"}, _Env())
    assert "[conf-m]" in out["output"]


def test_tool_no_model_returns_message_not_crash(monkeypatch, tmp_path):
    from harness import config
    d = tmp_path / "cfg"; d.mkdir()
    monkeypatch.setattr(config.paths, "config_dir", lambda: d)
    (d / "done.conf").write_text("schema_version = 1\n")
    monkeypatch.delenv("REVIEW_MODEL", raising=False)
    out = ReviewTool().execute({"content": "x"}, _Env())
    assert out["returncode"] == 1
    assert "review model" in out["output"].lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_review_tool.py -q`
Expected: FAIL — `ModuleNotFoundError: harness.tools.review`.

- [ ] **Step 3: Write minimal implementation**

```python
# harness/tools/review.py
"""ReviewTool — the agent calls this to run a code review on an independent
model. Mirrors the load_skill/load_memory tool shape. The review runs as a
one-shot completion (not a full agent)."""
from __future__ import annotations

from harness import review

SCHEMA = {
    "name": "review",
    "description": ("Review code/diff on a separate model (independent review "
                    "catches more than self-review). Pass the content to review. "
                    "quick=true uses the faster/cheaper quick-review model."),
    "input_schema": {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "the diff/code to review"},
            "quick": {"type": "boolean", "description": "use quick-review model"},
            "model": {"type": "string", "description": "explicit model override"},
        },
        "required": ["content"],
    },
}


def _build_call_model(model_name: str):
    """One-shot completion via litellm/vibeproxy (lazy import)."""
    import litellm
    from harness import vibeproxy
    model = vibeproxy.model_id(model_name)
    kwargs = vibeproxy.completion_kwargs()

    def call_model(prompt: str) -> str:
        resp = litellm.completion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            **kwargs,
        )
        return resp.choices[0].message.content or ""

    return call_model


class ReviewTool:
    name = "review"
    schema = SCHEMA

    def execute(self, args: dict, env) -> dict:
        content = args.get("content", "")
        quick = bool(args.get("quick", False))
        model_name = args.get("model") or review.resolve_review_model(quick=quick)
        if not model_name:
            key = "quick_review_model" if quick else "review_model"
            return {"output": f"no review model: set [harness] {key} in done.conf "
                              f"or pass model=", "returncode": 1, "exception_info": None}
        try:
            findings = review.run_review(content, quick=quick,
                                         call_model=_build_call_model(model_name))
        except ValueError as e:
            return {"output": str(e), "returncode": 1, "exception_info": None}
        return {"output": findings, "returncode": 0, "exception_info": None}
```

> Match `LoadSkillTool`'s actual attribute/return shape — read `harness/tools/load_skill.py` and align `name`/`schema`/`execute` to whatever the registry expects (the dict keys `output`/`returncode`/`exception_info` come from load_skill).

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_review_tool.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add harness/tools/review.py tests/test_review_tool.py
git commit -m "feat(review): ReviewTool agent tool (one-shot, model-bound)"
```

---

## Task 4: Register ReviewTool

**Files:**
- Modify: `harness/tools/registry.py` (register `ReviewTool` alongside the existing tools)
- Test: `tests/test_review_tool.py` (append a registry-membership test)

**Interfaces:**
- Consumes: `ReviewTool` (Task 3), the registry's existing registration mechanism.
- Produces: the `review` tool available to the agent.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_review_tool.py  (append)
def test_review_tool_is_registered():
    from harness.tools import registry
    names = registry.tool_names() if hasattr(registry, "tool_names") else None
    # Fallback: build the registry the way the app does and check for "review".
    # Adjust to the real registry API discovered in Step 3 below.
    assert names is not None and "review" in names
```

> Before writing this test, READ `harness/tools/registry.py` to learn the real
> registration API (how load_skill/read/edit are registered and how to list
> names). Replace the test body with the actual call that lists registered tool
> names, asserting `"review"` is present.

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_review_tool.py -q -k registered`
Expected: FAIL — `review` not in the registry.

- [ ] **Step 3: Write minimal implementation**

Read `harness/tools/registry.py`, find where `LoadSkillTool` (or similar) is registered, and add `ReviewTool` the same way. Example shape (align to the real file):

```python
from harness.tools.review import ReviewTool
# ... in the registration list/function alongside the others:
ReviewTool(),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_review_tool.py -q`
Expected: PASS. Also run `tests/test_tools*` if present to confirm no registry regression.

- [ ] **Step 5: Commit**

```bash
git add harness/tools/registry.py tests/test_review_tool.py
git commit -m "feat(review): register the review tool"
```

---

## Task 5: Bundled `/review` + `/quick-review` skills

**Files:**
- Create: `harness/skills/review/SKILL.md`
- Create: `harness/skills/quick-review/SKILL.md`
- Test: `tests/test_review.py` (append a skill-frontmatter validity check)

**Interfaces:**
- Consumes: the `review` tool (Tasks 3–4).
- Produces: `/review` and `/quick-review` as bundled, user-invocable skills whose body instructs the agent to call the `review` tool (and to run the resolve→propose→confirm→pin flow when no model is configured).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_review.py  (append)
def test_bundled_review_skills_parse():
    from pathlib import Path
    from harness import skills
    root = Path(__file__).resolve().parent.parent / "harness" / "skills"
    for name in ("review", "quick-review"):
        data, body = skills._parse_skill_md(root / name / "SKILL.md")
        assert data.get("name") == name           # frontmatter name matches dir
        assert data.get("description")
        assert "review" in body.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_review.py -q -k bundled`
Expected: FAIL — the skill dirs/files don't exist yet.

- [ ] **Step 3: Write minimal implementation**

`harness/skills/review/SKILL.md`:

```markdown
---
name: review
description: Review code or a diff on a separate, more capable model — independent review catches more than a model reviewing its own work. Use for "/review" or "review this".
---

# Review (independent model)

The user wants a code review run on a **different model** than the one writing
the code — independent eyes catch more.

To do it:
1. Gather what to review (e.g. run `git diff`, or use the content the user gave).
2. Call the `review` tool with `content` = that text (omit `quick`).
3. The tool resolves the review model from `done.conf [harness] review_model`,
   then the `REVIEW_MODEL` env var. **If neither is set**, propose a sensible
   strong model from the available models (`/models`), prefer one different from
   your own current model, tell the user your pick and why, and ask to confirm.
   On confirm, run the tool with `model=<picked>`, then offer to persist it
   (`[harness] review_model` in done.conf) so it stops asking.
4. Print the findings inline.
```

`harness/skills/quick-review/SKILL.md`:

```markdown
---
name: quick-review
description: Fast, cheap code review on a small separate model. Use for "/quick-review" — a quick independent pass.
---

# Quick review (fast independent model)

Same as `/review`, but a **fast/cheap** pass. Call the `review` tool with
`content` and `quick=true`.

The tool resolves the model from `done.conf [harness] quick_review_model`, then
`QUICK_REVIEW_MODEL`. If neither is set, propose a small/fast model from
`/models` (prefer one different from your own), confirm, run with `model=<picked>`,
and offer to persist it (`[harness] quick_review_model`). Print findings inline.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_review.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/skills/review/SKILL.md harness/skills/quick-review/SKILL.md tests/test_review.py
git commit -m "feat(review): bundled /review + /quick-review skills"
```

---

## Task 6: Full suite + docs

**Files:**
- Create: `docs/review.md` (user-facing reference)
- Modify: `README.md` (Highlights bullet + short section + docs-table entry)
- Test: full suite

- [ ] **Step 1: Write the docs**

`docs/review.md` — what `/review` and `/quick-review` do (independent-model review), model resolution (`done.conf [harness] review_model`/`quick_review_model` → env → propose), the no-enforcement note, and a `done.conf` example:

```toml
[harness]
review_model = "claude-opus-4-8"
quick_review_model = "claude-haiku-4-5-20251001"
```

README: add a Highlights bullet ("**Independent review.** `/review` and `/quick-review` run a review on a separate model — different eyes catch more …") and a short section linking to `docs/review.md`, mirroring the compress-aware section.

- [ ] **Step 2: Run the full suite**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/ -q`
Expected: PASS except the known pre-existing `tests/jobs/test_service_launchd.py` baseline failure (and the flaky TUI-Pilot cluster if it trips under the broad run — verify in isolation, not a regression).

- [ ] **Step 3: Commit**

```bash
git add docs/review.md README.md
git commit -m "docs: /review + /quick-review reference + README"
```

---

## Self-Review

**Spec coverage:**
- Model-bound sub-call (not prose) → Tasks 2–3 (one-shot completion in ReviewTool). ✓
- Content passed in by the agent → ReviewTool `content` arg; skills tell the agent to gather it. ✓
- Resolution `done.conf [harness]` → env → propose → confirm → pin → resolver (Task 2) + skill prose for propose/confirm/pin (Task 5) + `set_harness_setting` for the pin (Task 1). ✓
- No enforcement → no same-model check anywhere; documented in review.py docstring + Task 5 prose. ✓
- Inline output → tool returns findings string. ✓
- `/review` vs `/quick-review` differ only by tier/keys → `quick` flag throughout. ✓
- Reuse harness_setting / preserve-on-write / one-shot-completion pattern → Tasks 1–3. ✓
- Caveman prompt copy → `REVIEW_PROMPT` (Task 2). ✓
- Non-goals (git gathering, enforcement, file output, PR posting) → none implemented. ✓

**Placeholder scan:** Two tasks (4 registry, parts of 3) say "read the real file and align to its API" — these are NOT lazy placeholders: the registry/tool-shape API (`registry.py`, `LoadSkillTool`'s exact return contract) must be matched against live code the implementer reads in-place; the surrounding code and tests are complete. Every code step has real code.

**Type consistency:** `resolve_review_model(*, quick)` and `run_review(content, *, quick, call_model)` consistent across review.py (Task 2), ReviewTool (Task 3). `set_harness_setting(key, value)` consistent (Task 1 → used conceptually by the pin flow). Tool return dict `{output, returncode, exception_info}` consistent (Tasks 3–4). `[harness]` keys `review_model`/`quick_review_model` consistent across resolver, tool message, skills, docs.

**Known follow-ups (note at handoff):** the propose/confirm/pin flow lives in *skill prose* (the agent executes it), not code — so it's not unit-tested end-to-end; the *pieces* it uses (`resolve_review_model` returning None, `set_harness_setting`) are tested. Real-model dispatch (litellm) only runs with a model configured + auth, like compress.
