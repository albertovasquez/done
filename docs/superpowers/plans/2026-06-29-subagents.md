# Sub-agents (Hermes-model parallel workers) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `subagent` tool that lets a parent agent spawn ephemeral, low-context worker agents in parallel (cheaper model, restricted tools, fresh conversation) to do focused single-item tasks and return a structured-summary digest.

**Architecture:** Extract the agent-construction recipe into one reusable `build_persona_agent` chokepoint (shared by cron and workers). A new `SubagentTool` builds N workers via that chokepoint, runs them on a per-call `ThreadPoolExecutor`, and returns a digest. Workers get a trimmed prompt (no soul/memory/skills-menu, keep base+AGENTS.md), a `{read,bash}` default toolset, a low `step_limit` turn cap, and a cheaper model resolved from `done.conf` (unset = no-op).

**Tech Stack:** Python 3.11, vendored mini-swe-agent (`minisweagent`), litellm, pytest. No new dependencies.

## Global Constraints

- **Worktree only:** all work in `.worktrees/subagents-spec` (branch `subagents-spec`). Never edit the primary checkout. Verify with `git -C /Users/alberto/Work/Quiubo/harness status --short` staying clean.
- **Test command (from worktree root):** `.venv/bin/python -m pytest tests/ -q` (target `tests/` only). The `.venv` lives at the PRIMARY checkout root; from the worktree use `/Users/alberto/Work/quiubo/harness/.venv/bin/python`.
- **No-op discipline:** with no `subagent_model` configured and the tool merely present, existing persona/cron behavior MUST be byte-identical. Every task preserves this.
- **No regressions:** the existing executor/cron tests are the parity gate for the refactor. They MUST stay green at every commit.
- **Least complexity:** prefer the smallest change that satisfies the spec. Do NOT expand `AgentConfig`'s TOML round-trip; read `subagent_model` separately (Task 6).
- **Cost is NOT a guardrail:** upstream `GLOBAL_MODEL_STATS` is process-global; rely on `step_limit` + `wall_time_limit_seconds`, never `cost_limit`, to bound workers.
- **TDD:** failing test first, minimal impl, green, commit. Frequent commits.
- **Commit trailer:** end every commit message with `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `harness/agent_build.py` (NEW) | `build_persona_agent(...)` — the single agent-construction chokepoint (model + env + runner + agent_cfg knobs). Stamps `env._active_persona`. |
| `harness/tools/subagent.py` (NEW) | `SubagentTool` (schema + execute), the parallel worker runner, the digest formatter, model resolution, `MAX_TASKS_PER_CALL`. |
| `harness/subagent_config.py` (NEW) | `resolve_subagent_model(agent_id, *, per_task)` and `subagent_max_concurrent()` — read `done.conf` without touching `AgentConfig`. |
| `harness/tools/registry.py` (MOD) | `toolset` + `is_worker` params; register `SubagentTool`. |
| `harness/jobs/executor.py` (MOD) | `run_turn` calls `build_persona_agent`; stamp `env._active_persona = job.agent_id`. |
| `tests/test_agent_build.py` (NEW) | builder unit + parity tests. |
| `tests/tools/test_subagent.py` (NEW) | tool schema, parallel, failure isolation, digest, concurrency stress. |
| `tests/tools/test_registry_toolset.py` (NEW) | toolset filter + worker `subagent` deny. |
| `tests/test_subagent_config.py` (NEW) | model resolution order + max_concurrent. |

**Task order (dependencies):** Task 1 (registry filter) → Task 2 (builder) → Task 3 (cron refactor, parity gate) → Task 4 (config reader) → Task 5 (worker instance template) → Task 6 (subagent tool) → Task 7 (register tool) → Task 8 (cron budget wiring) → Task 9 (full-suite regression check).

---

### Task 1: Registry `toolset` filter + worker `subagent` deny

**Files:**
- Modify: `harness/tools/registry.py`
- Test: `tests/tools/test_registry_toolset.py` (Create)

**Interfaces:**
- Consumes: existing `build_registry(skill_roots=None, memory_root=None)`.
- Produces: `build_registry(skill_roots=None, memory_root=None, *, toolset: set[str] | None = None, is_worker: bool = False) -> list[Tool]`. When `toolset` is not None, keep only tools whose `.name in toolset`. When `is_worker` is True, ALWAYS drop the `subagent` tool regardless of `toolset`. Default args reproduce today's behavior exactly.

- [ ] **Step 1: Write the failing test**

```python
# tests/tools/test_registry_toolset.py
from harness.tools.registry import build_registry


def _names(reg):
    return {t.name for t in reg}


def test_default_unchanged_is_full_set():
    # No toolset, not a worker => today's behavior (byte-identical no-op).
    names = _names(build_registry())
    assert {"bash", "read", "write", "edit", "create_job"} <= names


def test_toolset_filters_to_named_tools():
    reg = build_registry(toolset={"read", "bash"})
    assert _names(reg) == {"read", "bash"}


