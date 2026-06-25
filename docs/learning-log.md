# Phase 0 — Learning Log

Fill these in by reading `upstream/src/minisweagent/` and by running
`./run.sh --model mock` and reading `trace/runs/<latest>/events.jsonl`.

## The loop (run / step)
- What makes `run()` stop?  (Answer: the last message's role is `"exit"` —
  default.py:118-120. `Submitted` is an `InterruptAgentFlow`, caught in `run()`,
  which appends an `exit` message and breaks.)
- What is one "step"?  (Answer: `step() = execute_actions(query())`.)

## The LLM seam (query / model.query)
- What does `model.query()` return, and how do actions attach to it?
  (Observed in `llm.return` events + `traj.json`: an assistant message whose
  `extra.actions` is the parsed tool calls.)
- When does a model call NOT happen even though the loop iterates?
  (Limit checks: `LimitsExceeded` / `TimeExceeded` before the call.)

## The shell seam (execute_actions / env.execute)
- How does an action dict become a real command + an observation?
  (`env.execute({"command": ...})` → `{output, returncode, exception_info}`;
  observation messages built by `model.format_observation_messages`.)
- Why did the final `action.done` need special handling?
  (The submit command makes `LocalEnvironment.execute` raise `Submitted`
  *before* returning — local.py `_check_finished`.)

## Observations from the mock run

In the mock run, Turn 3 ('Re-running the test to confirm the fix') emits `action.done returncode=1` even though the bug was already fixed in Turn 2. This is because the DeterministicToolcallModel replays a fixed, pre-scripted sequence and does NOT react to actual command output — a real LLM would see the passing test and adapt. This is an instructive property of a deterministic mock: it proves the loop mechanics (query→action→observation→repeat) without proving the agent reasons about observations.

## Interfaces I'd want to replace (feeds Phase 1 AgentRunner)
- (Notes on what the Model / Environment / Agent protocols would look like as a
  clean `AgentRunner` boundary.)

## VibeProxy run (bonus, manual)
- Did `--model vibeproxy` work? Did the endpoint accept `tools=[...]`
  (function-calling)? Record the outcome and any error verbatim.
