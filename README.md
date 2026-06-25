# harness — Phase 0: traced fork of mini-swe-agent

A learning-first agent harness. Phase 0 instruments a vendored, unmodified copy
of [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) (v2.4.2) with a
live event tracer, to understand the core agent loop's three seams.

## Setup

The project uses a Python 3.11 virtualenv at `.venv` with the vendored package installed editable (`python3.11 -m venv .venv && .venv/bin/pip install -e ./upstream pytest`); `./run.sh` auto-prefers it.

## Run the mock demo (zero cost)

```bash
./run.sh --model mock
```

Streams events to the console and writes `trace/runs/<ts>/events.jsonl` and
`traj.json`. The mock model fixes the failing test in `examples/sample-repo`.
Reset between runs with `git checkout examples/sample-repo/calculator.py`.

## Run against VibeProxy (bonus)

Copy `.env.example` to `.env`, ensure VibeProxy is running on `:8317`, then:

```bash
./run.sh --model vibeproxy --task "Fix the failing test in examples/sample-repo."
```

## Layout
- `upstream/` — vendored mini-swe-agent, never edited.
- `trace/` — the tracer (events, agent overrides, mock model, runner).
- `examples/sample-repo/` — tiny repo with one failing test.
- `docs/` — spec, plan, and learning log.

## Tests
```bash
.venv/bin/python -m pytest tests/ -v
```