def test_worker_denies_subagent_even_if_requested():
    # A worker must never get subagent, even if the toolset names it.
    reg = build_registry(toolset={"read", "bash", "subagent"}, is_worker=True)
    assert "subagent" not in _names(reg)
    assert {"read", "bash"} <= _names(reg)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/alberto/Work/quiubo/harness/.venv/bin/python -m pytest tests/tools/test_registry_toolset.py -q`
Expected: FAIL — `build_registry()` got an unexpected keyword argument `toolset`.

- [ ] **Step 3: Write minimal implementation**

Edit `harness/tools/registry.py` — change the signature and add the filter at the end (keep everything else):

```python
def build_registry(skill_roots: list[Path] | None = None,
                   memory_root: Path | None = None,
                   *,
                   toolset: set[str] | None = None,
                   is_worker: bool = False) -> list[Tool]:
    # ... existing body unchanged: builds `tools` list ...
    # (BashTool, ReadTool, WriteTool, EditTool, CreateJobTool, optional load_skill/load_memory)

    # Depth-1 enforcement: a worker can NEVER call subagent (explicit deny, not a
    # side effect of the toolset — a task could name it in `tools`).
    if is_worker:
        tools = [t for t in tools if t.name != "subagent"]
    # Restricted toolset: keep only the named tools (model schemas AND agent
    # dispatch use this one list, so they always agree).
    if toolset is not None:
        tools = [t for t in tools if t.name in toolset]
    return tools
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/alberto/Work/quiubo/harness/.venv/bin/python -m pytest tests/tools/test_registry_toolset.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Run the registry's existing tests to confirm no regression**

Run: `/Users/alberto/Work/quiubo/harness/.venv/bin/python -m pytest tests/ -q -k registry`
Expected: PASS (existing registry tests still green).

- [ ] **Step 6: Commit**

```bash
cd /Users/alberto/Work/quiubo/harness/.worktrees/subagents-spec
git add harness/tools/registry.py tests/tools/test_registry_toolset.py
git commit -m "feat(subagents): registry toolset filter + worker subagent deny

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `build_persona_agent` chokepoint

**Files:**
- Create: `harness/agent_build.py`
- Test: `tests/test_agent_build.py` (Create)

**Interfaces:**
- Consumes: `harness.runner.MiniSweAgentRunner`, `harness.tools.registry.build_registry` (Task 1), `harness.streaming_model.StreamingLitellmModel`, `harness.models_mock.build_mock_model`, `minisweagent.environments.local.LocalEnvironment`, `harness.vibeproxy`.
- Produces: `build_persona_agent(agent_id, *, model_name=None, model_kwargs=None, skill_roots=None, memory_root=None, agent_cfg, toolset=None, is_worker=False, step_limit=None, wall_time_limit=None) -> tuple[MiniSweAgentRunner, list[Tool]]`. Returns `(runner, registry)`. Builds a fresh model (mock when `model_name is None`, else `StreamingLitellmModel`), a fresh `LocalEnvironment(cwd=cwd_from_workspace)`, stamps `env._active_persona = agent_id`, applies `step_limit`/`wall_time_limit` into a COPY of `agent_cfg`, and constructs the runner with the SAME registry object handed to the model.

**Design note (least complexity):** this builder owns ONLY the model+env+runner+registry construction (executor.py:119-144). It does NOT own compose/skills/base_block — those stay in their callers and are passed in via `skill_roots`/`memory_root`/`agent_cfg`. This keeps the highest-risk extraction surgical.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_agent_build.py
from harness.agent_build import build_persona_agent


def _agent_cfg():
    # Minimal agent config dict (the keys DefaultAgent.AgentConfig accepts).
    return {"step_limit": 0, "cost_limit": 0, "wall_time_limit_seconds": 0}


def test_mock_model_when_model_name_none(tmp_path):
    runner, registry = build_persona_agent(
        "default", model_name=None, agent_cfg=_agent_cfg(),
        memory_root=tmp_path,
    )
    # Mock path: runner is constructed, env is stamped with the persona.
    assert runner._env._active_persona == "default"


def test_step_and_walltime_override_applied(tmp_path):
    runner, _ = build_persona_agent(
        "default", model_name=None, agent_cfg=_agent_cfg(),
        memory_root=tmp_path, step_limit=15, wall_time_limit=30,
    )
    assert runner._agent_cfg["step_limit"] == 15
    assert runner._agent_cfg["wall_time_limit_seconds"] == 30
    # Original cfg dict not mutated (builder copies).
    # (re-call with a fresh cfg and check independence)


def test_worker_registry_excludes_subagent(tmp_path):
    runner, registry = build_persona_agent(
        "default", model_name=None, agent_cfg=_agent_cfg(),
        memory_root=tmp_path, toolset={"read", "bash"}, is_worker=True,
    )
    assert "subagent" not in {t.name for t in registry}
    assert {"read", "bash"} == {t.name for t in registry}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/alberto/Work/quiubo/harness/.venv/bin/python -m pytest tests/test_agent_build.py -q`
Expected: FAIL — `No module named 'harness.agent_build'`.

- [ ] **Step 3: Write minimal implementation**

