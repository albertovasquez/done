# /goal Stop-Gate + Role-Model Config Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `/goal <thing>` — a durable, enforced goal directive that re-prompts the agent until an LLM reviewer says the goal is met — on top of a role-model config layer (orchestrator/worker/reviewer models + fallbacks in `done.conf`).

**Architecture:** Layer A adds a role-model resolution ladder (`resolve_role_candidates`, pure) + a config-writer fix so nested `[agents.<id>.roles]` tables survive writes; `resolve_subagent_model` becomes a `worker`-role wrapper. Layer B adds a session goal that crosses the TUI↔agent process boundary via `set_goal`/`clear_goal` ACP ext-methods, a pure `GoalGate.decide` policy, a one-shot no-tools reviewer client, and an engine hook at `tracing_agent.py`'s line-209 exit-check (guarded on `exit_status=="Submitted"`) that re-prompts in place.

**Tech Stack:** Python 3.11+, frozen dataclasses, tomllib, litellm (lazy-imported), pytest.

## Global Constraints

- Python floor `>=3.11`. Test cmd from worktree root: `.venv/bin/python -m pytest tests/ -q`.
- Frozen dataclasses in model/config leaves — never mutate; use `dataclasses.replace`.
- `resolve_role_candidates` is PURE (no I/O); `load_role_tables()` does the TOML read.
- Reviewer is a one-shot `litellm.completion` (no tools, no sentinel) — NEVER `TracingAgent.query()`.
- Engine gate hooks the line-209 exit-check guarded on `exit_status=="Submitted"`; cancel/`LimitsExceeded`/`TimeExceeded`/`RepeatedFormatError` are hard-terminal and pass through.
- No goal armed → byte-identical no-op. `resolve_subagent_model` refactor → existing subagent tests stay green unchanged.
- TUI↔agent are separate processes: goal crosses via `harness/set_goal`/`harness/clear_goal` ext-methods (mirror `harness/create_job`).
- Store isolation in tests: `monkeypatch.setattr("harness.paths.config_dir", lambda: tmp_path)` (autouse fixture).

---

## LAYER A — role-model config with fallbacks

### Task 1: `load_role_tables` + pure `resolve_role_candidates`

**Files:**
- Create: `harness/role_model.py`
- Test: `tests/test_role_model.py` (create)

**Interfaces:**
- Produces:
  - `load_role_tables() -> dict` — tolerant TOML read of `done.conf` (mirrors `subagent_config._raw`); `{}` on any failure.
  - `resolve_role_candidates(agent_id: str, role: str, parsed: dict, parent_model: str) -> list[str]` — PURE. Ladder: persona.primary, persona.fallbacks, default.primary, default.fallbacks, [worker-only legacy `[agents.<id>].subagent_model` then `[subagent].model`], parent_model. Filter malformed via isinstance; order-preserving dedup; always `list[str]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_role_model.py`:

```python
from harness.role_model import resolve_role_candidates


def _cfg(d):  # convenience: the parsed done.conf dict
    return d


def test_persona_primary_then_fallbacks_then_parent():
    parsed = {"agents": {"bob": {
        "roles": {"reviewer": "R1"},
        "roles.fallback": {"reviewer": ["R2"]},
    }}}
    # NOTE tomllib nests [agents.bob.roles.fallback] as roles -> fallback; see Task 2 shape.
    parsed = {"agents": {"bob": {
        "roles": {"reviewer": "R1", "fallback": {"reviewer": ["R2"]}},
    }}}
    got = resolve_role_candidates("bob", "reviewer", parsed, parent_model="P")
    assert got == ["R1", "R2", "P"]


def test_default_role_seeds_when_persona_absent():
    parsed = {"agents": {"default": {"roles": {"reviewer": "DR"}}}}
    got = resolve_role_candidates("alice", "reviewer", parsed, parent_model="P")
    assert got == ["DR", "P"]


def test_persona_over_default_over_parent_order():
    parsed = {"agents": {
        "alice": {"roles": {"worker": "AW", "fallback": {"worker": ["AF"]}}},
        "default": {"roles": {"worker": "DW", "fallback": {"worker": ["DF"]}}},
    }}
    got = resolve_role_candidates("alice", "worker", parsed, parent_model="P")
    assert got == ["AW", "AF", "DW", "DF", "P"]


def test_worker_includes_legacy_subagent_rungs():
    parsed = {"agents": {"alice": {"subagent_model": "LEGACY"}},
              "subagent": {"model": "GLOBAL"}}
    got = resolve_role_candidates("alice", "worker", parsed, parent_model="P")
    assert got == ["LEGACY", "GLOBAL", "P"]


def test_non_worker_role_ignores_legacy_rungs():
    parsed = {"agents": {"alice": {"subagent_model": "LEGACY"}},
              "subagent": {"model": "GLOBAL"}}
    got = resolve_role_candidates("alice", "reviewer", parsed, parent_model="P")
    assert got == ["P"]


def test_malformed_tables_are_skipped_not_raised():
    parsed = {"agents": {"alice": {
        "roles": {"worker": "", "fallback": {"worker": "notalist"}},  # empty primary, str fallback
    }}}
    got = resolve_role_candidates("alice", "worker", parsed, parent_model="P")
    assert got == ["P"]  # both malformed values skipped


def test_dedup_order_preserving():
    parsed = {"agents": {"alice": {"roles": {"worker": "P", "fallback": {"worker": ["P", "X"]}}}}}
    got = resolve_role_candidates("alice", "worker", parsed, parent_model="P")
    assert got == ["P", "X"]  # duplicate P collapsed, order kept


def test_empty_config_is_just_parent():
    assert resolve_role_candidates("nobody", "worker", {}, parent_model="P") == ["P"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_role_model.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'harness.role_model'`.

