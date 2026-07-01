# Design: `/goal` — durable, enforced goal directive + role-model config

**Status:** approved (brainstorming) — ready for implementation plan
**Date:** 2026-07-01
**Author:** Claude (with Alberto)
**Tracks:** #226 (this work) · feeds #175 Missions (Layer C = Phase 2, gated — NOT built here)
**Reviewed:** two Codex adversarial passes — Layer A (6 defects) + Layer B (5
defects, 2 blockers); all folded in — §7

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
  agent until the goal is met (bounded, with an escape hatch). Because the TUI and
  agent are **separate processes**, the goal crosses the boundary via a
  `SessionState` field + `set_goal`/`clear_goal` ACP ext-methods (mirroring
  `create_job`), and the gate hooks the engine's common exit-check guarded on a
  genuine "Submitted" status.

**Explicitly out of scope → #175 Missions Phase 2 (gated):** cron-scheduled
advancement, milestone decomposition, `validate:` assertions, checkpoint
write-back, mission review UI. Layer C reuses A + B with zero rework when #175's
validation gate clears.

## 2. Why this shape

The engine's turn-termination convergence point is the loop's exit-check, not any
single exception handler. In `tracing_agent.py`, the run loop (≈180–211) breaks
when the last message's role is `"exit"` (line 209). **Multiple paths append that
exit message** — the `InterruptAgentFlow` catch (202, covering `Submitted` /
`LimitsExceeded` / `TimeExceeded`), a successful `create_job` inside
`execute_actions` (287–292), a cancel at the loop top (186–190), and
RepeatedFormatError (196–199). Hooking only line 202 (as a first draft did) would
**miss the create_job Submitted exit** and **wrongly gate cancel/limit/error
exits.**

**The gate therefore hooks the common exit-check (line 209), guarded on
`exit_status == "Submitted"`** — the only status that represents a genuine "I'm
done" claim. Cancel (`cancelled`), engine hard limits (`LimitsExceeded`,
`TimeExceeded`), and `RepeatedFormatError` are **hard-terminal** and pass through
untouched (§4.2). This covers both real submit paths (sentinel + create_job) with
one hook and lets everything else end the turn as it does today.

Re-prompting is *in-place loop continuation* — replace the pending exit with a
user message and the existing loop steps again, feeding the full transcript back
to the model. No fresh `prompt()`, no new session, no thread. (Verified by an
Explore trace + a Codex adversarial pass of the ACP + CLI paths.)

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
           reviewer_attempts: int, limits: GateLimits) -> GateDecision: ...
```

- No goal armed → `stop` (byte-identical no-op path).
- `reviewer_attempts` past `limits.max_attempts` → `escape` (with reason). This
  is the GATE's OWN budget, counted separately from the worker's `n_calls`/`cost`
  (Codex B4) — a reviewer call never consumes the worker's step_limit.
- `verdict.met` is True → `stop`.
- `verdict.met` is False → `continue` (reason = why-not, for the re-prompt).
- `verdict is None` (reviewer call failed) → `escape` (fail-safe: never loop on a
  broken reviewer).

`Verdict` is `{met: bool, reason: str}` — produced by the reviewer client (§4.5),
not the gate. The gate is pure policy over the verdict + its own attempt count.

### 4.2 Engine wiring (`tracing_agent.py`, at the line-209 exit-check)

The gate runs at the loop's exit-check (line 209), NOT the line-202 catch, so it
sees every exit path (Codex BLOCKER 2). When `self.messages[-1]["role"] ==
"exit"`:

1. If `exit_status != "Submitted"` → break unchanged. Cancel, `LimitsExceeded`,
   `TimeExceeded`, `RepeatedFormatError` are **hard-terminal and always win**
   (Codex MAJOR 4) — the goal never suppresses them.
2. If no goal armed (goal ctx is None) → break unchanged (**byte-identical no-op
   invariant** — the whole hook is skipped when the turn wasn't launched with a
   goal).
3. Goal armed + `exit_status == "Submitted"` → run ONE reviewer self-check via a
   **separate one-shot completion** (§4.5, NOT `TracingAgent.query()`) using the
   **reviewer** role model (Layer A) → `Verdict{met, reason}`.
4. `GoalGate.decide(...)`:
   - `stop` → leave the exit message, break, **clear the goal** (via the goal ctx
     callback so the session sees it cleared).
   - `continue` → **remove/replace the pending exit** with `{"role": "user",
     "content": "Keep working toward the goal: <goal>. Not yet met: <why>."}` so
     `messages[-1]["role"]` is no longer `"exit"` → loop continues; the next
     worker `query()` uses the worker/orchestrator role model.
   - `escape` → leave the exit + append a surfaced note (`"goal not met after N
     attempts: <why>"`), break. Never trap the turn.