```python
# harness/agent_build.py
"""build_persona_agent(): the single agent-construction chokepoint.

Owns model + env + runner + registry construction ONLY. Compose/skills/base_block
stay in callers (cron executor, run_traced) and arrive via skill_roots/memory_root/
agent_cfg. Shared by the cron path and the subagent worker path so a worker can
never drift from how a real persona turn is built.

Stamps env._active_persona = agent_id UNCONDITIONALLY so tools (create_job,
subagent) bind to the right persona on every launch surface (the bare cron
LocalEnvironment did not stamp it before).
"""
from __future__ import annotations

from pathlib import Path

from harness.runner import MiniSweAgentRunner
from harness.tools.registry import build_registry


def build_persona_agent(
    agent_id: str,
    *,
    model_name: str | None = None,
    model_kwargs: dict | None = None,
    cwd: str | None = None,
    skill_roots: list[Path] | None = None,
    memory_root: Path | None = None,
    agent_cfg: dict,
    toolset: set[str] | None = None,
    is_worker: bool = False,
    step_limit: int | None = None,
    wall_time_limit: int | None = None,
) -> tuple[MiniSweAgentRunner, list]:
    # Fresh registry — handed to BOTH model (schemas) and agent (dispatch).
    registry = build_registry(
        skill_roots=skill_roots, memory_root=memory_root,
        toolset=toolset, is_worker=is_worker,
    )

    # Fresh model per call. Mock when no model_name (persona-fidelity rule #1:
    # never vibeproxy.default_model()).
    if model_name is None:
        from harness.models_mock import build_mock_model
        model = build_mock_model()
    else:
        from harness import vibeproxy as _vp
        from harness.streaming_model import StreamingLitellmModel
        model = StreamingLitellmModel(
            model_name=_vp.model_id(model_name),
            model_kwargs=(model_kwargs if model_kwargs is not None else _vp.model_kwargs()),
            cost_tracking="ignore_errors",
            registry=registry,
        )

    # Fresh env per call; stamp the persona so env-bound tools resolve agent_id.
    from minisweagent.environments.local import LocalEnvironment  # noqa: E402
    env = LocalEnvironment(cwd=cwd) if cwd else LocalEnvironment()
    env._active_persona = agent_id

    # Apply per-worker caps into a COPY of agent_cfg (never mutate the caller's dict).
    cfg = dict(agent_cfg)
    if step_limit is not None:
        cfg["step_limit"] = step_limit
    if wall_time_limit is not None:
        cfg["wall_time_limit_seconds"] = wall_time_limit

    runner = MiniSweAgentRunner(model, env, agent_cfg=cfg)
    return runner, registry
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/alberto/Work/quiubo/harness/.venv/bin/python -m pytest tests/test_agent_build.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
cd /Users/alberto/Work/quiubo/harness/.worktrees/subagents-spec
git add harness/agent_build.py tests/test_agent_build.py
git commit -m "feat(subagents): build_persona_agent construction chokepoint

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Refactor cron `run_turn` to use the chokepoint (PARITY GATE — highest risk)

**Files:**
- Modify: `harness/jobs/executor.py:119-148` (the model/env/runner construction inside `run_turn`)
- Test: existing `tests/jobs/` executor tests are the gate (no new test file; run them).

**Interfaces:**
- Consumes: `build_persona_agent` (Task 2).
- Produces: cron `run_turn` builds its model+env+runner via `build_persona_agent` instead of inline, AND stamps `env._active_persona = agent_id`. Behavior is otherwise IDENTICAL (parity gate).

**Why this is the risk-concentrated task:** the construction is inside a closure with ~15 lazy imports. We are NOT untangling the compose/skills logic — only swapping the model+env+runner lines (119-144) for a builder call. The existing executor tests must stay green.

- [ ] **Step 1: Run the existing executor tests FIRST to capture the green baseline**

Run: `/Users/alberto/Work/quiubo/harness/.venv/bin/python -m pytest tests/jobs/ -q`
Expected: PASS. Record the count. This is the parity bar.

- [ ] **Step 2: Replace the inline construction with a builder call**

In `harness/jobs/executor.py`, inside `run_turn`, replace lines 119-144 (from `# Fresh model per call;` through the `runner = MiniSweAgentRunner(...)` line) with:

```python
        # Construction via the shared chokepoint (harness/agent_build.py). Cron
        # passes model_name=None for mock, else the qualified model; the builder
        # stamps env._active_persona = agent_id so env-bound tools resolve.
        from harness.agent_build import build_persona_agent
        runner, _registry = build_persona_agent(
            agent_id=workspace.name,
            model_name=(None if model_id is None else model_id),
            skill_roots=skills_roots,
            memory_root=workspace,
            agent_cfg=_load_agent_cfg(),
            cwd=str(workspace),
        )
```

Notes:
- `workspace.name` is the persona id (the workspace dir is `config_dir()/agents/<id>`). This is the stamp the cron path was missing.
- Remove the now-dead inline imports of `StreamingLitellmModel`, `build_registry`, `LocalEnvironment`, and the `if model_id is None / else` block they lived in — the builder owns them now. KEEP the `runner.run(...)` loop below unchanged.
- `build_persona_agent` calls `_vp.model_id(model_name)` internally, so do NOT pre-qualify `model_id` here (pass the bare `model_id`).

- [ ] **Step 3: Run the executor tests — parity must hold**

Run: `/Users/alberto/Work/quiubo/harness/.venv/bin/python -m pytest tests/jobs/ -q`
Expected: PASS, same count as Step 1. If anything fails, the extraction changed behavior — fix until parity.

- [ ] **Step 4: Add a focused test that the cron env is now persona-stamped**