- [ ] **Step 3: Write minimal implementation**

Create `harness/role_model.py`:

```python
"""Role -> model resolution with ordered fallbacks (done.conf [agents.<id>.roles]).

Split from I/O so the ladder is pure and unit-testable: load_role_tables() reads
TOML; resolve_role_candidates() is a pure function over the parsed dict. Always
returns a list[str] (never a bare str). resolve_subagent_model wraps this with
role='worker' (see subagent_config.py) so legacy subagent config stays byte-identical."""
from __future__ import annotations

import tomllib

from harness.config import conf_path


def load_role_tables() -> dict:
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


def _str(v) -> str | None:
    return v if isinstance(v, str) and v else None


def _str_list(v) -> list[str]:
    if not isinstance(v, list):
        return []
    return [x for x in v if isinstance(x, str) and x]


def _roles_of(parsed: dict, agent_id: str) -> dict:
    agents = parsed.get("agents")
    if not isinstance(agents, dict):
        return {}
    table = agents.get(agent_id)
    if not isinstance(table, dict):
        return {}
    roles = table.get("roles")
    return roles if isinstance(roles, dict) else {}


def _primary_and_fallbacks(roles: dict, role: str) -> list[str]:
    out: list[str] = []
    p = _str(roles.get(role))
    if p:
        out.append(p)
    fb = roles.get("fallback")
    if isinstance(fb, dict):
        out.extend(_str_list(fb.get(role)))
    return out


def _legacy_worker_rungs(parsed: dict, agent_id: str) -> list[str]:
    out: list[str] = []
    agents = parsed.get("agents")
    if isinstance(agents, dict):
        table = agents.get(agent_id)
        if isinstance(table, dict):
            m = _str(table.get("subagent_model"))
            if m:
                out.append(m)
    sub = parsed.get("subagent")
    if isinstance(sub, dict):
        m = _str(sub.get("model"))
        if m:
            out.append(m)
    return out


def resolve_role_candidates(agent_id: str, role: str, parsed: dict,
                            parent_model: str) -> list[str]:
    cands: list[str] = []
    cands += _primary_and_fallbacks(_roles_of(parsed, agent_id), role)
    cands += _primary_and_fallbacks(_roles_of(parsed, "default"), role)
    if role == "worker":
        cands += _legacy_worker_rungs(parsed, agent_id)
    cands.append(parent_model)
    # order-preserving dedup
    seen: set[str] = set()
    out: list[str] = []
    for m in cands:
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_role_model.py -q`
Expected: PASS (8 passed).

- [ ] **Step 5: Commit**

