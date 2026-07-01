# Design: `/goal` — durable, enforced goal directive + role-model config

**Status:** approved (brainstorming) — ready for implementation plan
**Date:** 2026-07-01
**Author:** Claude (with Alberto)
**Tracks:** #226 (this work) · feeds #175 Missions (Layer C = Phase 2, gated — NOT built here)
**Reviewed:** Codex adversarial design pass (6 defects, all folded in — §7)

## 1. Summary

`/goal <thing>` is the friendly, session-triggered front door to a goal Done
*cannot wriggle out of* — the port of Claude Code's `/goal` stop-gate, made more
powerful because Done resolves the orchestrator/worker/reviewer roles to
configured models. This spec builds the two **ungated, additive** layers:

- **Layer A — role-model config with fallbacks** (`done.conf`): orchestrator /
  worker / reviewer each resolve to a configured model + ordered fallbacks, so a
  goal (or later, a mission) never needs the UI to pick them.
- **Layer B — the `/goal` stop-gate engine**: an in-loop intercept that, when the
  agent tries to end its turn, runs an LLM reviewer self-check and re-prompts the
  agent until the goal is met (bounded, with an escape hatch).

**Explicitly out of scope → #175 Missions Phase 2 (gated):** cron-scheduled
advancement, milestone decomposition, `validate:` assertions, checkpoint
write-back, mission review UI. Layer C reuses A + B with zero rework when #175's
validation gate clears.

## 2. Why this shape

The engine has one clean turn-termination seam. In `tracing_agent.py`, the run
loop (≈180–211) breaks when the last message's role is `"exit"`. A submit
sentinel raises `Submitted` (an `InterruptAgentFlow`) in `execute_actions`, which
is caught at the `except InterruptAgentFlow` handler (≈line 202) that appends the
exit message. **That handler is the single intercept point.** Re-prompting is
therefore *in-place loop continuation* — append a user message instead of the
exit message and the existing loop steps again, feeding the full transcript back
to the model. No fresh `prompt()`, no new session, no thread. (Verified by an
Explore trace of the ACP + CLI paths.)

Role-model resolution already has a seed: `resolve_subagent_model` in
`subagent_config.py` reads `[agents.<id>].subagent_model` → `[subagent].model` →
`parent_model`. Layer A **generalizes** this rather than adding a third ladder.

## 3. Layer A — role-model config with fallbacks

### 3.1 `done.conf` schema (additive)

```toml
[agents.bob.roles]
orchestrator = "claude-opus-4-8"
worker       = "claude-haiku-4-5-20251001"
reviewer     = "claude-sonnet-5"

[agents.bob.roles.fallback]           # each value MUST be a list of non-empty strings
orchestrator = ["claude-sonnet-5"]
worker       = ["claude-sonnet-5"]
reviewer     = ["claude-opus-4-8"]

[agents.default.roles]                # reserved: seeds every persona lacking its own
reviewer = "claude-sonnet-5"
```

### 3.2 I/O and resolution are SEPARATE (Codex #3)

The pure function cannot both be pure and read `done.conf`. Split:

- `load_role_tables() -> dict` — I/O: tolerant TOML read, mirroring
  `subagent_config._raw()` (returns `{}` on any parse failure). Returns the raw
  parsed dict.