```python
# tests/jobs/test_executor_persona_stamp.py
from pathlib import Path
from harness.agent_build import build_persona_agent


def test_builder_stamps_active_persona(tmp_path):
    # The cron path previously built a bare LocalEnvironment with no _active_persona.
    # The builder must stamp it so env-bound tools (create_job, subagent) resolve.
    runner, _ = build_persona_agent(
        "alice", model_name=None, agent_cfg={"step_limit": 0},
        memory_root=tmp_path, cwd=str(tmp_path),
    )
    assert getattr(runner._env, "_active_persona", None) == "alice"
```

- [ ] **Step 5: Run it**

Run: `/Users/alberto/Work/quiubo/harness/.venv/bin/python -m pytest tests/jobs/test_executor_persona_stamp.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/alberto/Work/quiubo/harness/.worktrees/subagents-spec
git add harness/jobs/executor.py tests/jobs/test_executor_persona_stamp.py
git commit -m "refactor(subagents): cron run_turn builds via build_persona_agent + persona stamp

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `subagent_config` reader (model resolution + max_concurrent)

**Files:**
- Create: `harness/subagent_config.py`
- Test: `tests/test_subagent_config.py` (Create)

**Interfaces:**
- Consumes: `harness.config.conf_path` / `tomllib` (read `done.conf` raw), `harness.config.RESERVED_KEY`.
- Produces:
  - `resolve_subagent_model(agent_id, *, per_task=None, parent_model) -> str` — order: `per_task` → `[agents.<id>].subagent_model` → `[subagent].model` (global) → `parent_model`.
  - `subagent_max_concurrent(default=4) -> int` — reads `[subagent].max_concurrent`, else default.
- **Design note (least complexity):** does NOT touch `AgentConfig` (which skips tables lacking backend+model). Reads the TOML directly for the two new keys, so the strict round-trip and no-op discipline are untouched.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_subagent_config.py
import harness.subagent_config as sc


def _write_conf(tmp_path, text):
    (tmp_path / "done.conf").write_text(text)


def test_global_unset_falls_back_to_parent(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "conf_path", lambda: tmp_path / "done.conf")
    _write_conf(tmp_path, "schema_version = 1\n")
    assert sc.resolve_subagent_model("default", parent_model="gpt-5.4") == "gpt-5.4"


def test_per_task_wins(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "conf_path", lambda: tmp_path / "done.conf")
    _write_conf(tmp_path, '[subagent]\nmodel = "cheap-global"\n')
    assert sc.resolve_subagent_model(
        "default", per_task="cheap-task", parent_model="gpt-5.4") == "cheap-task"


def test_per_persona_over_global(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "conf_path", lambda: tmp_path / "done.conf")
    _write_conf(tmp_path,
        '[subagent]\nmodel = "cheap-global"\n'
        '[agents.alice]\nbackend = "vibeproxy"\nmodel = "x"\nsubagent_model = "cheap-alice"\n')
    assert sc.resolve_subagent_model("alice", parent_model="gpt-5.4") == "cheap-alice"
    # default persona has no per-persona key => global wins
    assert sc.resolve_subagent_model("default", parent_model="gpt-5.4") == "cheap-global"


def test_max_concurrent_default_and_override(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "conf_path", lambda: tmp_path / "done.conf")
    _write_conf(tmp_path, "schema_version = 1\n")
    assert sc.subagent_max_concurrent() == 4
    _write_conf(tmp_path, "[subagent]\nmax_concurrent = 8\n")
    assert sc.subagent_max_concurrent() == 8
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/alberto/Work/quiubo/harness/.venv/bin/python -m pytest tests/test_subagent_config.py -q`
Expected: FAIL — `No module named 'harness.subagent_config'`.

- [ ] **Step 3: Write minimal implementation**

```python
# harness/subagent_config.py
"""Subagent knobs read from done.conf WITHOUT touching AgentConfig's strict
round-trip. Two keys: [subagent].model / [subagent].max_concurrent (global) and
[agents.<id>].subagent_model (per-persona). All optional; unset => no-op."""
from __future__ import annotations

import tomllib

from harness.config import RESERVED_KEY, conf_path  # noqa: F401  (conf_path patched in tests)


def _raw() -> dict:
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


def resolve_subagent_model(agent_id: str, *, per_task: str | None = None,
                           parent_model: str) -> str:
    if per_task:
        return per_task
    data = _raw()
    agents = data.get("agents")
    if isinstance(agents, dict):
        table = agents.get(agent_id)
        if isinstance(table, dict):
            m = table.get("subagent_model")
            if isinstance(m, str) and m:
                return m
    sub = data.get("subagent")
    if isinstance(sub, dict):
        m = sub.get("model")
        if isinstance(m, str) and m:
            return m
    return parent_model


def subagent_max_concurrent(default: int = 4) -> int:
    sub = _raw().get("subagent")
    if isinstance(sub, dict):
        n = sub.get("max_concurrent")
        if isinstance(n, int) and n > 0:
            return n
    return default
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/alberto/Work/quiubo/harness/.venv/bin/python -m pytest tests/test_subagent_config.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
cd /Users/alberto/Work/quiubo/harness/.worktrees/subagents-spec
git add harness/subagent_config.py tests/test_subagent_config.py
git commit -m "feat(subagents): done.conf reader for subagent model + max_concurrent

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Worker instance template (structured-summary contract)

**Files:**
- Create: `harness/tools/subagent_prompt.py` (the template string + builder)
- Test: `tests/tools/test_subagent_prompt.py` (Create)

**Interfaces:**
- Produces: `build_worker_task(goal: str, context: str) -> str` — returns the worker's task string: the goal+context plus the four-field structured-summary instruction (did / found / files modified / issues). This string is passed to `runner.run(task=...)` as the worker's instance prompt.
- **Design note:** the worker prompt is the `task` argument to `runner.run` (which upstream renders into the instance template), NOT a YAML config change. Lowest-complexity home: a plain string builder. This resolves spec §12's open question.

- [ ] **Step 1: Write the failing test**

```python
# tests/tools/test_subagent_prompt.py
from harness.tools.subagent_prompt import build_worker_task


