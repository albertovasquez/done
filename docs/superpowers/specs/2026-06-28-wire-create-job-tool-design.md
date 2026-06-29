# Wire `create_job` as an agent tool — design

**Status:** approved (brainstorming) — ready for plan
**Date:** 2026-06-28
**Fixes:** the create-job gate loop — the agent collects all four gates then has no way to actually create the job, so it re-prints the gate template forever.

## Problem

The `create-job` skill instructs the model: *"call `harness/create_job` once all
gates are answered."* But `harness/create_job` is an **ACP ext-method**
(`acp_agent.py:258`, invoked by the TUI *client* over the protocol) — the model
has **no tool** to call it. The agent tool registry (`harness/tools/`) is
bash + Read/Write/Edit + load_skill + load_memory; there is no `create_job`.

Result: the model walks the four gates, reaches "now call `harness/create_job`",
finds no mechanism, and loops re-emitting the gate template. **No job can be
created from the agent.** `handle_create_job` exists and is unit-tested, but
nothing in production calls it (verified: not a tool, the TUI only seeds the
prompt, the model has no binding).

## Decision (brainstorming)

Register `create_job` as a real **agent tool** (mirrors `load_skill`/`load_memory`),
so the skill's instruction becomes literally true. `agent_id` comes from the
**environment** (active persona), never from the model — a job is always bound to
the persona the user is chatting as.

## Architecture

Mirrors the existing `Tool` protocol (`harness/tools/base.py`): `name`, JSON
`schema`, `display_label`, `execute(args, env) -> {output, returncode, exception_info}`.

### 1. `harness/tools/create_job.py` (new)

```
name = "create_job"
schema = { function: { name: "create_job", description: "...", parameters: {
    schedule: str  (cron "0 9 * * *" | interval seconds | ISO timestamp),
    description: str,
    cost: { timeout_secs: int, min_cadence_secs: int, max_consecutive_failures: int },
    grant: { paths: [str], shell: bool, network: bool, tools: [str] },
    payload: { kind: "reminder"|"agent_turn", text?: str, message?: str }  (optional;
              default a reminder using `description` if omitted),
    required: [schedule, description, cost, grant]
}}}

execute(args, env):
    agent_id = getattr(env, "_active_persona", "default")   # NEVER from args
    spec = { agent_id, schedule, description, cost, grant, payload }   # assembled
    try:
        result = handle_create_job(spec, now=time.time())   # the existing door
        return {"output": f"Created job {result['id']} ({result['name']}).",
                "returncode": 0, "exception_info": None}
    except Exception as e:                                   # fail-closed errors land here
        return {"output": f"Could not create job: {e}", "returncode": 1,
                "exception_info": None}
```

Reuses the SINGLE privileged door `handle_create_job` (`acp_agent.py:65`) — no
second write path, no duplicated validation.

**REQUIRED fields the tool must synthesize** (verified against `handle_create_job`
+ `job_to_dict`/`payload_from_dict`):
- `spec["id"]` — REQUIRED by `handle_create_job` (line 92, no default → `KeyError`).
  The model does NOT supply it; the tool generates `id = uuid.uuid4().hex[:12]`.
- `spec["payload"]` — REQUIRED (line 100: `payload_from_dict(spec["payload"])`).
  `payload_from_dict` needs `{"kind":"reminder","text":...}` or
  `{"kind":"agent_turn","message":...}`. If the model omits `payload`, the tool
  defaults to `{"kind":"reminder","text": description}`.
- `spec["name"]` — optional (`spec.get("name", spec["id"])`); the tool derives a
  short name from `description` (first ~40 chars) for a readable roster row.

`handle_create_job` returns `job_to_dict(...)` → a dict with `id` and `name` keys,
so the success message `f"Created job {r['id']} ({r['name']})."` is valid.

**`agent_id` is resolved in the tool from `env`**, so the schema does NOT expose
it — the model cannot bind a job to the wrong persona.

### 2. Stamp `env._active_persona`

The env (`AcpEnvironment`) is built at `acp_agent.py:704` with `state.cwd`;
`state.workspace_dir` is in scope there. Stamp the persona id at construction:

```
env._active_persona = state.workspace_dir.name if state.workspace_dir else "default"
```

This mirrors how the engine stamps `env._loaded_skills` per turn
(`tracing_agent.py:125`). The CLI/`run_traced` path (no workspace) falls back to
`"default"` via the tool's `getattr(..., "default")`.

### 3. Register the tool — `harness/tools/registry.py`

Add `create_job` to `build_registry()` **unconditionally** (alongside the four
defaults). Verified safe: `tests/test_tools_registry.py` asserts only that `bash`
is present, the list is fresh, and every tool satisfies the protocol — there is
NO "exactly N tools" contract. Unlike `load_skill`/`load_memory` (gated because
they need roots/content), `create_job` needs no context, so it is always
available — which is what the skill assumes.

### 4. Update the skill — `harness/skills/create-job/SKILL.md`

Replace "call `harness/create_job`" with "call the **`create_job` tool**" and the
arg shape to match the tool schema (drop `agent_id` from what the model supplies —
the tool fills it). Keep the four gates and fail-closed rule verbatim. The
ext-method framing stays only as an internal note (the TUI/tests still use it).

## Data flow

```
press n / "create a job" → create-job skill → 4 gates answered
   → model calls create_job tool with {schedule,description,cost,grant}
   → tool reads agent_id from env._active_persona
   → handle_create_job(spec) validates fail-closed + ops.add → job_id
   → tool returns "Created job <id>" → model reports success (loop ends)
```

## Error handling

- Missing/invalid gate → `handle_create_job` raises (existing fail-closed:
  `agent_id`/`cost`/`grant` required; `Every` below `min_cadence_s` rejected) →
  tool returns `returncode:1` + the message → model surfaces it and asks for the
  missing piece. The gates still gate — but now they *resolve* to a created job.
- `env` lacks `_active_persona` (mock/CLI) → defaults to `"default"`.
- The tool never raises out of `execute` (returns the error as output), matching
  the Tool contract.

## Testing

- `create_job` tool:
  - valid spec → `handle_create_job` called, returns `Created job <id>`,
    returncode 0; job present in a tmp store.
  - missing `cost` (or `grant`) → returncode 1, message contains "fail closed".
  - `agent_id` taken from `env._active_persona` (set a non-default persona on a
    fake env → the created job's `agent_id` matches it), NOT from args.
  - `env` without `_active_persona` → falls back to `"default"`.
- registry: `create_job` is in `build_registry()`.
- env stamp: after the agent builds the env, `env._active_persona` equals the
  active persona id (default and a non-default workspace).
- skill: `SKILL.md` references the `create_job` tool (sanity grep test optional).

## Files

- **New:** `harness/tools/create_job.py`, `tests/jobs/test_create_job_tool.py`.
- **Modify:** `harness/tools/registry.py` (add tool), `harness/acp_agent.py`
  (stamp `env._active_persona` at `:704`), `harness/skills/create-job/SKILL.md`
  (tool instruction), `tests/jobs/test_create_job_extmethod.py` (unchanged — the
  ext-method still works; the tool is additive).

## YAGNI / deferred

- Keep the `harness/create_job` ext-method (TUI/tests use it) — this is additive.
- `agent_id` from env only; not a model arg.
- No change to gate semantics, the store, or the daemon.
- Cron sub-floor still unenforced (separate #146 item).
