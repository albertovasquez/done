# Debugging the harness

The TUI (`dn`) and the agent (`dn-agent` / `python -m harness.acp_main`) are
**separate processes** talking over ACP/JSON-RPC on stdio. The CLI
(`run_traced.py`) is a third, single-process path. All three write durable,
**standard-format** artifacts under `harness/runs/` — so you (or an AI) read them
with `jq` and `grep`; there is no custom log viewer to learn.

## Artifacts at a glance

| File | Format | Written by | When |
| --- | --- | --- | --- |
| `harness/runs/<ts>/events.jsonl` | JSONL `{seq, t, type, data}` | the **CLI** (`run_traced.py`) | always (every CLI run) |
| `harness/runs/<ts>-<pid>/trace.jsonl` | JSONL `{seq, t, source, type, data}` | the **TUI** | only under `--debug` |
| `harness/runs/<ts>-agent-<pid>/harness.log` | plain text `time LEVEL name: msg` | the **agent subprocess** | only under `--debug` |
| `harness/runs/<ts>/traj.json` | minisweagent trajectory | the **CLI** | always (engine's own dump) |

`harness/runs/` is gitignored. The two JSONL files share a near-identical event
vocabulary; the trace adds a `source` field (`dn`/`agent`) because it spans two
processes, and uses real wall-clock `t` (the CLI uses run-relative `t`, starting
at `0.0`).

## Finding the logs

```sh
# the most recent run dir
ls -td harness/runs/*/ | head -1

# the latest trace.jsonl / events.jsonl / agent log (whichever exists)
ls -t harness/runs/*/trace.jsonl   2>/dev/null | head -1
ls -t harness/runs/*/events.jsonl  2>/dev/null | head -1
ls -t harness/runs/*-agent-*/harness.log 2>/dev/null | head -1
```

A handy shell shortcut for "the newest trace":

```sh
LATEST=$(ls -t harness/runs/*/trace.jsonl 2>/dev/null | head -1); echo "$LATEST"
```

## 1. TUI trace (`--debug`) — the cross-process source of truth

Run with `--debug` to write a unified JSONL trace covering **both** the dn↔agent
boundary and the agent's internal loop, to `harness/runs/<ts>-<pid>/trace.jsonl`:

```sh
dn --debug
# equivalently:
export HARNESS_DEBUG=1        # leave it on across runs
# or pin it per machine in done.conf:
#   [harness]
#   debug = true
```

Precedence: `--debug` flag > `HARNESS_DEBUG=1` env > `[harness] debug` in
done.conf > off. When off, no file is created and the ACP wire is byte-identical
to a normal run (zero overhead).

Each line is one event:

```json
{"seq": 0, "t": 1719500000.12, "source": "dn",    "type": "tx.prompt",      "data": {"sid": "s1", "text": "..."}}
{"seq": 1, "t": 1719500000.13, "source": "agent", "type": "task.classified", "data": {"sid": "s1", "task_type": "code_fix"}}
```

- `source` — `dn` (the TUI) or `agent` (relayed from the agent subprocess over
  ACP). The TUI is the **sole writer**, so the file is already time-ordered
  across both processes. Read it top to bottom as one conversation, or hand it to
  a model: *"here's the trace, find the bug."*
- `seq` — globally monotonic. `t` — wall-clock seconds. `data.sid` — session id
  (present on most events; filter by it to isolate one conversation).

### Event types

| source | types |
| --- | --- |
| `dn` | `tx.prompt`, `tx.cancel`, `rx.update`, `perm`, `spawn.failed`, `teardown.error` |
| `agent` | `task.classified`, `clarify`, `chat.done`, `router.failed`, `run.started`, `llm.call`, `llm.return`, `action`, `action.done`, `run.finished` |
| `agent` (reserved for the future cron model) | `cron.fire`, `cron.tick`, `cron.error` |

The `agent` `llm.*` / `action.*` / `run.*` events come from the engine's own
tracer (`harness/tracing_agent.py`), relayed instead of discarded. `llm.return`
carries a 120-char `content_preview`, not the full response body.

## 2. CLI trace (`events.jsonl`) — always written

`run_traced.py` (the headless `python harness/run_traced.py` path) writes
`harness/runs/<ts>/events.jsonl` on **every** run — no flag needed. Same JSONL
shape as the trace, minus `source` (one process), with run-relative `t`:

```json
{"seq": 0, "t": 0.0, "type": "task.classified", "data": {"task_type": "code_fix", "skills": ["python-testing"], "confidence": 0.97}}
{"seq": 1, "t": 0.0, "type": "skill.load", "data": {"injected": ["python-testing"], "skipped": []}}
```

Event types: `task.classified`, `task.classify_failed`, `skill.load`,
`run.started`, `llm.call`, `llm.return`, `action`, `action.done`, `run.finished`,
`run.failed`. Failures (`task.classify_failed`, `run.failed`) are recorded here
too — the file is **not** blank when a run dies.

## 3. Agent log (`harness.log`) — plain text, `--debug` only

`logger.warning` / `logger.exception` calls from the agent subprocess
(`harness.config`, `harness.acp_agent`, `harness.router`, `harness.persona`,
`harness.skills`, …) are invisible by default — the subprocess's stderr is hidden
behind the TUI's alt-screen. Under `--debug` they are routed to a plain-text file:

```
2026-06-27 16:11:02,341 WARNING harness.router: router classification unparseable (...); raw='...'
2026-06-27 16:11:05,902 ERROR   harness.acp_agent: agent engine failed (model='gpt-5.4', persona='default')
Traceback (most recent call last):
  ...
```

This is where you look when the trace shows a `*.failed` event and you want the
full traceback / reason. Read it with `grep`/`tail` (it is not JSON).

> Note: the **TUI** process's own `harness.*` logs are not yet file-routed
> (tracked in issue #69); use `textual console` (below) to see them live.

## Reading recipes (`jq` + `grep`)

```sh
# pretty-print every event in the latest trace as `source  type  data`
jq -rc '[.source, .type, (.data|tostring)] | @tsv' "$(ls -t harness/runs/*/trace.jsonl | head -1)"

# only the agent side (the engine loop)
jq -c 'select(.source=="agent")' "$(ls -t harness/runs/*/trace.jsonl | head -1)"

# one session/conversation
jq -c 'select(.data.sid=="<sid>")' harness/runs/<ts>-<pid>/trace.jsonl

# every failure across the run (trace or CLI events file)
jq -c 'select(.type | endswith(".failed") or . == "teardown.error")' harness/runs/<ts>*/*.jsonl

# the shell commands the agent ran (command is on the `action` event)
jq -rc 'select(.type=="action") | .data.command' harness/runs/<ts>*/*.jsonl

# their return codes (returncode is on the paired `action.done` event)
jq -rc 'select(.type=="action.done") | .data.returncode' harness/runs/<ts>*/*.jsonl

# tail the agent's plain-text log for warnings/errors
grep -E "WARNING|ERROR" "$(ls -t harness/runs/*-agent-*/harness.log | head -1)"
```

## Live TUI console (`textual console`)

The trace file captures the agent. To watch the **TUI** side live — widget
events, `self.log(...)` — without corrupting the screen, use Textual's devtools
(install with the `dev` extra: `pip install -e ".[dev]"`):

```sh
# terminal A — the log receiver
textual console

# terminal B — run the TUI in dev mode
textual run --dev harness.tui_main:main
```

`textual console` only sees the **TUI** process (the agent's stdout is the ACP
wire, so the console can never see it). Use it together with the `--debug` trace
file, not instead of it.

See <https://textual.textualize.io/guide/devtools/> for console flags
(`-x EVENT` to mute groups, `-v` for verbose).