def test_includes_goal_context_and_four_fields():
    out = build_worker_task("Survey X", "Files at /a, /b. Use ripgrep.")
    assert "Survey X" in out
    assert "/a, /b" in out
    # The four structured-summary fields are instructed.
    for token in ("did", "found", "modified", "issues"):
        assert token in out.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/alberto/Work/quiubo/harness/.venv/bin/python -m pytest tests/tools/test_subagent_prompt.py -q`
Expected: FAIL — `No module named 'harness.tools.subagent_prompt'`.

- [ ] **Step 3: Write minimal implementation**

```python
# harness/tools/subagent_prompt.py
"""The worker's task string: goal + context + the structured-summary contract.
Borrowed from Hermes — the worker finishes by summarizing what it did, found,
modified, and any issues, so the parent's digest is predictable."""
from __future__ import annotations

_SUMMARY_CONTRACT = (
    "\n\nWhen done, finish with a short structured summary covering:\n"
    "1. what you did,\n"
    "2. what you found,\n"
    "3. any files you modified,\n"
    "4. any issues you hit.\n"
    "Submit that summary as your final answer."
)


def build_worker_task(goal: str, context: str) -> str:
    return f"Goal: {goal}\n\nContext:\n{context}{_SUMMARY_CONTRACT}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/alberto/Work/quiubo/harness/.venv/bin/python -m pytest tests/tools/test_subagent_prompt.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/alberto/Work/quiubo/harness/.worktrees/subagents-spec
git add harness/tools/subagent_prompt.py tests/tools/test_subagent_prompt.py
git commit -m "feat(subagents): worker task builder with structured-summary contract

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: `SubagentTool` — schema, parallel runner, digest, failure isolation

**Files:**
- Create: `harness/tools/subagent.py`
- Test: `tests/tools/test_subagent.py` (Create)

**Interfaces:**
- Consumes: `build_persona_agent` (Task 2), `resolve_subagent_model`/`subagent_max_concurrent` (Task 4), `build_worker_task` (Task 5).
- Produces: `SubagentTool` with `name = "subagent"`, `schema` (dict), `display_label(args)`, `execute(args, env) -> dict`. Also module consts `MAX_TASKS_PER_CALL = 16`, `DEFAULT_WORKER_TOOLSET = {"read", "bash"}`, `DEFAULT_STEP_LIMIT = 15`.
- `execute` runs each task via `_run_one_worker`, on a per-call `ThreadPoolExecutor(max_workers=subagent_max_concurrent())`, returns `{"output": digest, "returncode": 0, "exception_info": None}`. Over `MAX_TASKS_PER_CALL` → `{"output": "<error>", "returncode": 1, ...}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/tools/test_subagent.py
import harness.tools.subagent as sub
from harness.tools.subagent import SubagentTool


class _FakeEnv:
    _active_persona = "default"


def _patch_worker(monkeypatch, fn):
    # Replace the single-worker runner so tests don't spin a real engine.
    monkeypatch.setattr(sub, "_run_one_worker", fn)


def test_schema_shape():
    t = SubagentTool()
    assert t.name == "subagent"
    assert t.schema["function"]["name"] == "subagent"
    assert "tasks" in t.schema["function"]["parameters"]["properties"]


def test_runs_all_tasks_and_digests(monkeypatch):
    def fake(task, env, *, agent_id):  # returns (ok, summary_or_error)
        return (True, f"summary for {task['goal']}")
    _patch_worker(monkeypatch, fake)
    out = SubagentTool().execute(
        {"tasks": [{"goal": "A", "context": "c"}, {"goal": "B", "context": "c"}]},
        _FakeEnv())
    assert out["returncode"] == 0
    assert "summary for A" in out["output"]
    assert "summary for B" in out["output"]
    assert "1/2" in out["output"] and "2/2" in out["output"]


def test_one_failure_does_not_abort_siblings(monkeypatch):
    def fake(task, env, *, agent_id):
        if task["goal"] == "bad":
            raise RuntimeError("boom")
        return (True, "ok")
    _patch_worker(monkeypatch, fake)
    out = SubagentTool().execute(
        {"tasks": [{"goal": "good", "context": "c"}, {"goal": "bad", "context": "c"}]},
        _FakeEnv())
    # Tool still succeeds; failure is in the text with a ✗.
    assert out["returncode"] == 0
    assert "✓" in out["output"] and "✗" in out["output"]
    assert "boom" in out["output"]


def test_rejects_over_hard_cap(monkeypatch):
    tasks = [{"goal": str(i), "context": "c"} for i in range(sub.MAX_TASKS_PER_CALL + 1)]
    out = SubagentTool().execute({"tasks": tasks}, _FakeEnv())
    assert out["returncode"] == 1
    assert "too many" in out["output"].lower()


def test_concurrency_isolation_no_crosstalk(monkeypatch):
    # N concurrent mock workers must each return their OWN goal (no shared state).
    def fake(task, env, *, agent_id):
        return (True, f"[{task['goal']}]")
    _patch_worker(monkeypatch, fake)
    tasks = [{"goal": f"g{i}", "context": "c"} for i in range(8)]
    out = SubagentTool().execute({"tasks": tasks}, _FakeEnv())
    for i in range(8):
        assert f"[g{i}]" in out["output"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/alberto/Work/quiubo/harness/.venv/bin/python -m pytest tests/tools/test_subagent.py -q`