- `resolve_role_candidates(agent_id, role, parsed, parent_model) -> list[str]`
  — **PURE.** No I/O. Takes the already-parsed config. **Always returns a
  `list[str]`** (Codex #6 — never a bare `str`, never mixes shapes).

### 3.3 The exact ladder (Codex #2 — no ambiguity)

`resolve_role_candidates` builds, in this order, then filters + dedups:

```
1. persona.primary        [agents.<id>.roles].<role>
2. persona.fallbacks      [agents.<id>.roles.fallback].<role>   (in list order)
3. default.primary        [agents.default.roles].<role>
4. default.fallbacks      [agents.default.roles.fallback].<role>
5. LEGACY worker rungs     (only when role == "worker", Codex #4):
                            [agents.<id>].subagent_model, then [subagent].model
6. parent_model            (always the final candidate)
```

- **Filter (Codex #5):** at every level, `isinstance` guards. `roles` must be a
  dict; a primary must be a non-empty `str`; a `fallback` table must be a dict;
  a fallback value must be a `list` and each element a non-empty `str`. Anything
  malformed is **skipped** (never raises) — resolution continues to the next
  rung and ultimately to `parent_model`.
- **Dedup:** order-preserving, applied AFTER filtering. If `parent_model` equals
  an earlier candidate, the duplicate collapses (fine).
- **Empty case:** a persona with no `roles` table and no legacy subagent config
  yields `[parent_model]` — behavior identical to today for a role with no
  config.

### 3.4 `resolve_subagent_model` becomes a compat wrapper (Codex #4)

To avoid a third parallel ladder AND keep existing subagent configs
byte-identical:

```python
def resolve_subagent_model(agent_id, *, per_task=None, parent_model) -> str:
    if per_task:
        return per_task
    cands = resolve_role_candidates(agent_id, "worker", load_role_tables(), parent_model)
    return cands[0]                    # first candidate = today's precedence
```

Because the `worker` ladder (§3.3 step 5) includes the legacy
`subagent_model`/`[subagent].model` rungs *in the same order they had before*,
`resolve_subagent_model("alice", parent_model="P")` returns exactly what it
returns today for every existing config. This is a **refactor-under-test**:
existing subagent tests must stay green unchanged.

### 3.5 Optional availability probe (Codex #6)

Probing is a SEPARATE helper, never mixed into the resolver's return shape:

```python
def first_available(candidates: list[str], probe) -> str | None:
    return next((m for m in candidates if probe(m)), None)
```

The engine caller decides whether to probe or just walk the list doing failover
(try `candidates[0]`; on model-unavailable error, try the next).

### 3.6 Config writer must round-trip nested tables (Codex #1 — BLOCKER)

The live `config._serialize` emits only flat scalars
(`name/backend/model/yolo_pinned/compress_aware`) and `preserve` explicitly
**skips** the `"agents"` subtree — so today `update_agent(...)` / any write
**deletes** a `[agents.<id>.roles]` table. Confirmed by a live `tomllib` probe in
review.

**Fix (full round-trip preservation — chosen for maturity over a partial patch):**
the writer must read-modify-write, preserving any agent-owned nested tables
(`roles`, `roles.fallback`, and any future nested key) that `_serialize` does not
itself emit. Concretely: `_serialize` re-emits the flat scalars it owns AND
re-emits preserved nested sub-tables for each agent from the raw prior config.
Preservation must hold across **all** writers: `save_agent`, `update_agent`,
`set_harness_setting`, `set_compress_aware`.

Rationale for full (not minimal/separate-file): a partial "preserve only roles"
is a footgun the moment another nested key appears (Missions Phase 2 will add
job/mission config), and a separate config file violates the "no second config
home" principle `persona_sessions.py` was built on. Fix the writer once,
correctly.

## 4. Layer B — the `/goal` stop-gate

### 4.1 `GoalGate.decide` — pure policy (§1 decision, no LLM inside)

```python
@dataclass(frozen=True)
class GateDecision:
    action: str          # "stop" | "continue" | "escape"
    reason: str = ""

def decide(*, goal: str | None, verdict: Verdict | None,
           n_calls: int, cost: float, limits: GateLimits) -> GateDecision: ...
```

- No goal armed → `stop` (byte-identical no-op path).
- Retry budget exhausted (`n_calls`/`cost` past `limits`) → `escape` (with reason).
- `verdict.met` is True → `stop`.
- `verdict.met` is False → `continue` (reason = why-not, for the re-prompt).
- `verdict is None` (reviewer call failed) → `escape` (fail-safe: never loop on a
  broken reviewer).

`Verdict` is `{met: bool, reason: str}` — produced by the ENGINE, not the gate.

### 4.2 Engine wiring (`tracing_agent.py`, the `InterruptAgentFlow` catch)

When the agent tries to exit:

1. Read the armed goal from goal state (§4.3). None → append exit message, break
   (the existing behavior — **byte-identical no-op invariant**).
2. Goal armed → run ONE reviewer self-check: an LLM call using the **reviewer**
   role model (Layer A) — "Given the transcript, is this goal satisfied? Answer
   met=yes/no + one-line why." → `Verdict`.
3. `GoalGate.decide(...)`:
   - `stop` → append the exit message, break, **clear the goal**.
   - `continue` → append `{"role": "user", "content": "Keep working toward the
     goal: <goal>. Not yet met: <why>."}` and DO NOT append the exit → loop
     continues, next step uses the worker/orchestrator role model.
   - `escape` → append exit + a surfaced note (`"goal not met after N attempts:
     <why>"`), break. Never trap the turn.
4. `cancel_flag`/ESC is honored exactly as today (checked at loop top, ≈line
   186) — a user cancel always ends the turn, goal or not.

### 4.3 Goal state

Session-scoped store — a small `goal_state.py`, mirroring `prompt_state.py`
(get/set/clear the active goal + its retry budget for the session). The
durable-file layer is a **seam** Layer C fills later; this spec does not persist
to disk. Goal is set by the `/goal` command (§4.4).

### 4.4 `/goal` surface

- A `/goal` slash command (`harness/tui/commands.py`, per the #225 pattern):
  - `/goal <text>` → arm the goal (store the text), notify the user it's active,
    and seed the first turn (via `_seed_prompt`, added in #225) so work starts.
  - bare `/goal` → show the active goal + how to clear it (`/goal clear`).
  - `/goal clear` → disarm.
- The gate reads the armed goal from goal state; no per-turn UI.

## 5. Component boundaries (each independently testable)

| Unit | Responsibility | Depends on |
|---|---|---|
| `load_role_tables()` | tolerant TOML read of `done.conf` | filesystem |
| `resolve_role_candidates(...)` | PURE ladder → `list[str]` | nothing (parsed dict in) |
| `first_available(...)` | PURE probe over candidates | a probe callable |
| `config._serialize` (fixed) | round-trip incl. nested agent tables | raw prior config |
| `GoalGate.decide(...)` | PURE stop/continue/escape policy | nothing |
| engine hook (tracing_agent) | run reviewer call, act on decision | GoalGate, Layer A, goal_state |
| `goal_state` | session goal store | nothing (in-memory) |
| `/goal` command | arm/show/clear + seed | goal_state, app._seed_prompt |

## 6. Data flow (one goal lifecycle)

1. User: `/goal get the failing test green`. Command arms goal, seeds the turn.
2. Agent works, then tries to submit (thinks it's done).
3. Engine intercept: goal armed → reviewer self-check (reviewer role model) →
   `Verdict(met=False, reason="test still red")`.
4. `decide` → `continue`. Engine appends "Keep working… Not yet met: test still
   red" → loop continues (worker role model). No new turn.
5. Agent fixes it, tries to submit again → reviewer → `Verdict(met=True)`.
6. `decide` → `stop`. Exit message appended, turn ends, goal cleared.
7. (If the agent had thrashed past the retry cap → `escape`: turn ends with
   "goal not met after N attempts: …", never trapping the user.)

## 7. Codex review findings — all folded in

| # | Sev | Finding | Where fixed |
|---|---|---|---|
| 1 | blocker | writer deletes nested `[agents.*.roles]` on any write | §3.6 full round-trip |
| 2 | major | fallback merge order underspecified | §3.3 exact ladder |
| 3 | major | signature can't be pure AND read config | §3.2 I/O split |
| 4 | major | role-only resolver breaks legacy subagent config | §3.4 compat wrapper |
| 5 | major | malformed role/fallback table raises / iterates chars | §3.3 isinstance filter |
| 6 | major | `probe` mixes `str`/`list[str]` return (footgun) | §3.2/§3.5 always `list[str]` |

Dropped (correctly, per Codex): nested TOML is valid; `null` model rejected by
tomllib; order-preserving dedup after filtering is sound.

## 8. Error handling

| Situation | Behavior |
|---|---|
| No goal armed | no-op, byte-identical to today |
| Reviewer LLM call fails | `verdict=None` → `escape` (stop + surface); never loop |
| Retry budget exhausted | `escape` with reason |
| ESC / cancel mid-goal | cancel wins, turn ends (goal left armed for next turn) |
| Malformed role config | skipped, falls through to `parent_model` |
| Write with roles present | round-trips (writer fixed) |
| Legacy subagent config, no roles | `resolve_subagent_model` byte-identical |

## 9. Testing

**Layer A:**
- `resolve_role_candidates`: ladder precedence (persona > default > parent);
  fallback ordering within each level; `worker` legacy rungs in correct order;
  malformed primary/fallback skipped; always `list[str]`; order-preserving dedup;
  empty-config → `[parent_model]`.
- `resolve_subagent_model` compat: every existing subagent test stays GREEN
  unchanged (refactor-under-test); explicit cases for
  `[agents.<id>].subagent_model` and `[subagent].model` precedence.
- `first_available`: returns first probe-true; None if all fail.
- Writer round-trip: `save_agent`/`update_agent`/`set_harness_setting`/
  `set_compress_aware` each PRESERVE a pre-existing `[agents.<id>.roles]` +
  `roles.fallback` table.

**Layer B:**
- `GoalGate.decide`: no-goal→stop; met→stop; unmet→continue(reason);
  verdict-None→escape; budget-exhausted→escape.
- engine hook: no-goal path byte-identical no-op; unmet→loop continues (asserts
  no exit message appended, a user message appended); met→exit + goal cleared;
  reviewer-failure→escape; ESC escapes regardless of goal.
- `/goal` command: arm/show/clear; bare `/goal` shows active; seeds via
  `_seed_prompt`.

Run: `.venv/bin/python -m pytest tests/ -q`.

## 10. Rollout / compatibility

Purely additive. No `done.conf` without a `roles` table changes behavior. The
writer fix is behavior-preserving for flat configs (it only *adds* preservation
of nested tables that were previously dropped). The `resolve_subagent_model`
refactor is byte-identical by construction and guarded by its existing tests.
No migration. The `/goal` command and the engine hook are no-ops when no goal is
armed.

## 11. Open questions (non-blocking)

- Reviewer prompt wording — pin during implementation; keep it a single
  yes/no + one-line-why to bound cost.
- Default retry budget numbers (n_calls / cost caps) — pick conservative
  defaults in the plan; expose later if needed. YAGNI on config for v1.
- Whether `escape` should also emit a user-facing notification (vs. just the
  transcript note) — defer; the transcript note is sufficient for v1.
