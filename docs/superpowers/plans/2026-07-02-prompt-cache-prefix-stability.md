# Prompt-Cache Prefix Stability (PR 2 of #139) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the agent/chat system prompt byte-stable across turns (per the append-only invariant in `docs/superpowers/specs/2026-07-02-prompt-cache-prefix-stability-design.md`), and make cache hits observable.

**Architecture:** Four independent moves: (1) extract cache-read counts from LLM usage into events + TUI footer; (2) split the volatile `# Environment` block out of `render_base_prompt` to the system-prompt tail and reorder the spine most-stable-first; (3) move per-turn `skill_block` from the system message to the instance (user) message; (4) hash the prompt blocks per session and emit a `cache.boundary` trace event naming which block changed.

**Tech Stack:** Python 3.11, pytest, existing harness seams (`TracingAgent`, `MiniSweAgentRunner`, `ChatHandler`, `render_base_prompt`). No new dependencies.

## Global Constraints

- Work in the dedicated git worktree; NEVER commit on `main` (AGENTS.md #1).
- Test command from the worktree root: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/ -q` (the repo conftest resolves the worktree source; always run from the worktree root, never the primary checkout).
- Known pre-existing baseline failures (NOT yours to fix, they fail on main too): `tests/test_system_skills.py::test_catalog_is_exactly_the_maturity_spine` and `tests/test_tui_snapshots.py::test_completed_turn_ordering`. Additionally `test_pilot_streams_deltas_into_one_markdown_widget` is a known ~1-in-5 flake on unmodified main (transient #working indicator race) — retry it before suspecting your diff.
- Never modify anything under `upstream/` (vendored engine, zero-upstream-edits policy).
- Prompt SEMANTICS must not change — blocks move and reorder, but their text content is unchanged except where a task explicitly adds a delimiter line.

---

### Task 1: Cache-read observability (`_usage_from_extra` + TUI footer)

**Files:**
- Modify: `harness/tracing_agent.py:40-67` (`_usage_from_extra`)
- Modify: `harness/tui/app.py` (~line 180 `self._tokens = 0`; lines 1644-1647 usage intake; lines 629-633 `_context_tagline`)
- Test: `tests/test_tracing_agent_usage.py` (create)

**Interfaces:**
- Produces: usage dicts from `_usage_from_extra` may now contain `"cached": int` (cache-read tokens). Downstream (`llm.return` event at `tracing_agent.py:314-319`, relay at `acp_agent.py:834-836`) forwards the dict as-is — no changes needed there.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tracing_agent_usage.py`:

```python
from harness.tracing_agent import _usage_from_extra


def _extra(usage):
    return {"response": {"usage": usage}}


def test_openai_shape_extracts_cached_tokens():
    out = _usage_from_extra(_extra({
        "total_tokens": 100, "prompt_tokens": 80, "completion_tokens": 20,
        "prompt_tokens_details": {"cached_tokens": 64},
    }))
    assert out == {"total": 100, "prompt": 80, "completion": 20, "cached": 64}


def test_anthropic_shape_extracts_cache_read_tokens():
    out = _usage_from_extra(_extra({
        "input_tokens": 80, "output_tokens": 20,
        "cache_read_input_tokens": 48,
    }))
    assert out["cached"] == 48


def test_no_cache_fields_omits_cached_key():
    out = _usage_from_extra(_extra({
        "total_tokens": 100, "prompt_tokens": 80, "completion_tokens": 20}))
    assert "cached" not in out


def test_non_int_cached_ignored():
    out = _usage_from_extra(_extra({
        "prompt_tokens": 80, "completion_tokens": 20,
        "prompt_tokens_details": {"cached_tokens": None},
    }))
    assert "cached" not in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_tracing_agent_usage.py -q`
Expected: FAIL (`assert 'cached' in ...` / KeyError-style mismatches) — `_usage_from_extra` drops the fields today.

- [ ] **Step 3: Implement extraction**

In `harness/tracing_agent.py`, inside `_usage_from_extra`, after the `completion` handling (line 66, before `return out`), add:

```python
    # Cache-read tokens: OpenAI shape (prompt_tokens_details.cached_tokens)
    # or Anthropic top-level (cache_read_input_tokens). #139: this is the
    # signal that proves prefix caching is working in production.
    details = usage.get("prompt_tokens_details")
    cached = None
    if isinstance(details, dict) and isinstance(details.get("cached_tokens"), int):
        cached = details["cached_tokens"]
    elif isinstance(usage.get("cache_read_input_tokens"), int):
        cached = usage["cache_read_input_tokens"]
    if cached is not None:
        out["cached"] = cached
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_tracing_agent_usage.py -q`
Expected: 4 passed.

- [ ] **Step 5: Surface hit-rate in the TUI footer**

In `harness/tui/app.py`:

(a) Near line 180, directly after `self._tokens = 0                      # last-known token count from usage updates`, add:

```python
        self._cache_pct: int | None = None    # last llm.return cache-read % (None = no signal)
```

(b) At lines 1644-1647, the current block is:

```python
        usage = (field_meta.get("harness") or {}).get("usage") if isinstance(
            ...
        if isinstance(usage, dict) and isinstance(usage.get("total"), int):
            self._tokens = usage["total"]
```

Extend the `if` body (keep existing line):

```python
        if isinstance(usage, dict) and isinstance(usage.get("total"), int):
            self._tokens = usage["total"]
            cached, prompt = usage.get("cached"), usage.get("prompt")
            self._cache_pct = (round(100 * cached / prompt)
                               if isinstance(cached, int) and isinstance(prompt, int)
                               and cached > 0 and prompt > 0 else None)
```

(c) In `_context_tagline` (lines 629-633), change the return to:

```python
    def _context_tagline(self) -> str:
        # _tokens is the latest llm.return total (prompt+completion for that call),
        # which tracks current context footprint until the next model call (or compaction).
        window = resolve_ctx_window(_model_label(self.model, self._worker_model_id))
        tag = ctx_bar(self._tokens, window)
        if self._cache_pct is not None:
            tag += f" [$muted]· cache {self._cache_pct}%[/]"
        return tag
```

- [ ] **Step 6: Run the full suite**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/ -q`
Expected: only the two known baseline failures. (`_cache_pct` defaults to None, so snapshot output is unchanged.)

- [ ] **Step 7: Commit**

```bash
git add harness/tracing_agent.py harness/tui/app.py tests/test_tracing_agent_usage.py
git commit -m "feat(cache): extract cached_tokens into usage events + footer hit-rate (#139)"
```

---

### Task 2: Split the Environment block out of the spine; reorder most-stable-first

**Files:**
- Modify: `harness/base_prompt.py:81-117` (`render_base_prompt`; new `render_env_block`)
- Modify: `harness/tracing_agent.py` (constructor + `_render_template`)
- Modify: `harness/runner.py:85-96` (`MiniSweAgentRunner.run` signature)
- Modify: `harness/chat_handler.py` (constructor + `answer_stream` system_content)
- Modify: `harness/acp_agent.py:527-544` and `_run_agent_turn` call chain (~lines 611-614, 631-633)
- Modify: `harness/run_traced.py:199-213`
- Modify: `tests/test_base_prompt.py:12-20` (env assertions move to the new function)
- Test: `tests/test_base_prompt.py` (extend)

**Interfaces:**
- Produces: `base_prompt.render_env_block(*, model_id: str, cwd: str, system_line: str, cutoff: str = KNOWLEDGE_CUTOFF) -> str` — the exact `# Environment` text previously embedded in `render_base_prompt`.
- Produces: `render_base_prompt(...)` KEEPS its signature but no longer renders the env block (the `model_id`, `cwd`, `system_line`, `cutoff` params remain accepted for compatibility but unused — remove them from the signature ONLY if every caller in this task compiles without them; they do: remove `model_id`, `cwd`, `system_line`, `cutoff` and fix the two callers listed above).
- Produces: `TracingAgent(..., env_block: str = "")`, `MiniSweAgentRunner.run(..., env_block: str = "")`, `ChatHandler(..., env_block: str = "")` — all default `""` so every untouched constructor/test keeps working.
- New assembled system order: engine template → BASE_POLICY → agents_block → skills_menu → persona-files → persona_block → memory_block → env_block.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_base_prompt.py`:

```python
def test_env_block_split_out_and_base_has_no_environment():
    out = base_prompt.render_base_prompt(
        persona_id="bob", persona_dir="/p/bob",
        skills_menu="SKILLSMENU", agents_block="AGENTSBLOCK")
    assert "# Environment" not in out
    env = base_prompt.render_env_block(
        model_id="vibeproxy", cwd="/repo/proj", system_line="macOS-15")
    assert "# Environment" in env
    assert "/repo/proj" in env and "vibeproxy" in env and "macOS-15" in env
    assert base_prompt.KNOWLEDGE_CUTOFF in env
    assert "separate processes" in env


def test_spine_order_most_stable_first():
    out = base_prompt.render_base_prompt(
        persona_id="bob", persona_dir="/p/bob",
        skills_menu="SKILLSMENU", agents_block="AGENTSBLOCK")
    i_policy = out.index("You are Done")
    i_agents = out.index("AGENTSBLOCK")
    i_skills = out.index("SKILLSMENU")
    i_persona = out.index("# Persona files")
    assert i_policy < i_agents < i_skills < i_persona
```

Also UPDATE the existing `test_render_interpolates_environment_values` (lines 12-20): it currently asserts `# Environment` appears in `render_base_prompt` output. Point its assertions at `render_env_block(model_id=..., cwd=..., system_line=...)` instead (same expected strings).

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_base_prompt.py -q`
Expected: FAIL — `render_env_block` does not exist; order assertion fails.

- [ ] **Step 3: Implement in `base_prompt.py`**

Replace `render_base_prompt` (lines 81-117) with:

```python
def render_base_prompt(*, persona_id: str | None = None,
                       persona_dir: str | None = None,
                       skills_menu: str | None = None,
                       agents_block: str | None = None) -> str:
    """Return the spine: static policy, then AGENTS.md block, then the skills
    menu, then # Persona files — ordered most-stable-first so upstream prefix
    caches survive persona/model differences as long as possible (#139). The
    volatile # Environment block is rendered separately by render_env_block()
    and appended at the system-prompt TAIL by the assembly sites. Pure — no
    I/O, no globals read."""
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
    return BASE_POLICY + (agents_block or "") + (skills_menu or "") + persona


def render_env_block(*, model_id: str, cwd: str, system_line: str,
                     cutoff: str = KNOWLEDGE_CUTOFF) -> str:
    """The runtime # Environment section — the least-stable prompt block
    (cwd/model/platform vary per session; model can swap mid-session), so the
    assembly sites append it LAST in the system prompt. Pure."""
    return (
        "\n\n# Environment\n"
        f"- Working directory: {cwd}\n"
        f"- Model: {model_id}\n"
        f"- Knowledge cutoff: {cutoff}\n"
        f"- OS: {system_line}\n"
        "- Surface: you run as Done in the user's terminal (a TUI); the agent and "
        "the UI are separate processes communicating over a pipe.\n"
    )
```

(The env text is byte-identical to the old inline version. The persona text is byte-identical. Only order and the leading `\n\n` positions change: note the old layout put env right after BASE_POLICY — the new spine has agents/skills blocks immediately after BASE_POLICY, exactly as they were previously concatenated at the end, unchanged text.)

- [ ] **Step 4: Thread `env_block` through the agent path**

(a) `harness/tracing_agent.py` constructor (line 71): add `env_block: str = ""` after `base_block: str = ""`, and store `self._env_block = env_block` next to `self._base_block = base_block` (line 94).

(b) `_render_template` (lines 102-116) system branch — append env LAST:

```python
        if template is self.config.system_template:
            if self._base_block:
                out += self._base_block
            if self._persona_block:
                out += self._persona_block
            if self._memory_block:
                out += self._memory_block
            if self._skill_block:
                out += self._skill_block
            if self._env_block:
                out += self._env_block
        return out
```

(Task 3 moves `skill_block` out of this branch; here it stays, before env.)

(c) `harness/runner.py:85-96`: add `env_block: str = ""` to `run()`'s keyword params and pass `env_block=env_block` into the `TracingAgent(...)` construction.

- [ ] **Step 5: Thread `env_block` through chat + callers**

(a) `harness/chat_handler.py`: constructor gains `env_block: str = ""` stored as `self._env_block = env_block` (next to `self._base_block`, line 175); in `answer_stream` change line 212 to:

```python
        system_content = self._base_block + self._persona_block + self._env_block
```

(b) `harness/acp_agent.py:527-533` — replace the `render_base_prompt` call with:

```python
        base_block = base_prompt.render_base_prompt(
            persona_id=(ws.name if ws else None),
            persona_dir=(str(ws.resolve()) if ws else None),
            skills_menu=_skills_menu,
            agents_block=_agents_block)
        env_block = base_prompt.render_env_block(
            model_id=(model_id or "mock"),
            cwd=state.cwd, system_line=platform.platform())
```

Pass `env_block=env_block` to the `ChatHandler(...)` construction (line 540) and through `self._run_agent_turn(...)` (line 611-614) → `_run_agent_turn` signature (line 631-633: add `env_block=""` keyword) → wherever `_run_agent_turn` calls `runner.run(...)`/builds the engine, pass `env_block` alongside `base_block` (search for `base_block=base_block` inside `_run_agent_turn`'s body and mirror it).

(c) `harness/run_traced.py:199-213` — same split:

```python
    base_block = base_prompt.render_base_prompt(
        skills_menu=skills.compose_menu(_menu_metas),
        agents_block=_agents_block)
    env_block = base_prompt.render_env_block(
        model_id=(worker_model_id or "mock"),
        cwd=args.cwd,
        system_line=platform.platform())
```

and add `env_block=env_block` to the `runner.run(...)` call at lines 210-213. Note `run_traced` passed no persona kwargs to `render_base_prompt` before — keep it that way. Also check line ~236 (`ChatHandler` built with `persona_block=persona_block + memory_block`) and pass `env_block=env_block` there too.

- [ ] **Step 6: Run the full suite; fix ONLY order-encoding assertions**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/ -q`
Expected: the two known baseline failures, plus possibly tests that assert the OLD block order or env-in-base (e.g. anything asserting `# Environment` before `# Persona files` in an assembled prompt). Fix only assertions that encode the old ORDER/placement; if a test failure is not obviously order-related, STOP and report it instead of adapting the test.

- [ ] **Step 7: Commit**

```bash
git add harness/base_prompt.py harness/tracing_agent.py harness/runner.py \
        harness/chat_handler.py harness/acp_agent.py harness/run_traced.py \
        tests/test_base_prompt.py
git commit -m "feat(cache): split env block to system tail; spine most-stable-first (#139)"
```

---

### Task 3: Move per-turn skill bodies to the instance message

**Files:**
- Modify: `harness/tracing_agent.py:102-118` (`_render_template`)
- Test: `tests/test_prompt_cache_stability.py` (create)

**Interfaces:**
- Consumes: `TracingAgent(..., env_block=...)` from Task 2.
- Produces: system message no longer contains `skill_block`; the instance (user) message opens with `## Skills loaded for this task\n<skill_block>\n\n` when a skill_block is present.

- [ ] **Step 1: Write the failing test (the load-bearing regression test)**

Create `tests/test_prompt_cache_stability.py`:

```python
"""Byte-stability of the system prompt across turns (#139 PR2).

The append-only invariant (docs/superpowers/specs/
2026-07-02-prompt-cache-prefix-stability-design.md): within a session the
system message must be byte-identical across turns unless a declared
boundary fires. Router-picked skills differ per turn, so skill bodies must
ride the instance message, never the system message.
"""
import json
from pathlib import Path

import yaml
from minisweagent.environments.local import LocalEnvironment
from minisweagent.models.test_models import DeterministicToolcallModel, make_toolcall_output

from harness.events import Emitter
from harness.tracing_agent import TracingAgent


def _agent_cfg() -> dict:
    cfg = yaml.safe_load(Path("upstream/src/minisweagent/config/mini.yaml").read_text())
    return cfg["agent"]


def _submit_model():
    tcid = "call_0_0"
    return DeterministicToolcallModel(outputs=[make_toolcall_output(
        "done",
        [{"id": tcid, "type": "function",
          "function": {"name": "bash", "arguments": json.dumps(
              {"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"})}}],
        [{"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT",
          "tool_call_id": tcid}],
    )], cost_per_call=0.0)


def _run_turn(tmp_path, skill_block: str, name: str):
    emitter = Emitter(tmp_path / f"{name}.jsonl", clock=lambda: 0.0, console=False)
    cfg = _agent_cfg()
    cfg["output_path"] = str(tmp_path / f"{name}-traj.json")
    agent = TracingAgent(
        _submit_model(), LocalEnvironment(cwd=str(tmp_path)), emitter=emitter,
        skill_block=skill_block, base_block="BASEBLOCK", persona_block="PERSONA",
        memory_block="MEMORY", env_block="ENVBLOCK", **cfg)
    agent.run("same task every turn")
    emitter.close()
    system = agent.messages[0]["content"]
    instance = agent.messages[1]["content"]
    return system, instance


def test_system_prompt_byte_stable_when_skills_differ(tmp_path):
    sys_a, inst_a = _run_turn(tmp_path, "SKILL-BODY-A", "a")
    sys_b, inst_b = _run_turn(tmp_path, "SKILL-BODY-B", "b")
    assert sys_a == sys_b                       # THE invariant
    assert "SKILL-BODY-A" not in sys_a
    assert "SKILL-BODY-A" in inst_a
    assert "## Skills loaded for this task" in inst_a
    assert "SKILL-BODY-B" in inst_b


def test_no_skill_block_leaves_instance_untouched(tmp_path):
    _, inst = _run_turn(tmp_path, "", "none")
    assert "## Skills loaded for this task" not in inst


def test_env_block_is_system_suffix(tmp_path):
    sys_a, _ = _run_turn(tmp_path, "", "envcheck")
    assert sys_a.endswith("ENVBLOCK")


def test_model_swap_changes_only_the_env_suffix(tmp_path):
    # Spec §2b: a mid-session model swap must invalidate ONLY the final env
    # block — the prefix above it stays byte-identical.
    sys_a, _ = _run_turn(tmp_path, "", "swap-a", env_block="ENV-MODEL-A")
    sys_b, _ = _run_turn(tmp_path, "", "swap-b", env_block="ENV-MODEL-B")
    assert sys_a.endswith("ENV-MODEL-A") and sys_b.endswith("ENV-MODEL-B")
    assert sys_a[:-len("ENV-MODEL-A")] == sys_b[:-len("ENV-MODEL-B")]
```

(For this test, give `_run_turn` a keyword parameter `env_block: str = "ENVBLOCK"` and pass it through to the `TracingAgent(...)` construction in place of the literal.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_prompt_cache_stability.py -q`
Expected: `test_system_prompt_byte_stable_when_skills_differ` FAILS (`sys_a != sys_b`, skill body found in system). `test_env_block_is_system_suffix` passes already (Task 2).

- [ ] **Step 3: Implement the move**

In `harness/tracing_agent.py` `_render_template`, remove the `skill_block` append from the system branch and add an instance branch. Full method after the change:

```python
    def _render_template(self, template: str) -> str:
        # Inject blocks AFTER Jinja renders the base, so a skill body containing
        # {{ }}/{% %} is literal text and cannot break StrictUndefined.
        # System gets the stable blocks (env LAST — least stable, #139).
        # Router-picked skill bodies differ per turn, so they ride the INSTANCE
        # message: putting them in the system message changes message[0] bytes
        # turn-over-turn and cold-misses the whole prompt cache.
        out = super()._render_template(template)
        if template is self.config.system_template:
            if self._base_block:
                out += self._base_block
            if self._persona_block:
                out += self._persona_block
            if self._memory_block:
                out += self._memory_block
            if self._env_block:
                out += self._env_block
        elif template is self.config.instance_template and self._skill_block:
            out = ("## Skills loaded for this task\n" + self._skill_block
                   + "\n\n" + out)
        return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_prompt_cache_stability.py -q`
Expected: 3 passed.

- [ ] **Step 5: Run the full suite**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/ -q`
Expected: only the two known baseline failures. If a test asserts skill bodies in the SYSTEM prompt (search: `grep -rn "skill_block" tests/`), update it to assert the instance placement instead — but only tests that encode placement, nothing else.

- [ ] **Step 6: Commit**

```bash
git add harness/tracing_agent.py tests/test_prompt_cache_stability.py
git commit -m "feat(cache): skill bodies ride the instance message, not the system prompt (#139)"
```

---

### Task 4: `cache.boundary` events (the silent-invalidator alarm)

**Files:**
- Create: `harness/prompt_hash.py`
- Modify: `harness/acp_session.py` (SessionState dataclass — add one field)
- Modify: `harness/acp_agent.py` (after the block assembly from Task 2, before the chat/agent branch)
- Test: `tests/test_prompt_hash.py` (create)

**Interfaces:**
- Produces: `prompt_hash.block_hashes(blocks: dict[str, str]) -> dict[str, str]` (8-hex-char sha256 per block) and `prompt_hash.changed_blocks(old: dict | None, new: dict) -> list[str]` (sorted names whose hash differs; `[]` when `old` is None).
- Produces: trace event `cache.boundary` with `data.changed` = comma-joined block names, emitted at most once per turn.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_prompt_hash.py`:

```python
from harness.prompt_hash import block_hashes, changed_blocks


def test_hashes_stable_and_distinct():
    h1 = block_hashes({"base": "A", "env": "B"})
    h2 = block_hashes({"base": "A", "env": "B"})
    assert h1 == h2
    assert h1["base"] != h1["env"]
    assert all(len(v) == 8 for v in h1.values())


def test_changed_blocks_names_only_the_diff():
    old = block_hashes({"base": "A", "env": "B", "memory": "M"})
    new = block_hashes({"base": "A", "env": "B2", "memory": "M"})
    assert changed_blocks(old, new) == ["env"]


def test_first_turn_reports_no_change():
    assert changed_blocks(None, block_hashes({"base": "A"})) == []


def test_added_and_removed_blocks_count_as_changed():
    old = block_hashes({"base": "A"})
    new = block_hashes({"base": "A", "memory": "M"})
    assert changed_blocks(old, new) == ["memory"]
    assert changed_blocks(new, old) == ["memory"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_prompt_hash.py -q`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement `harness/prompt_hash.py`**

```python
"""Per-session prompt-block hashing (#139 PR2): detect WHICH block of the
system prompt changed between turns. A change at an undeclared moment is a
silent cache invalidator — the cache.boundary trace event makes it visible
in the run trace instead of only in the token bill. Pure functions."""

from __future__ import annotations

import hashlib


def block_hashes(blocks: dict[str, str]) -> dict[str, str]:
    """8-hex-char sha256 per named block. Deterministic, content-only."""
    return {name: hashlib.sha256(text.encode()).hexdigest()[:8]
            for name, text in blocks.items()}


def changed_blocks(old: dict[str, str] | None, new: dict[str, str]) -> list[str]:
    """Sorted names whose hash differs between old and new (added/removed
    count as changed). [] when old is None (first turn: nothing to compare)."""
    if old is None:
        return []
    return sorted(name for name in (old.keys() | new.keys())
                  if old.get(name) != new.get(name))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_prompt_hash.py -q`
Expected: 4 passed.

- [ ] **Step 5: Wire into the per-turn assembly**

(a) In `harness/acp_session.py`, find the `SessionState` dataclass (it holds `persona_block`, `memory_block`, `cwd`, ...) and add one field with the neighboring fields' style:

```python
    prompt_hashes: dict | None = None   # last turn's block hashes (cache.boundary, #139)
```

(b) In `harness/acp_agent.py`, immediately after the `base_block`/`env_block` assembly from Task 2 (after line ~533) and BEFORE the `if cls.task_type == "chat_question":` branch, add:

```python
        # cache.boundary: name which prompt block changed since the last turn.
        # Declared boundaries (persona/model swap, skills/AGENTS.md edits) are
        # expected; anything else appearing here is a silent cache invalidator.
        from harness import prompt_hash as _prompt_hash
        _hashes = _prompt_hash.block_hashes({
            "base": base_block,
            "persona": state.persona_block or "",
            "memory": state.memory_block or "",
            "env": env_block,
        })
        _changed = _prompt_hash.changed_blocks(state.prompt_hashes, _hashes)
        if _changed:
            await self._trace(session_id, "cache.boundary",
                              sid=session_id, changed=",".join(_changed))
        state.prompt_hashes = _hashes
```

(Match `self._trace` call style to the existing `await self._trace(session_id, "chat.done", sid=session_id)` at line 597.)

- [ ] **Step 6: Run the full suite**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/ -q`
Expected: only the two known baseline failures.

- [ ] **Step 7: Commit**

```bash
git add harness/prompt_hash.py harness/acp_session.py harness/acp_agent.py tests/test_prompt_hash.py
git commit -m "feat(cache): cache.boundary trace event names the changed prompt block (#139)"
```

---

### Task 5: Plan/spec bookkeeping + live acceptance note

**Files:**
- Modify: this plan file (check boxes) — commit it with the branch.

- [ ] **Step 1: Commit the plan doc into the branch**

```bash
git add docs/superpowers/plans/2026-07-02-prompt-cache-prefix-stability.md
git commit -m "docs(plan): prompt-cache prefix stability implementation plan (#139)"
```

- [ ] **Step 2: Live acceptance (reviewer runs this, not the subagent)**

After merge, in a real `dn` session: two agent turns in a row; confirm the footer shows `cache NN%` on turn 2 and `trace.jsonl` has `llm.return` events with `usage.cached > 0` and NO `cache.boundary` event between the turns (same persona/model/cwd).