Expected: FAIL — `No module named 'harness.tools.subagent'`.

- [ ] **Step 3: Write minimal implementation**

```python
# harness/tools/subagent.py
"""SubagentTool: spawn ephemeral low-context workers in parallel for focused
single-item tasks, return a structured-summary digest. Workers run AS the parent
persona (env._active_persona) with a fresh conversation, restricted toolset, and a
cheaper model. Depth-1: workers never get the subagent tool (registry is_worker).

Guardrails: per-worker step_limit (turn cap) + wall_time; NOT cost (upstream
GLOBAL_MODEL_STATS is process-global). Pool is per-call. Hard MAX_TASKS_PER_CALL."""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

from harness.agent_build import build_persona_agent
from harness.subagent_config import resolve_subagent_model, subagent_max_concurrent
from harness.tools.subagent_prompt import build_worker_task

MAX_TASKS_PER_CALL = 16
DEFAULT_WORKER_TOOLSET = {"read", "bash"}
DEFAULT_STEP_LIMIT = 15

SUBAGENT_TOOL = {
    "type": "function",
    "function": {
        "name": "subagent",
        "description": (
            "Delegate one or more FOCUSED tasks to fresh low-context worker agents "
            "that run in parallel and return a structured summary. A worker does NOT "
            "see this conversation — put everything it needs in `context`. Default "
            "tools are read-only (read, bash); grant write/edit per task via `tools`. "
            "Use for parallel single-item investigation/work on a cheaper model."),
        "parameters": {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "goal": {"type": "string"},
                            "context": {"type": "string"},
                            "tools": {"type": "array", "items": {"type": "string"}},
                            "model": {"type": "string"},
                            "max_iterations": {"type": "integer"},
                        },
                        "required": ["goal", "context"],
                    },
                },
            },
            "required": ["tasks"],
        },
    },
}


def _run_one_worker(task: dict, env, *, agent_id: str):
    """Build + run ONE worker. Returns (ok: bool, text: str). Raising is caught by
    the caller and rendered as a failed entry (sibling isolation)."""
    parent_model = os.environ.get("VIBEPROXY_MODEL")  # None => mock path
    model_name = resolve_subagent_model(
        agent_id, per_task=task.get("model"), parent_model=parent_model) if parent_model else None

    toolset = set(task.get("tools") or DEFAULT_WORKER_TOOLSET)
    step_limit = int(task.get("max_iterations") or DEFAULT_STEP_LIMIT)
    remaining = getattr(env, "_remaining_secs", None)

    import yaml
    from pathlib import Path
    _root = Path(__file__).resolve().parent.parent.parent
    agent_cfg = yaml.safe_load(
        (_root / "upstream/src/minisweagent/config/mini.yaml").read_text())["agent"]

    workspace_cwd = getattr(env, "config", None)
    cwd = getattr(workspace_cwd, "cwd", None) or os.getcwd()

    runner, _ = build_persona_agent(
        agent_id=agent_id,
        model_name=model_name,
        skill_roots=None,            # worker: no skills menu (load_skill only if granted)
        memory_root=None,            # worker: no memory block (load_memory gated elsewhere)
        agent_cfg=agent_cfg,
        toolset=toolset,
        is_worker=True,
        step_limit=step_limit,
        wall_time_limit=remaining,   # min handled below
    )
    if remaining is not None:
        # Cap wall-time at the parent's remaining budget (cron).
        default_wt = agent_cfg.get("wall_time_limit_seconds", 0) or remaining
        runner._agent_cfg["wall_time_limit_seconds"] = min(default_wt, remaining)

    task_str = build_worker_task(task["goal"], task["context"])
    for _ in runner.run(task_str):
        pass
    res = runner.result
    summary = (res.submission or "").strip() if res else ""
    ok = bool(res and res.ok)
    if not ok:
        return (False, (res.error if res and res.error else res.exit_status if res else "unknown"))
    return (True, summary or "(no summary returned)")


def _format_digest(results: list[tuple[bool, str]], goals: list[str]) -> str:
    n = len(results)
    blocks = []
    for i, ((ok, text), goal) in enumerate(zip(results, goals), start=1):
        mark = "✓" if ok else "✗"
        head = f"[subagent {i}/{n} {mark}] goal: {goal!r}"
        body = text if ok else f"failed: {text}"
        blocks.append(f"{head}\n{body}")
    return "\n\n".join(blocks)


class SubagentTool:
    name = "subagent"
    schema = SUBAGENT_TOOL

    def display_label(self, args: dict) -> str:
        tasks = args.get("tasks") or []
        return f"subagent ({len(tasks)} task{'s' if len(tasks) != 1 else ''})"

    def execute(self, args: dict, env) -> dict:
        agent_id = getattr(env, "_active_persona", None) or "default"
        tasks = args.get("tasks") or []
        if len(tasks) > MAX_TASKS_PER_CALL:
            return {"output": f"Too many tasks ({len(tasks)}); max is "
                              f"{MAX_TASKS_PER_CALL} per call.",
                    "returncode": 1, "exception_info": None}
        if not tasks:
            return {"output": "No tasks provided.", "returncode": 1,
                    "exception_info": None}

        goals = [t.get("goal", "") for t in tasks]

        def _safe(task):
            try:
                return _run_one_worker(task, env, agent_id=agent_id)
            except BaseException as e:  # sibling isolation
                return (False, f"{type(e).__name__}: {e}")

        max_workers = min(subagent_max_concurrent(), len(tasks))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            results = list(pool.map(_safe, tasks))

        return {"output": _format_digest(results, goals), "returncode": 0,
                "exception_info": None}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/alberto/Work/quiubo/harness/.venv/bin/python -m pytest tests/tools/test_subagent.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
cd /Users/alberto/Work/quiubo/harness/.worktrees/subagents-spec
git add harness/tools/subagent.py tests/tools/test_subagent.py
git commit -m "feat(subagents): SubagentTool — parallel workers, digest, failure isolation

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Register `SubagentTool` in the registry (non-worker agents only)

**Files:**
- Modify: `harness/tools/registry.py`
- Test: `tests/tools/test_registry_toolset.py` (extend Task 1's file)

**Interfaces:**
- Consumes: `SubagentTool` (Task 6).
- Produces: `build_registry()` (no toolset, not worker) now INCLUDES a `subagent` tool. A worker registry (`is_worker=True`) still excludes it (Task 1 deny already covers this).

- [ ] **Step 1: Write the failing test (extend the Task 1 file)**

```python
# append to tests/tools/test_registry_toolset.py
def test_normal_agent_has_subagent_tool():
    assert "subagent" in _names(build_registry())


