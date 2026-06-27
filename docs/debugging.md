# Debugging the harness

The TUI (`dn`) and the agent (`dn-agent` / `python -m harness.acp_main`) are
**separate processes** talking over ACP/JSON-RPC on stdio. That split is why
there are two complementary tools below: a durable trace **file** that captures
both processes, and a live **console** that captures only the TUI.

## Trace file (`--debug`) — the source of truth

Run with `--debug` to write a unified JSONL trace of the run to
`harness/runs/<timestamp>/trace.jsonl`:

    dn --debug
    # or, equivalently:
    export HARNESS_DEBUG=1        # leave it on across runs
    # or pin it per machine in done.conf:
    #   [harness]
    #   debug = true

Precedence: `--debug` flag > `HARNESS_DEBUG=1` env > `[harness] debug` in
done.conf > off. When off, no file is created and the ACP wire is byte-identical
to a normal run (zero overhead).

### Reading it

Each line is one event:

    {"seq": 0, "t": 1719500000.12, "source": "dn",    "type": "tx.prompt",     "data": {...}}
    {"seq": 1, "t": 1719500000.13, "source": "agent", "type": "task.classified","data": {...}}

- `source` — `dn` (the TUI) or `agent` (relayed from the agent subprocess over
  ACP). The TUI is the **sole writer**, so the file is already time-ordered
  across both processes. Read it top to bottom as one conversation, or hand it to
  a model: *"here's the trace, find the bug."*
- `seq` — globally monotonic. `t` — wall-clock seconds.

Filter one session with `jq`:

    jq -c 'select(.data.sid=="<sid>")' harness/runs/<ts>/trace.jsonl

### Event types

| source | types |
| --- | --- |
| `dn` | `tx.prompt`, `tx.cancel`, `rx.update`, `perm` |
| `agent` | `task.classified`, `clarify`, `chat.done`, `run.started`, `llm.call`, `llm.return`, `action`, `action.done`, `run.finished` |
| `agent` (reserved for the future cron model) | `cron.fire`, `cron.tick`, `cron.error` |

The `agent` `llm.*` / `action.*` / `run.*` events come from the engine's own
tracer (`harness/tracing_agent.py`), relayed instead of discarded. `llm.return`
carries a 120-char `content_preview`, not the full response body.

## Live TUI console (`textual console`)

The trace file already captures the agent. To watch the **TUI** side live —
widget events, `self.log(...)` — without corrupting the screen, use Textual's
devtools (install with the `dev` extra: `pip install -e ".[dev]"`):

    # terminal A — the log receiver
    textual console

    # terminal B — run the TUI in dev mode
    textual run --dev harness.tui_main:main

`textual console` only sees the **TUI** process (the agent's stdout is the ACP
wire, so the console can never see it). Use it together with the `--debug` trace
file, not instead of it.

See <https://textual.textualize.io/guide/devtools/> for console flags
(`-x EVENT` to mute groups, `-v` for verbose).
