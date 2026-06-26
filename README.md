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

A single-session Textual TUI that is an **ACP client**. It launches the Phase-4
agent (`trace/acp_main.py`) as a subprocess and drives it over ACP — so the TUI
talks to the engine exactly the way Zed would, not by importing it.

**Run it:**

```bash
# mock LLM (zero cost, no VibeProxy needed)
.venv/bin/python trace/tui_main.py

# real LLM through VibeProxy
.venv/bin/python trace/tui_main.py --model vibeproxy

# point it at a different project directory (default: current dir)
.venv/bin/python trace/tui_main.py --model vibeproxy --cwd ~/myproject
```

**Flags:**

| Flag | Values | Default | Meaning |
|---|---|---|---|
| `--model` | `mock`, `vibeproxy` | `mock` | which LLM the agent subprocess uses |
| `--cwd` | a path | `.` | the working directory the agent operates in |

**Using it:**

- Type a prompt in the bottom input box and press **Enter** to send. The input
  is disabled while a turn streams, then re-enabled.
- **Esc** cancels the in-flight turn (best-effort, at the next command boundary).
- **Ctrl-Q** quits (this also tears down the agent subprocess cleanly).

**What you'll see in the transcript**, as the turn streams:

- `agent:` / thinking lines — the streamed assistant message chunks.
- `$ <command>` lines with a colored status (`pending`→`completed ✓`/`failed ✗`)
  — each shell command the agent runs, rendered as a tool call.
- A modal **permission prompt** before a command runs (Allow / Reject) when the
  agent asks — your choice is sent back over ACP.
- The harness **chips** that generic ACP clients (Toad/Zed) silently drop —
  e.g. `[classified: chat_question · skills: — · conf: 0.99]` and
  `[skills: 1 loaded, 0 skipped]`. These come from our custom `_meta["harness"]`
  stream and are the whole reason to build our own client instead of using Zed.

**How it's built:** `trace/tui/render.py` (pure update→display, where the chips
live and are unit-tested), `trace/tui/client.py` (`acp.Client` — marshals the
session/update stream to the UI, bridges permission via an `asyncio.Future`),
`trace/tui/app.py` (the Textual shell + permission modal), and
`trace/tui_main.py` (entrypoint). Official `acp` SDK on both ends; single async
loop, no worker threads on the client side.

## Layout
- `upstream/` — vendored mini-swe-agent, never edited.
- `trace/` — the tracer (events, agent overrides, mock model, runner, ACP server).
- `trace/tui/` — the Textual ACP client (render core, `acp.Client`, app); entrypoint `trace/tui_main.py`.
- `examples/sample-repo/` — tiny repo with one failing test.
- `docs/` — spec, plan, and learning log.

## Tests
```bash
.venv/bin/python -m pytest tests/ -v
```