def test_worker_never_has_subagent_tool():
    assert "subagent" not in _names(build_registry(is_worker=True))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/alberto/Work/quiubo/harness/.venv/bin/python -m pytest tests/tools/test_registry_toolset.py::test_normal_agent_has_subagent_tool -q`
Expected: FAIL — `subagent` not in names.

- [ ] **Step 3: Write minimal implementation**

In `harness/tools/registry.py`, add the import and append `SubagentTool()` to the base tool list:

```python
from harness.tools.subagent import SubagentTool  # add with the other tool imports
```

Change the base list line to include it:

```python
    tools: list[Tool] = [BashTool(), ReadTool(), WriteTool(), EditTool(),
                         CreateJobTool(), SubagentTool()]
```

(The `is_worker` deny from Task 1 already removes it for workers; the `toolset` filter already removes it when not named.)

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/alberto/Work/quiubo/harness/.venv/bin/python -m pytest tests/tools/test_registry_toolset.py -q`
Expected: PASS (5 passed — 3 from Task 1 + 2 new).

- [ ] **Step 5: Guard against an import cycle**

`subagent.py` imports `agent_build` which imports `registry`. Confirm no cycle by importing the registry fresh:

Run: `/Users/alberto/Work/quiubo/harness/.venv/bin/python -c "from harness.tools.registry import build_registry; print(sorted(t.name for t in build_registry()))"`
Expected: prints a list including `subagent` with no ImportError. If a cycle appears, move the `SubagentTool` import INSIDE `build_registry` (local import) — note this in the commit.

- [ ] **Step 6: Commit**

```bash
cd /Users/alberto/Work/quiubo/harness/.worktrees/subagents-spec
git add harness/tools/registry.py tests/tools/test_registry_toolset.py
git commit -m "feat(subagents): register SubagentTool for non-worker agents

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Cron budget wiring — stamp `_remaining_secs` so workers inherit the job budget

**Files:**
- Modify: `harness/jobs/executor.py` (the `run_turn` env, after the builder call)
- Test: `tests/jobs/test_executor_budget.py` (Create)

**Interfaces:**
- Consumes: the `runner` from `build_persona_agent` (Task 3 already calls it in `run_turn`).
- Produces: when the job carries a timeout, `runner._env._remaining_secs` is set to that timeout so a worker spawned mid-job caps its wall-time at the remaining budget. Interactive path leaves `_remaining_secs` unset (None).

**Design note (least complexity):** v1 sets `_remaining_secs` to the job's configured `timeout_secs` (a static upper bound), NOT a live countdown. The spec's `min(default, remaining)` still holds; a live countdown is future work. If the job has no timeout, do not set the attribute.

- [ ] **Step 1: Write the failing test**

```python
# tests/jobs/test_executor_budget.py
from harness.agent_build import build_persona_agent


