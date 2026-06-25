# Phase 0 — Learning Log

Filled in by reading `upstream/src/minisweagent/` and running
`./run.sh --model mock` and reading `trace/runs/<latest>/events.jsonl`.

## The loop (run / step)

`run()` stops when the last message's role is `"exit"` — default.py:118-120.
`Submitted` is an `InterruptAgentFlow`, caught in `run()`, which appends an
`exit` message and breaks. One "step" is `step() = execute_actions(query())`.

## The LLM seam (query / model.query)

`model.query()` returns an assistant message whose `extra.actions` is the parsed
list of tool calls. This is observed in `llm.return` events and `traj.json`.
A model call does NOT happen when a limit check fires before the call:
`LimitsExceeded` or `TimeExceeded` abort the step before `query()` is invoked.

## The shell seam (execute_actions / env.execute)

`env.execute({"command": ...})` returns `{output, returncode, exception_info}`;
observation messages are built by `model.format_observation_messages`. The final
`action.done` needs special handling because the submit sentinel makes
`LocalEnvironment.execute` raise `Submitted` *before* returning —
local.py `_check_finished`.

## Observations from the mock run

The mock demo produces a **genuine red→green**. Turn 1 runs a stdlib assertion
against the buggy `calculator.py` — it raises `AssertionError` (returncode!=0),
visibly demonstrating the failure. Turn 2 applies a python3 in-place edit that
replaces `return a - b` with `return a + b`. Turn 3 runs the same assertion and
it now prints `PASS` (returncode=0).

Key discovery: the agent's shell commands run through `LocalEnvironment`, which
uses the **inherited shell's `python3`** (system 3.9 in this environment) — NOT
the runner's `.venv` where pytest is installed. This is why the demo uses
stdlib-only assertions rather than `python3 -m pytest`: pytest does not exist on
the agent's effective `python3`, so a pytest command would return "No module
named pytest" (rc=1) regardless of whether the fix was applied — making the
verify step meaningless.

Lesson for Phase 1: an `AgentRunner` must make a deliberate decision about the
agent's **execution environment** — which interpreter and which installed
dependencies the agent's shell commands see — because it is completely
independent of the harness's own environment.

## Interfaces I'd want to replace (feeds Phase 1 AgentRunner)

- **Model protocol**: `model.query(messages) -> assistant_message` with
  `extra.actions` attached. Replace with any model backend that speaks this
  contract.
- **Environment protocol**: `env.execute({"command": ...}) -> {output,
  returncode, exception_info}`. Replace with a sandboxed or remote executor.
- **Agent execution environment**: which `python3` (and which installed
  packages) the agent's shell commands inherit. Currently this is whatever
  `LocalEnvironment` inherits from the runner's process — a future `AgentRunner`
  should explicitly provision the agent's interpreter and dependencies (e.g.
  activate a specific venv, or run inside a container) rather than implicitly
  inheriting the harness environment.

## VibeProxy run (bonus, manual)

Not attempted in this pass.