```bash
git add harness/role_model.py tests/test_role_model.py
git commit -m "feat(config): pure role-model candidate ladder with fallbacks

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `resolve_subagent_model` becomes a `worker`-role wrapper

**Files:**
- Modify: `harness/subagent_config.py`
- Test: `tests/test_role_model.py` (add wrapper-compat cases); existing `tests/**` subagent tests must stay green.

**Interfaces:**
- Consumes: `resolve_role_candidates`, `load_role_tables` (Task 1).
- Produces: `resolve_subagent_model(agent_id, *, per_task=None, parent_model) -> str` — unchanged signature, now returns `resolve_role_candidates(agent_id, "worker", load_role_tables(), parent_model)[0]` (after the `per_task` short-circuit).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_role_model.py`:

```python
import pytest
from harness import subagent_config


@pytest.fixture(autouse=True)
def _cron_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("harness.paths.config_dir", lambda: tmp_path)


def _write_conf(tmp_path, text):
    (tmp_path / "done.conf").write_text(text)


def test_subagent_wrapper_prefers_per_task(tmp_path):
    assert subagent_config.resolve_subagent_model(
        "a", per_task="PT", parent_model="P") == "PT"


def test_subagent_wrapper_reads_legacy_persona_key(tmp_path):
    _write_conf(tmp_path, 'schema_version = 1\n[agents.alice]\nsubagent_model = "LEG"\n')
    assert subagent_config.resolve_subagent_model(
        "alice", parent_model="P") == "LEG"


def test_subagent_wrapper_reads_global_subagent_model(tmp_path):
    _write_conf(tmp_path, 'schema_version = 1\n[subagent]\nmodel = "GLOB"\n')
    assert subagent_config.resolve_subagent_model(
        "alice", parent_model="P") == "GLOB"


def test_subagent_wrapper_falls_to_parent(tmp_path):
    _write_conf(tmp_path, 'schema_version = 1\n')
    assert subagent_config.resolve_subagent_model(
        "alice", parent_model="P") == "P"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_role_model.py -q -k subagent_wrapper`
Expected: PASS for per_task/legacy/global/parent IF the old impl still happens to match — but this is a **refactor-under-test**. First confirm the OLD tests pass (baseline), then refactor. If any wrapper test fails against the current impl, that's a real behavior gap to reconcile before changing code.

- [ ] **Step 3: Refactor `resolve_subagent_model`**

In `harness/subagent_config.py`, replace the body of `resolve_subagent_model` (keep `_raw` and `subagent_max_concurrent` untouched — `_raw` may still be used elsewhere):

```python
def resolve_subagent_model(agent_id: str, *, per_task: str | None = None,
                           parent_model: str) -> str:
    if per_task:
        return per_task
    from harness.role_model import load_role_tables, resolve_role_candidates
    return resolve_role_candidates(
        agent_id, "worker", load_role_tables(), parent_model)[0]
```

- [ ] **Step 4: Run the wrapper tests AND every existing subagent test**

Run: `.venv/bin/python -m pytest tests/test_role_model.py tests/tools/test_subagent.py tests/ -q -k "subagent or role_model"`
Expected: PASS. The existing subagent suite must be GREEN unchanged (byte-identical behavior).

- [ ] **Step 5: Commit**

```bash
git add harness/subagent_config.py tests/test_role_model.py
git commit -m "refactor(config): resolve_subagent_model wraps the worker-role ladder

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: config writer round-trips nested `[agents.<id>.roles]` (Codex blocker #1)

**Files:**
- Modify: `harness/config.py` (`_serialize`)
- Test: `tests/test_config.py` (add nested-preservation cases; if the file doesn't exist, create `tests/test_config_roles_preserve.py`)

**Interfaces:**
- Consumes: `_serialize(agents, *, preserve=, partial=)` (existing).
- Produces: `_serialize` re-emits, per agent, any nested sub-tables present in `preserve["agents"][key]` that are NOT flat scalars it already owns (i.e. dict-valued keys like `roles`). Every writer (`save_agent`/`update_agent`/`set_harness_setting`/`set_compress_aware`) therefore preserves a pre-existing `[agents.<id>.roles]` table.

- [ ] **Step 1: Write the failing test**

Create `tests/test_config_roles_preserve.py`:

```python
import tomllib
import pytest
from harness import config


@pytest.fixture(autouse=True)
def _cfgdir(tmp_path, monkeypatch):
    monkeypatch.setattr("harness.paths.config_dir", lambda: tmp_path)


_SEED = '''schema_version = 1

[agents.bob]
backend = "vibeproxy"
model = "parent"

[agents.bob.roles]
worker = "w1"
reviewer = "r1"

[agents.bob.roles.fallback]
worker = ["w2"]
'''


def _seed(tmp_path):
    (tmp_path / "done.conf").write_text(_SEED)


def _reload(tmp_path):
    return tomllib.loads((tmp_path / "done.conf").read_text())


def test_update_agent_preserves_roles(tmp_path):
    _seed(tmp_path)
    config.update_agent("bob", model="new-parent")   # a normal flat write
    doc = _reload(tmp_path)
    roles = doc["agents"]["bob"]["roles"]
    assert roles["worker"] == "w1"
    assert roles["reviewer"] == "r1"
    assert roles["fallback"]["worker"] == ["w2"]
    assert doc["agents"]["bob"]["model"] == "new-parent"


def test_set_harness_setting_preserves_roles(tmp_path):
    _seed(tmp_path)
    config.set_harness_setting("theme", "dark")
    doc = _reload(tmp_path)
    assert doc["agents"]["bob"]["roles"]["worker"] == "w1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_config_roles_preserve.py -q`
Expected: FAIL — `KeyError: 'roles'` (the writer drops the nested table today).

- [ ] **Step 3: Implement nested preservation in `_serialize`**

In `harness/config.py`, inside `_serialize`, after emitting each agent's flat
scalars (right before the trailing `lines.append("")` that closes the agent
table in the main `for key in ordered:` loop), re-emit preserved nested
sub-tables for that agent. Add a helper and call it:

```python
def _emit_nested_agent_tables(lines: list, agent_key: str, preserve: dict | None) -> None:
    """Re-emit dict-valued (nested) keys under [agents.<key>] from the prior raw
    config — e.g. `roles` and `roles.fallback` — which _serialize's flat schema
    does not own. Flat scalars are already emitted by the caller."""
    if not preserve:
        return
    agents_raw = preserve.get("agents")
    if not isinstance(agents_raw, dict):
        return
    table = agents_raw.get(agent_key)
    if not isinstance(table, dict):
        return
    for k, v in table.items():
        if not isinstance(v, dict):
            continue  # flat scalars already emitted by the caller
        # top-level nested table, e.g. [agents.<key>.roles]
        lines.append(f"[agents.{agent_key}.{k}]")
        for kk, vv in v.items():
            if isinstance(vv, dict):
                continue  # deeper nesting emitted below
            _emit_scalar(lines, kk, vv)
        lines.append("")
        # one more level, e.g. [agents.<key>.roles.fallback]
        for kk, vv in v.items():
            if isinstance(vv, dict):
                lines.append(f"[agents.{agent_key}.{k}.{kk}]")
                for k3, v3 in vv.items():
                    _emit_scalar(lines, k3, v3)
                lines.append("")


def _emit_scalar(lines: list, key: str, value) -> None:
    if isinstance(value, bool):
        lines.append(f"{key} = {'true' if value else 'false'}")
    elif isinstance(value, list):
        inner = ", ".join(_quote(str(x)) for x in value)
        lines.append(f"{key} = [{inner}]")
    else:
        lines.append(f"{key} = {_quote(str(value))}")
```

Then in the `for key in ordered:` loop, replace the agent-closing
`lines.append("")` with:

```python
        _emit_nested_agent_tables(lines, key, preserve)
        lines.append("")
```

(Do NOT change the `_OWNED`/top-level preserve block — `[harness]` etc. still
round-trip via the existing path; this only adds per-agent nested preservation.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_config_roles_preserve.py -q`
Expected: PASS (2 passed).

Then run the FULL existing config suite to confirm flat writes are unaffected:
Run: `.venv/bin/python -m pytest tests/ -q -k config`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/config.py tests/test_config_roles_preserve.py
git commit -m "fix(config): round-trip nested [agents.<id>.roles] tables on write

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## LAYER B — the /goal stop-gate

### Task 4: `GoalGate.decide` — pure policy

**Files:**
- Create: `harness/goal_gate.py`
- Test: `tests/test_goal_gate.py` (create)

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) Verdict(met: bool, reason: str = "")`
  - `@dataclass(frozen=True) GateLimits(max_attempts: int)`
  - `@dataclass(frozen=True) GateDecision(action: str, reason: str = "")` — action in `{"stop","continue","escape"}`.
  - `decide(*, goal: str | None, verdict: Verdict | None, reviewer_attempts: int, limits: GateLimits) -> GateDecision`

- [ ] **Step 1: Write the failing test**

Create `tests/test_goal_gate.py`:

```python
from harness.goal_gate import decide, Verdict, GateLimits


L = GateLimits(max_attempts=3)


def test_no_goal_stops():
    d = decide(goal=None, verdict=None, reviewer_attempts=0, limits=L)
    assert d.action == "stop"


def test_met_stops():
    d = decide(goal="g", verdict=Verdict(met=True), reviewer_attempts=1, limits=L)
    assert d.action == "stop"


def test_unmet_continues_with_reason():
    d = decide(goal="g", verdict=Verdict(met=False, reason="tests red"),
               reviewer_attempts=1, limits=L)
    assert d.action == "continue"
    assert "tests red" in d.reason


def test_reviewer_failure_escapes():
    d = decide(goal="g", verdict=None, reviewer_attempts=1, limits=L)
    assert d.action == "escape"


def test_budget_exhausted_escapes():
    d = decide(goal="g", verdict=Verdict(met=False), reviewer_attempts=3, limits=L)
    assert d.action == "escape"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_goal_gate.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'harness.goal_gate'`.

- [ ] **Step 3: Write minimal implementation**

Create `harness/goal_gate.py`:

```python
"""Pure stop/continue/escape policy for the /goal stop-gate. No LLM, no I/O — the
engine computes the reviewer Verdict and passes it here. reviewer_attempts is the
gate's OWN budget (separate from the worker's n_calls, per spec §4.1)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Verdict:
    met: bool
    reason: str = ""


@dataclass(frozen=True)
class GateLimits:
    max_attempts: int


@dataclass(frozen=True)
class GateDecision:
    action: str          # "stop" | "continue" | "escape"
    reason: str = ""


def decide(*, goal: str | None, verdict: "Verdict | None",
           reviewer_attempts: int, limits: GateLimits) -> GateDecision:
    if not goal:
        return GateDecision("stop")
    if reviewer_attempts >= limits.max_attempts:
        return GateDecision("escape", f"goal not met after {reviewer_attempts} attempts")
    if verdict is None:
        return GateDecision("escape", "reviewer unavailable")
    if verdict.met:
        return GateDecision("stop")
    return GateDecision("continue", verdict.reason)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_goal_gate.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add harness/goal_gate.py tests/test_goal_gate.py
git commit -m "feat(goal): pure GoalGate.decide stop/continue/escape policy

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: reviewer client — one-shot, no tools

**Files:**
- Create: `harness/goal_reviewer.py`
- Test: `tests/test_goal_reviewer.py` (create)

**Interfaces:**
- Consumes: nothing from earlier tasks (uses litellm/vibeproxy like `review.py`).
- Produces:
  - `review_goal(goal: str, transcript_text: str, model: str, *, caller=None) -> Verdict` — builds a yes/no prompt, calls a one-shot completion (injectable `caller: (prompt)->str` for tests), parses `met`. Unparseable → `Verdict(met=False, reason=<raw>)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_goal_reviewer.py`:

```python
from harness.goal_reviewer import review_goal
from harness.goal_gate import Verdict


def test_parses_met_yes():
    v = review_goal("g", "did the work", "m", caller=lambda p: "met: yes\nlooks done")
    assert isinstance(v, Verdict) and v.met is True


def test_parses_met_no_with_reason():
    v = review_goal("g", "t", "m", caller=lambda p: "met: no\ntests still red")
    assert v.met is False
    assert "tests still red" in v.reason


def test_unparseable_defaults_to_not_met():
    v = review_goal("g", "t", "m", caller=lambda p: "banana")
    assert v.met is False


def test_prompt_contains_goal_and_transcript():
    seen = {}
    def cap(p): seen["p"] = p; return "met: yes"
    review_goal("SHIP IT", "the transcript body", "m", caller=cap)
    assert "SHIP IT" in seen["p"] and "the transcript body" in seen["p"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_goal_reviewer.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

Create `harness/goal_reviewer.py`:

```python
"""One-shot goal reviewer: a no-tools LiteLLM completion that judges whether a
goal is met. NEVER TracingAgent.query()/StreamingLitellmModel (which advertise
tools and raise FormatError on prose). Mirrors harness/tools/review.py's direct
litellm pattern; the completion caller is injectable for tests."""
from __future__ import annotations

from harness.goal_gate import Verdict

_PROMPT = """You are reviewing whether a stated GOAL has been achieved, given the
work transcript below. Answer on the FIRST line exactly `met: yes` or `met: no`,
then one short line explaining why.

GOAL:
{goal}

TRANSCRIPT (most recent work):
{transcript}
"""


def _default_caller(model: str):
    import litellm  # noqa: PLC0415
    from harness import vibeproxy  # noqa: PLC0415
    mid = vibeproxy.model_id(model)
    kwargs = vibeproxy.completion_kwargs()

    def call(prompt: str) -> str:
        resp = litellm.completion(
            model=mid, messages=[{"role": "user", "content": prompt}], **kwargs)
        return resp.choices[0].message.content or ""
    return call


def review_goal(goal: str, transcript_text: str, model: str, *, caller=None) -> Verdict:
    caller = caller or _default_caller(model)
    prompt = _PROMPT.format(goal=goal, transcript=transcript_text)
    raw = (caller(prompt) or "").strip()
    first = raw.splitlines()[0].lower() if raw else ""
    rest = "\n".join(raw.splitlines()[1:]).strip() or raw
    if first.startswith("met: yes") or first.startswith("met:yes"):
        return Verdict(met=True, reason=rest)
    if first.startswith("met: no") or first.startswith("met:no"):
        return Verdict(met=False, reason=rest)
    return Verdict(met=False, reason=raw)   # unparseable → keep working (bounded by retry cap)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_goal_reviewer.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add harness/goal_reviewer.py tests/test_goal_reviewer.py
git commit -m "feat(goal): one-shot no-tools goal reviewer client

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: `GoalContext` on `SessionState` + `set_goal`/`clear_goal` ext-methods

**Files:**
- Modify: `harness/acp_session.py` (add `goal` field)
- Modify: `harness/acp_agent.py` (`ext_method` switch: `harness/set_goal`, `harness/clear_goal`)
- Test: `tests/test_goal_extmethod.py` (create)

**Interfaces:**
- Consumes: `SessionState` (acp_session.py), the `ext_method` switch (acp_agent.py:119+).
- Produces:
  - `@dataclass GoalContext(text: str, reviewer_model: str, max_attempts: int = 3, attempts: int = 0)` (in `harness/goal_gate.py` — co-located with the policy).
  - `SessionState.goal: GoalContext | None = None`.
  - ext-methods: `harness/set_goal` (params `{text, reviewer_model?}`) sets `state.goal`; `harness/clear_goal` sets it None. Both return `{"ok": True}`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_goal_extmethod.py`:

```python
import inspect
from harness import acp_agent


def test_set_and_clear_goal_registered():
    src = inspect.getsource(acp_agent.HarnessAgent.ext_method)
    assert '"harness/set_goal"' in src
    assert '"harness/clear_goal"' in src


def test_goal_context_dataclass_shape():
    from harness.goal_gate import GoalContext
    g = GoalContext(text="do X", reviewer_model="m")
    assert g.text == "do X" and g.reviewer_model == "m"
    assert g.max_attempts == 3 and g.attempts == 0


def test_session_state_has_goal_field():
    from harness.acp_session import SessionState
    import dataclasses
    fields = {f.name for f in dataclasses.fields(SessionState)}
    assert "goal" in fields
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_goal_extmethod.py -q`
Expected: FAIL — `GoalContext` missing / `goal` not a field / ext-methods absent.

- [ ] **Step 3: Implement**

In `harness/goal_gate.py`, add:

```python
@dataclass
class GoalContext:
    text: str
    reviewer_model: str
    max_attempts: int = 3
    attempts: int = 0
```

In `harness/acp_session.py`, add to `SessionState` (after the existing fields):

```python
    goal: "object | None" = None    # GoalContext | None (avoid import cycle; duck-typed)
```

In `harness/acp_agent.py` `ext_method`, add two branches (alongside the existing
`harness/...` handlers — match their exact return/lookup style; `state` is the
per-session state object the other handlers mutate):

```python
        if method == "harness/set_goal":
            from harness.goal_gate import GoalContext
            state = self._store.get(params["session_id"])   # SessionStore.get(session_id)
            reviewer = params.get("reviewer_model") or self._worker_model_id
            state.goal = GoalContext(text=params["text"], reviewer_model=reviewer)
            return {"ok": True}
        if method == "harness/clear_goal":
            state = self._store.get(params["session_id"])
            state.goal = None
            return {"ok": True}
```

NOTE for implementer: the state-mutation idiom is `self._store.get(session_id)`
as used by `harness/set_model` (acp_agent.py:135) — NOT the stateless
`harness/create_job` door (which calls `handle_create_job` and touches no
session state). Confirm the exact params key the TUI sends for the session id
(the other handlers read it from `params`); `self._worker_model_id` (line 83) is
the reviewer-model fallback. The shapes above are the contract.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_goal_extmethod.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add harness/goal_gate.py harness/acp_session.py harness/acp_agent.py tests/test_goal_extmethod.py
git commit -m "feat(goal): SessionState.goal + set_goal/clear_goal ext-methods

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: engine hook at the line-209 exit-check

**Files:**
- Modify: `harness/tracing_agent.py` (the run loop's exit-check, ≈209-210)
- Modify: `harness/acp_agent.py` (pass goal ctx + reviewer into `TracingAgent` construction)
- Test: `tests/test_goal_gate_engine.py` (create)

**Interfaces:**
- Consumes: `decide` (Task 4), `review_goal` (Task 5), `GoalContext` (Task 6), `resolve_role_candidates` (Task 1).
- Produces: `TracingAgent` accepts an optional `goal_ctx=None`. At the exit-check, when `exit_status=="Submitted"` and `goal_ctx` is set, it runs the reviewer, calls `decide`, and either keeps the exit (stop/escape) or replaces the last message with a continue user-message (continue). Non-Submitted exits and no-goal are byte-identical no-ops.

**Design note:** to keep the loop testable, factor the gate action into a pure-ish
helper the loop calls: `_apply_goal_gate(self) -> bool` returning True if the turn
should still exit (stop/escape) or False if it should continue (a user message was
appended). The reviewer call + attempt counter live here; `decide` stays pure.

- [ ] **Step 1: Write the failing test**

Create `tests/test_goal_gate_engine.py`:

```python
import types
from harness.goal_gate import GoalContext


def _agent_with_goal(monkeypatch, verdict_seq):
    """Build a bare TracingAgent-like harness around _apply_goal_gate, injecting
    a fake reviewer. We test the gate helper in isolation from the LLM loop."""
    from harness import tracing_agent as ta
    calls = {"n": 0}

    def fake_review(goal, transcript, model, **kw):
        v = verdict_seq[min(calls["n"], len(verdict_seq) - 1)]
        calls["n"] += 1
        return v
    monkeypatch.setattr(ta, "review_goal", fake_review)
    agent = ta.TracingAgent.__new__(ta.TracingAgent)   # bypass __init__
    agent.goal_ctx = GoalContext(text="G", reviewer_model="m", max_attempts=2)
    agent.messages = [{"role": "exit",
                       "extra": {"exit_status": "Submitted", "submission": "done?"}}]
    agent._transcript_text = lambda: "work so far"
    return agent


def test_unmet_replaces_exit_with_continue(monkeypatch):
    from harness.goal_gate import Verdict
    agent = _agent_with_goal(monkeypatch, [Verdict(met=False, reason="not yet")])
    still_exit = agent._apply_goal_gate()
    assert still_exit is False
    assert agent.messages[-1]["role"] == "user"
    assert "not yet" in agent.messages[-1]["content"]


def test_met_keeps_exit_and_clears_goal(monkeypatch):
    from harness.goal_gate import Verdict
    agent = _agent_with_goal(monkeypatch, [Verdict(met=True)])
    still_exit = agent._apply_goal_gate()
    assert still_exit is True
    assert agent.messages[-1]["role"] == "exit"
    assert agent.goal_ctx is None            # cleared on stop


def test_budget_exhaustion_escapes(monkeypatch):
    from harness.goal_gate import Verdict
    agent = _agent_with_goal(monkeypatch, [Verdict(met=False)])
    agent.goal_ctx = GoalContext(text="G", reviewer_model="m", max_attempts=1)
    agent.goal_ctx.attempts = 1              # already at cap
    still_exit = agent._apply_goal_gate()
    assert still_exit is True                # escape ends the turn
    assert agent.messages[-1]["role"] == "exit"


def test_non_submitted_exit_is_ignored(monkeypatch):
    from harness.goal_gate import Verdict
    agent = _agent_with_goal(monkeypatch, [Verdict(met=False)])
    agent.messages = [{"role": "exit",
                       "extra": {"exit_status": "cancelled", "submission": ""}}]
    still_exit = agent._apply_goal_gate()
    assert still_exit is True                # cancel passes through, no reviewer run


def test_no_goal_is_noop(monkeypatch):
    from harness.goal_gate import Verdict
    agent = _agent_with_goal(monkeypatch, [Verdict(met=False)])
    agent.goal_ctx = None
    still_exit = agent._apply_goal_gate()
    assert still_exit is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_goal_gate_engine.py -q`
Expected: FAIL — `AttributeError: _apply_goal_gate`.

- [ ] **Step 3: Implement `_apply_goal_gate` + wire it into the loop**

In `harness/tracing_agent.py`, import at top: `from harness.goal_gate import decide, GateLimits`
and `from harness.goal_reviewer import review_goal`.

Add the helper method on `TracingAgent`:

```python
    def _apply_goal_gate(self) -> bool:
        """At the exit-check: if a goal is armed and the exit is a genuine
        'Submitted', run the reviewer and decide. Returns True if the turn should
        still exit (stop/escape), False if it should continue (a user message was
        appended in place of the exit). Non-Submitted exits and no-goal → True
        (byte-identical no-op)."""
        ctx = getattr(self, "goal_ctx", None)
        if ctx is None:
            return True
        last = self.messages[-1]
        if last.get("extra", {}).get("exit_status") != "Submitted":
            return True
        ctx.attempts += 1
        verdict = None
        try:
            verdict = review_goal(ctx.text, self._transcript_text(), ctx.reviewer_model)
        except Exception:  # noqa: BLE001 — a broken reviewer must not crash the turn
            verdict = None
        d = decide(goal=ctx.text, verdict=verdict,
                   reviewer_attempts=ctx.attempts,
                   limits=GateLimits(max_attempts=ctx.max_attempts))
        if d.action == "continue":
            self.messages[-1] = self.model.format_message(
                role="user",
                content=f"Keep working toward the goal: {ctx.text}. Not yet met: {d.reason}")
            return False
        if d.action == "escape":
            self.add_messages({"role": "user",
                               "content": f"(goal gate: {d.reason})"})
            # leave the ORIGINAL exit as the last message by re-appending it:
            self.add_messages(last)
            self.goal_ctx = None
            return True
        # stop
        self.goal_ctx = None
        return True

    def _transcript_text(self) -> str:
        """Compact recent transcript for the reviewer prompt (last ~20 messages)."""
        parts = []
        for m in self.messages[-20:]:
            c = m.get("content")
            if isinstance(c, str) and c:
                parts.append(f"[{m.get('role','?')}] {c}")
        return "\n".join(parts)
```

Wire it into the loop — change the exit-check at line 209-210 from:

```python
                if self.messages[-1].get("role") == "exit":
                    break
```

to:

```python
                if self.messages[-1].get("role") == "exit":
                    if self._apply_goal_gate():
                        break
                    # else: gate replaced the exit with a continue-message; loop again
```

In `harness/acp_agent.py`, where `TracingAgent(...)` is constructed (the site that
passes `cancel_flag`, ≈line 719), pass the session's goal context:

```python
            goal_ctx=state.goal,   # None when no /goal armed → gate is a no-op
```

And add `goal_ctx=None` to `TracingAgent.__init__`'s signature, storing
`self.goal_ctx = goal_ctx`. The CLI path (`run_traced.py`) passes nothing → default
None → no-op.

NOTE: `format_message` is the model's message-builder (used elsewhere in
tracing_agent, e.g. the instance_template add at ~178). Confirm its signature
(`role=, content=`) against live code before use.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_goal_gate_engine.py -q`
Expected: PASS (5 passed).

Then the full engine suite for no-regression:
Run: `.venv/bin/python -m pytest tests/ -q -k "tracing or engine or agent"`
Expected: PASS (existing tracing tests unaffected — no goal_ctx → no-op).

- [ ] **Step 5: Commit**

```bash
git add harness/tracing_agent.py harness/acp_agent.py tests/test_goal_gate_engine.py
git commit -m "feat(goal): engine gate at the exit-check re-prompts until goal met

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: `/goal` slash command + clear-on-cancel

**Files:**
- Modify: `harness/tui/commands.py` (add `/goal` command)
- Modify: `harness/acp_agent.py` (cancel path clears `state.goal` — Codex B5)
- Test: `tests/test_tui_commands.py` (add `/goal` cases); `tests/test_goal_extmethod.py` (cancel-clears case)

**Interfaces:**
- Consumes: `app._seed_prompt` (#225), the `harness/set_goal`/`harness/clear_goal` ext-methods (Task 6).
- Produces: a `Command("goal", ...)` whose handler: `/goal <text>` → send `harness/set_goal` + seed; bare `/goal` → show active goal (or hint); `/goal clear` → send `harness/clear_goal`. Cancel handler sets `state.goal=None`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_tui_commands.py`:

```python
def test_registry_has_goal():
    from harness.tui.commands import build_registry
    assert "goal" in {c.name for c in build_registry()}


def test_goal_with_arg_arms_and_seeds():
    import asyncio
    from harness.tui.commands import build_registry

    class _App:
        def __init__(self): self.calls = []
        async def _seed_prompt(self, t): self.calls.append(("seed", t))
        async def _ext(self, method, **p): self.calls.append((method, p))
        def _notify_line(self, m): self.calls.append(("notify", m))
        def _active_goal(self): return None
    reg = {c.name: c for c in build_registry()}
    app = _App()
    asyncio.run(reg["goal"].handler(app, "get tests green"))
    methods = [c[0] for c in app.calls]
    assert "harness/set_goal" in methods
    assert any(c[0] == "seed" for c in app.calls)


def test_goal_clear_disarms():
    import asyncio
    from harness.tui.commands import build_registry

    class _App:
        def __init__(self): self.calls = []
        async def _ext(self, method, **p): self.calls.append(method)
        def _notify_line(self, m): pass
    reg = {c.name: c for c in build_registry()}
    app = _App()
    asyncio.run(reg["goal"].handler(app, "clear"))
    assert "harness/clear_goal" in app.calls
```

NOTE: the handler calls an app method to send an ext-method. Read
`harness/tui/app.py` for the REAL method that sends a `harness/*` ext call
(grep `ext_method`/`send`/`_call_ext`); name the fake accordingly and use that
exact name in the handler. The `_App` fake above uses `_ext` as a placeholder —
match it to the live method.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tui_commands.py -q -k goal`
Expected: FAIL — no `goal` command.

- [ ] **Step 3: Implement the command + cancel-clear**

In `harness/tui/commands.py`, add the handler + registry entry (place near
`/loop` from #225):

```python
async def _goal(app, arg: str = "") -> None:
    sub = arg.strip()
    if sub == "":
        active = app._active_goal()
        app._notify_line(f"active goal: {active}" if active
                         else "no goal set. /goal <what to accomplish>")
        return
    if sub.lower() == "clear":
        await app._send_ext("harness/clear_goal")
        app._notify_line("goal cleared.")
        return
    await app._send_ext("harness/set_goal", text=sub)
    app._notify_line(f"goal armed: {sub}")
    await app._seed_prompt(sub)
```

Register: `Command("goal", "Set a goal the agent must reach before it can stop", _goal)`.

In `harness/acp_agent.py`, in the cancel handler (where `state.cancel_flag.set()`
is called, ≈line 322), also clear the goal:

```python
            state.goal = None    # Codex B5: cancel disarms the goal (no next-turn hijack)
```

NOTE: implementer must reconcile the handler's app-method names (`_send_ext`,
`_active_goal`) with the live `harness/tui/app.py`. Add thin app methods if they
don't exist: `_send_ext(method, **params)` wrapping the existing ext-call plumbing
(grep how `/models` sends `harness/set_model`), and `_active_goal()` reading back
the armed goal (may require a `harness/get_goal` ext-method OR tracking it app-side
at set time — prefer app-side tracking to avoid a round-trip). Keep it minimal.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tui_commands.py -q -k goal`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/tui/commands.py harness/acp_agent.py tests/test_tui_commands.py
git commit -m "feat(goal): /goal slash command + clear-on-cancel

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: full-suite green + no-regression sweep

**Files:** none (verification task).

- [ ] **Step 1: Run the full suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all green EXCEPT the known pre-existing proxy-dependent failures
(`test_acp_smoke.py` x4, `test_tui_capabilities.py` x1 — confirm the count is
unchanged from `main`). ANY other failure is a regression to fix in its owning
task.

- [ ] **Step 2: Confirm the no-goal no-op invariant**

Run: `.venv/bin/python -m pytest tests/ -q -k "tracing or subagent or config"`
Expected: PASS. These prove the engine hook, the subagent wrapper, and the config
writer are behavior-preserving when no goal/roles are configured.

- [ ] **Step 3: Commit (if any fixes were needed; else skip)**

```bash
git add -A && git commit -m "test(goal): full-suite green, no-regression sweep

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Notes for the implementer

- **Live-idiom reconciliation:** Tasks 6, 7, 8 each have a NOTE flagging an idiom
  to match against live code (the ext-method state lookup, `format_message`'s
  signature, the app's ext-send method). Read the cited live line BEFORE writing
  — the code blocks are the contract; the idiom must match the real symbols.
- **No upstream edits.** Everything is in `harness/` + `tests/`. Do not touch
  `upstream/`.
- **Layer A ships independently of Layer B.** Tasks 1-3 are a coherent, shippable
  slice (role-model config) even if Layer B is deferred. If splitting into two
  PRs, cut after Task 3.
- **The gate's no-op invariant is load-bearing** — every no-goal path must be
  byte-identical to today. Task 9 step 2 guards it.