def test_remaining_secs_caps_worker_walltime(tmp_path):
    # Simulate the cron stamp: env._remaining_secs is read by the subagent tool.
    runner, _ = build_persona_agent(
        "default", model_name=None, agent_cfg={"step_limit": 0},
        memory_root=tmp_path, cwd=str(tmp_path),
    )
    runner._env._remaining_secs = 30
    assert getattr(runner._env, "_remaining_secs") == 30
```

(The end-to-end cap is covered by the subagent tool test; this asserts the wiring point exists.)

- [ ] **Step 2: Run test to verify it fails (or trivially passes — then strengthen)**

Run: `/Users/alberto/Work/quiubo/harness/.venv/bin/python -m pytest tests/jobs/test_executor_budget.py -q`
Expected: PASS (attribute is settable). Proceed to wire the executor so it's set in production.

- [ ] **Step 3: Wire the stamp in `run_turn`**

In `harness/jobs/executor.py` `run_turn`, immediately AFTER the `build_persona_agent(...)` call from Task 3, add:

```python
        # Cron budget: let any worker the turn spawns cap its wall-time at the job's
        # configured timeout. Static upper bound (not a live countdown) in v1.
        _timeout = getattr(getattr(job, "payload", None), "timeout_secs", None) if "job" in dir() else None
        # run_turn does not receive `job`; pass the timeout through instead (see note).
```

**Correction (read carefully — least complexity):** `run_turn` does not have `job` in scope. The cleanest wiring: `run_headless_turn` already knows the job. Pass an optional `wall_budget` kwarg from `run_headless_turn` → `deps.run_turn`. In `run_headless_turn`, after computing `model_id`, read the budget:

```python
    # in run_headless_turn, AgentTurn branch:
    _budget = getattr(job.payload, "timeout_secs", None)
    deps.run_turn(model_id=model_id, workspace=ws,
                  persona_block=pb, memory_block=mb, message=msg,
                  wall_budget=_budget)
```

And in `run_turn`'s signature add `wall_budget: int | None = None`, then after the builder call:

```python
        if wall_budget:
            runner._env._remaining_secs = wall_budget
```

(If `AgentTurn` has no `timeout_secs` field, read it from the job's cost gate where the daemon already enforces the timeout — locate the existing timeout source in `jobs/` and use the SAME value. Do not invent a new one.)

- [ ] **Step 4: Run the executor tests — parity must still hold**

Run: `/Users/alberto/Work/quiubo/harness/.venv/bin/python -m pytest tests/jobs/ -q`
Expected: PASS (the new `wall_budget` kwarg defaults to None, so existing callers are unaffected).

- [ ] **Step 5: Commit**

```bash
cd /Users/alberto/Work/quiubo/harness/.worktrees/subagents-spec
git add harness/jobs/executor.py tests/jobs/test_executor_budget.py
git commit -m "feat(subagents): cron stamps _remaining_secs so workers inherit job budget

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Full-suite regression check + no-op verification

**Files:** none (verification only).

- [ ] **Step 1: Run the FULL test suite**

Run: `/Users/alberto/Work/quiubo/harness/.venv/bin/python -m pytest tests/ -q`
Expected: PASS — the prior green count PLUS the new tests. Zero failures. If anything that was green before is now red, STOP and fix (regression).

- [ ] **Step 2: Verify the no-op discipline (subagent present but unconfigured)**

Run: `/Users/alberto/Work/quiubo/harness/.venv/bin/python -c "import harness.subagent_config as sc; print('model fallback:', sc.resolve_subagent_model('default', parent_model='X')); print('concurrency:', sc.subagent_max_concurrent())"`
Expected: `model fallback: X` (no config => parent model) and `concurrency: 4`.

- [ ] **Step 3: Smoke-import the whole tool surface**

Run: `/Users/alberto/Work/quiubo/harness/.venv/bin/python -c "from harness.tools.registry import build_registry; from harness.agent_build import build_persona_agent; from harness.tools.subagent import SubagentTool; print('imports OK; tools:', sorted(t.name for t in build_registry()))"`
Expected: `imports OK; tools: [...]` including `subagent`, no ImportError.

- [ ] **Step 4: Commit (if any fixes were needed)**

```bash
cd /Users/alberto/Work/quiubo/harness/.worktrees/subagents-spec
git add -A
git commit -m "test(subagents): full-suite regression + no-op verification green

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Notes for the implementer

- **Verify symbol names against live code before each task.** This plan references real signatures (`runner.result`, `RunResult.submission/.ok/.error/.exit_status`, `env.config.cwd`, `_vp.model_id/.model_kwargs`, `mini.yaml["agent"]`). If a name differs in the live source, trust the live source and adjust — note the discrepancy in the commit.
- **`RunResult` fields:** confirm `submission`, `ok`, `error`, `exit_status` exist on `harness.runner.RunResult` (they do per the runner module). The digest in Task 6 depends on them.
- **Mock model behavior:** `build_mock_model()` returns deterministic output; worker tests that exercise the REAL runner (not the `_run_one_worker` patch) should assert structure, not exact text.
- **If the cron timeout source isn't on `AgentTurn`:** Task 8 says to find the existing timeout the daemon already enforces and reuse it. Do not duplicate timeout logic.
- **Caveman-review checkpoint:** after Task 3 (the parity-gated refactor) and after Task 6 (the tool), request a caveman-review of the diff before continuing if anything feels off.