5. Reviewer attempts + cost are tracked in the **goal ctx, separate from the
   worker's `n_calls`/`cost`** (Codex MAJOR 4) — a reviewer call must not consume
   the worker's step_limit.
6. `cancel_flag`/ESC honored exactly as today (loop top, line 186) → yields
   `exit_status == "cancelled"` → step 1 passes it through, and the goal is
   **cleared on cancel** (§4.4, Codex MAJOR 5).

### 4.3 Goal reaches the engine across the process boundary (Codex BLOCKER 1)

The TUI and the agent are **separate OS processes**; a TUI slash command cannot
write into the agent's in-process state. So (mirroring `create_job`, #159, which
crosses the same boundary via an ext-method):

- **`SessionState.goal`** — a new field on `harness/acp_session.py:SessionState`
  (`goal: GoalContext | None`), holding the goal text, retry budget, reviewer
  attempts/cost, and the reviewer role model resolved for this session.
- **`harness/set_goal` / `harness/clear_goal` ext-methods** — added to the
  `HarnessAgent.ext_method` switch (`acp_agent.py`, alongside `harness/create_job`
  et al.). `/goal <text>` in the TUI calls `harness/set_goal`; `/goal clear` calls
  `harness/clear_goal`.
- **`TracingAgent` gets an explicit goal context** at construction
  (`acp_agent.py` where it's built with `cancel_flag`) — the gate reads the goal +
  reviewer-model from this context, never from a global. When the session has no
  goal, the context is `None` and the hook is a no-op (§4.2 step 2).
- The CLI path (`run_traced.py`) constructs `TracingAgent` with goal context
  `None` — `/goal` is a TUI feature; the engine hook degrades to no-op there.

### 4.4 `/goal` surface

- A `/goal` slash command (`harness/tui/commands.py`, per the #225 pattern):
  - `/goal <text>` → call `harness/set_goal`, notify the user it's active, and
    seed the first turn (via `_seed_prompt`, #225) so work starts.
  - bare `/goal` → show the active goal + `/goal clear` hint.
  - `/goal clear` → call `harness/clear_goal`.
- **Cancel clears the goal** (Codex MAJOR 5): ESC/cancel disarms the goal so it
  cannot hijack an unrelated next turn. Re-arming is an explicit new `/goal`.

### 4.5 The reviewer client (Codex MAJOR 3)

The reviewer is a **separate one-shot LiteLLM completion with NO tools and NO
sentinel path** — it must NOT reuse `TracingAgent.query()` (which increments
`n_calls`, mutates `self.messages`) or `StreamingLitellmModel` (which always
advertises tool schemas — upstream `actions_toolcall.py` raises `FormatError`
when the model returns prose instead of a tool call, which the reviewer
correctly does). It follows the existing direct-LiteLLM pattern in
`harness/tools/review.py` (`litellm.completion(model=reviewer_model,
messages=[{"role":"user","content": <prompt>}], **vibeproxy.completion_kwargs())`).
The prompt asks for a single `met: yes|no` + one-line reason; parse leniently
(default to `met=no` if unparseable — err toward keeping the agent working, but
the retry cap in §4.1 bounds it).

## 5. Component boundaries (each independently testable)

| Unit | Responsibility | Depends on |
|---|---|---|
| `load_role_tables()` | tolerant TOML read of `done.conf` | filesystem |
| `resolve_role_candidates(...)` | PURE ladder → `list[str]` | nothing (parsed dict in) |
| `first_available(...)` | PURE probe over candidates | a probe callable |
| `config._serialize` (fixed) | round-trip incl. nested agent tables | raw prior config |
| `GoalGate.decide(...)` | PURE stop/continue/escape policy | nothing |
| reviewer client | one-shot no-tools LiteLLM met/why call | litellm, reviewer model |
| engine hook (tracing_agent) | at line-209, act on decision | GoalGate, reviewer client, goal ctx |
| `GoalContext` (on SessionState) | goal text + budget + reviewer model + attempts | nothing (per-session) |
| `set_goal`/`clear_goal` ext-methods | cross-process arm/disarm | SessionState |
| `/goal` command | arm/show/clear + seed (calls ext-methods) | app._seed_prompt, ext-methods |

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

**Layer B — second Codex pass (the engine hook, previously unreviewed):**

| # | Sev | Finding | Where fixed |
|---|---|---|---|
| B1 | blocker | goal state has no cross-process path to the engine (TUI ≠ agent process) | §4.3 SessionState.goal + set_goal/clear_goal ext-methods + explicit goal ctx into TracingAgent |
| B2 | blocker | line-202 hook misses create_job + RepeatedFormatError terminal exits | §2/§4.2 hook line-209 guarded on `exit_status=="Submitted"` |
| B3 | major | reviewer via `query()`/streaming model corrupts loop or raises FormatError (no tool call) | §4.5 separate one-shot no-tools LiteLLM client (review.py pattern) |
| B4 | major | engine hard-limits (LimitsExceeded/TimeExceeded) obscured/replayed by gate | §4.2 hard-terminal statuses always win; reviewer budget separate from worker n_calls |
| B5 | major | cancel leaves stale armed goal that hijacks the next turn | §4.4 cancel clears the goal |

Layer A: the second pass found NO new Layer A defect and confirmed §3.2–§3.6
correctly fold the original six. No sandbox false-positives in either pass.

## 8. Error handling

| Situation | Behavior |
|---|---|
| No goal armed (goal ctx None) | no-op, byte-identical to today |
| Non-Submitted exit (cancel/limits/format) | passes through untouched — hard-terminal, goal never suppresses |
| Reviewer LLM call fails | `verdict=None` → `escape` (stop + surface); never loop |
| Retry budget exhausted | `escape` with reason; reviewer budget tracked separate from worker n_calls |
| ESC / cancel mid-goal | cancel wins, turn ends, **goal cleared** (no next-turn hijack) |
| Malformed role config | skipped, falls through to `parent_model` |
| Write with roles present | round-trips (writer fixed) |
| Legacy subagent config, no roles | `resolve_subagent_model` byte-identical |
| create_job completion with goal armed | seen by the gate (line-209 hook), reviewed like any Submitted |

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
- `GoalGate.decide` (pure): no-goal→stop; met→stop; unmet→continue(reason);
  verdict-None→escape; budget-exhausted→escape.
- reviewer client: builds a no-tools `litellm.completion` call; parses
  `met yes/no`; unparseable→met=no; is NOT `TracingAgent.query` (asserts no
  `n_calls` mutation).
- engine hook (line-209): no-goal ctx → byte-identical no-op (exit unchanged);
  non-Submitted exit_status (cancelled/LimitsExceeded/TimeExceeded/
  RepeatedFormatError) → passes through even with a goal armed (B2/B4);
  Submitted+unmet → loop continues (asserts last message flipped from exit to a
  user message); Submitted+met → exit kept + goal cleared; reviewer-failure→
  escape; retry-cap→escape; reviewer budget separate from worker n_calls.
- create_job exit with goal armed → reviewed (not bypassed) (B2).
- cross-process: `harness/set_goal`/`harness/clear_goal` ext-methods set/clear
  `SessionState.goal`; `TracingAgent` built with goal ctx None → no-op (B1).
- cancel clears the goal → a following unrelated turn is NOT gated (B5).
- `/goal` command: arm (calls set_goal)/show/clear (calls clear_goal); bare
  `/goal` shows active; seeds via `_seed_prompt`.

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
