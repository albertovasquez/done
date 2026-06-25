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

Attempted and **succeeded end-to-end** with a real model on 2026-06-25.

Findings:

- **Model names matter.** `.env.example`'s default `gpt-5.1-codex` (a guess from
  VibeProxy's setup guide) does not exist on this VibeProxy instance — it returned
  `unknown provider for model gpt-5.1-codex`. Query the live list with
  `curl -s http://localhost:8317/v1/models`. This instance exposes 20 models
  (Claude `opus-4-8`/`sonnet-4-6`/…, GPT `gpt-5.4`/`gpt-5.5`/`gpt-5.3-codex-spark`,
  image models).
- **Per-provider auth is independent.** Claude models failed with
  `auth_unavailable: no auth available (providers=claude)` — the Claude OAuth
  session in VibeProxy was not active. GPT models worked with `api_key=dummy-not-used`
  (the ChatGPT/Codex subscription was authenticated). So the dummy key is fine;
  what matters is which provider VibeProxy has a live session for.
- **The tracer is model-agnostic.** Switching from the mock to `gpt-5.4` needed
  only `VIBEPROXY_MODEL=gpt-5.4` — zero code change — and produced the identical
  event schema.

Successful run (`VIBEPROXY_MODEL=gpt-5.4 ./run.sh --model vibeproxy`):

```
run.started   model=openai/gpt-5.4
llm.return n=1  $0.0043  "I'll start by locating the repository..."
  action: pwd && ls -la && find ...                 rc=0
llm.return n=2  $0.0064  "I found a very small repo..."
  action: cat calculator.py test_calculator.py      rc=0
  action: python3 reproduce_issue.py                rc=0
llm.return n=3  $0.0081  "add is subtracting. I'll fix calculator.py"
  action: cat <<'EOF' > calculator.py  (real edit)  rc=0
llm.return n=4  $0.0050  "fix is in place ... both pass"
  action: echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT rc=0
run.finished  ok=True exit_status=Submitted n_calls=4 total_cost=$0.0238 elapsed=15.2s
```

**The key lesson — mock vs. real model.** The deterministic mock replays a fixed
script and ignores observations. The real model *reasons about them*: it ran
`find`/`cat` to discover the repo structure it had never seen, noticed an existing
reproduce script, diagnosed the bug from the actual output, wrote the fix, and
created its own `reproduce_issue.py` + `edge_cases.py` to verify. The mock proves
the loop *mechanics*; only a real model exercises the loop's *purpose* —
observation-driven reasoning. Real cost tracking also works through the proxy
($0.0238 total), via litellm's pricing.

# Phase 3 — Knowledge / Skills Layer

## Three-way separation: select → load → inject

The Router's job is classification — it returns skill *names*, not content.
`trace/skills.py` owns all file I/O: `load_catalog(skills_dir)` reads every
`SKILL.md`, parses its YAML front-matter, and returns `(name, body)` pairs;
`compose(skills_dir, names)` calls it and assembles the final `SkillLoad`
dataclass (`block`, `injected`, `skipped`). `TracingAgent` consumes the
finished `block` string and injects it — it never touches the filesystem.
Keeping selection, content-loading, and injection in separate owners makes
each piece independently testable and swappable.

## The post-render injection trick

`TracingAgent._render_template` renders the Jinja system template first (with
`StrictUndefined`), then appends the skill block to the rendered string by
string concatenation — identified by object identity, not by a template
variable. Because the skill body is appended *after* Jinja has already run,
any `{{ }}` sequences inside a skill file are literal text, not template
expressions. A skill author can write Jinja-flavored examples in their
`SKILL.md` without ever triggering `UndefinedError`.

## Skipped-and-shown failure model

A malformed `SKILL.md` (bad YAML, missing `name`/`description` keys, filename
mismatch, non-UTF-8 bytes) is *skipped* with a human-readable reason — loading
never raises. The skip appears in two places: the console print at startup and
the `skill.load` event's `skipped` list. This means a bad skill is visible but
never fatal; the agent runs with whatever good skills remain.

## `skill.load` as the Phase-4 pickup point

A `skill.load` event is emitted on every agent run, even when no skill was
selected (in which case `injected` is empty and `block` is `""`). The event
carries the full `SkillLoad` payload — injected names, skipped entries with
reasons — so the Phase-4 CLI can surface skill activity to the user without
parsing the system prompt. It is the single authoritative record of what the
knowledge layer actually delivered to a run.
