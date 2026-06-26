# harness — Phase 0: traced fork of mini-swe-agent

A learning-first agent harness. Phase 0 instruments a vendored, unmodified copy
of [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) (v2.4.2) with a
live event tracer, to understand the core agent loop's three seams.

## Setup

The project uses a Python 3.11 virtualenv at `.venv` with the vendored package installed editable (`python3.11 -m venv .venv && .venv/bin/pip install -e ./upstream pytest`); `./run.sh` auto-prefers it.

For the ACP agent (Phase 4), also install the ACP SDK:

```bash
.venv/bin/pip install agent-client-protocol
```

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

## ACP agent (Phase 4)

The engine is now also an **ACP server** — the control inversion of earlier
phases. Instead of a CLI driving the engine, an editor (e.g. Zed) or a smoke
client drives the agent over JSON-RPC/stdio using the
[Agent Client Protocol](https://github.com/i-am-bee/acp).

Launch the agent server:

```bash
# mock LLM (zero cost, no VibeProxy needed)
.venv/bin/python trace/acp_main.py --model mock

# real LLM through VibeProxy
.venv/bin/python trace/acp_main.py --model vibeproxy
```

The process speaks ACP over stdin/stdout. An editor or the bundled smoke client
(`trace/acp_smoke_client.py`) connects to it and drives sessions — sending
prompts, receiving streamed `message_chunk` events, issuing `cancel`, and
resuming prior sessions by ID. All Phase-1–3 capabilities (tracing, skills,
permissions, fs/terminal delegation) are available through the ACP interface.

### Phase 5 — Textual ACP client (TUI)

A single-session Textual TUI that is an **ACP client** driving the Phase-4 agent
as a subprocess. Run it:

    .venv/bin/python trace/tui_main.py --model mock          # or --model vibeproxy
    .venv/bin/python trace/tui_main.py --model mock --cwd ~/myproject

Type a prompt; watch the streaming session/update render — messages, tool-call
lines, permission prompts (as a modal), and the harness **chips**
(`classified: …`, `skills: N loaded`) that generic ACP clients (Toad/Zed) drop.
The TUI is `render.py` (pure update→display) + `client.py` (`acp.Client`) +
`app.py` (Textual shell), on the official `acp` SDK both ends.

## Layout
- `upstream/` — vendored mini-swe-agent, never edited.
- `trace/` — the tracer (events, agent overrides, mock model, runner, ACP server).
- `examples/sample-repo/` — tiny repo with one failing test.
- `docs/` — spec, plan, and learning log.

## Tests
```bash
.venv/bin/python -m pytest tests/ -v
```
