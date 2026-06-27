# Unified `--debug` trace — design

**Status:** approved design, pre-implementation
**Date:** 2026-06-27
**Branch:** `worktree-debug-trace`

## Problem

Debugging the harness is hard because the two processes that matter are opaque:

- **dn ↔ agent communication.** The TUI (`HarnessTui`) and the agent
  (`acp_main.py`) are separate OS processes talking over ACP/JSON-RPC on stdio.
  Today **neither side logs anything** about the messages crossing that boundary
  (`harness/tui/client.py`, `harness/acp_agent.py` — zero loggers).
- **The agent's internal loop is discarded.** `acp_agent.py:412` builds an
  `Emitter("/dev/null", …)` with the comment *"ACP carries the stream"* — the
  agent's structured trace (LLM calls, actions, tool results) is built and
  thrown away.
- **Future cron/loop bugs.** The persona roadmap ports an OpenClaw cron model.
  Today only a `ScheduleView` placeholder exists (`harness/tui/state.py`). When
  crons land, looping/misfiring jobs will be invisible without trace points.

`textual console` (Textual devtools) only sees the **TUI process** — and the
agent's stdout *is* the ACP wire, so the console can never see the agent. It
solves at most half the problem.

## Goal

A **durable, unified, model-readable trace on disk** that the user can point a
model (or a subagent) at and say "here's what happened, find the bug." It must
cover both the dn↔agent boundary and the agent's internal loop, in one
time-ordered file.

## Decisions (locked with the user)

| Decision | Choice |
| --- | --- |
| Sink | A JSONL file on disk (durable, greppable, survives crash) |
| Unification | **One file, one writer.** Agent relays its events to the TUI over the existing ACP metadata channel; the **TUI is the sole writer** of `runs/<ts>/trace.jsonl`. |
| Default state | **Behind a `--debug` flag** (off by default, zero overhead). Also honors `HARNESS_DEBUG=1` env and `done.conf` so it can be left on. |
| Trace depth | **Full firehose at `--debug`** — every message and every payload (LLM prompts/responses, action output bodies). No separate `--verbose` tier. |
| Textual devtools | Secondary deliverable, **same PR**: `textual-dev` + `self.log()` so `textual console` is an optional live view of the dn side. File stays source of truth. |

## Architecture (Approach B)

```
┌─ TUI process (HarnessTui) ─────────────── SOLE WRITER ──────────┐
│  user action ──► _send_prompt / action_cancel                    │
│       │           └─ tracer.emit("dn", "tx.prompt"/"tx.cancel")  │
│       ▼                                                          │
│  TuiClient.session_update(update)  ◄──── ACP/JSON-RPC ──┐        │
│       │   reads update.field_meta["harness"]["trace"]   │        │
│       ├─ tracer.emit("dn", "rx.update", kind=…)         │        │
│       └─ if trace payload present: write agent event    │        │
│                       │                                 │        │
│              DebugTracer ─► runs/<ts>/trace.jsonl        │        │
│                       ▲                                 │        │
│  request_permission ──┘ tracer.emit("dn","perm",…)      │        │
└─────────────────────────────────────────────────────────┼───────┘
                                                           │
┌─ Agent subprocess (HarnessAgent) ─────────────────────────┼──────┐
│  every boundary emit already uses with_meta(...)          │      │
│  + run_engine's Emitter (today → /dev/null)               │      │
│       └─ when --debug: each event ALSO stamped into ──────┘      │
│          with_meta(message_chunk(""), {"trace": event})          │
└──────────────────────────────────────────────────────────────────┘
```

**Why Approach B.** It is the only option that honors "one file, one writer,"
and it reuses three seams that already exist rather than inventing structure:

1. `Emitter` / `Event` (`harness/events.py`) — the writer, unchanged.
2. `with_meta(update, harness_meta)` → `field_meta["harness"]`
   (`harness/acp_emit.py:33`) — the relay. Already carries five event classes
   (`task_classified`, `persona`, `skill_load`, `memory_load`, `stream_reset`).
   A `trace` key is a natural sixth.
3. The discarded `/dev/null` Emitter (`acp_agent.py:412`) — becomes the
   agent-side trace source instead of being thrown away.

The agent **never opens the trace file.** It only relays events as
`field_meta["harness"]["trace"]` payloads. The TUI's `TuiClient.session_update`
— the single inbound chokepoint (`harness/tui/client.py:26`) — unpacks them and
writes them, interleaved with the TUI's own `dn`-side events, into one file.
Wall-clock ordered, no cross-process write race.

### Rejected approaches

- **A — each process writes its own file, correlate by run-id.** Least code
  (just repoint `/dev/null` to a real path), but produces two files. The user
  explicitly chose one file.
- **C — TUI sole writer, but agent inner events go to a sidecar file the TUI
  merges at close.** Keeps the hot inner loop off the ACP wire, but
  reintroduces a second file + merge step, contradicting "one writer."

## Trace schema

One `Event` per line (existing `harness/events.py` shape), extended with
`source`:

```json
{"seq":0,"t":1719500000.123,"source":"dn","type":"tx.prompt","data":{"sid":"s1","turn":1,"text":"fix the bug"}}
{"seq":1,"t":1719500000.130,"source":"agent","type":"task.classified","data":{"sid":"s1","task_type":"agent","skills":[],"confidence":0.9}}
{"seq":2,"t":1719500000.450,"source":"agent","type":"llm.call","data":{"sid":"s1","turn":1,"n_calls":1,"messages":[]}}
{"seq":3,"t":1719500001.900,"source":"agent","type":"llm.return","data":{"sid":"s1","turn":1,"n_calls":1,"content":"..."}}
{"seq":4,"t":1719500002.000,"source":"agent","type":"action","data":{"sid":"s1","command":"pytest -q","returncode":0,"output":"..."}}
{"seq":5,"t":1719500002.010,"source":"dn","type":"perm","data":{"sid":"s1","command":"rm x","decision":"denied"}}
```

- **`source`** — `"dn"` (TUI-originated) or `"agent"` (relayed). The one new
  field; tells the reader which side spoke.
- **`seq`** — assigned by the TUI's single Emitter, globally monotonic across
  both sources (the point of one writer). The agent's own seq is ignored on
  relay; the TUI reassigns via the existing `write_renumbered` mechanism.
- **`t`** — real wall-clock, so cross-process ordering is meaningful (the CLI's
  run-relative `0.0` clock would not order two processes).
- **`sid` / `turn`** — every event carries session id and turn so one
  conversation can be `grep`-ed out of a busy file.

### Type taxonomy

| Source | Types |
| --- | --- |
| `dn` | `tx.prompt`, `tx.cancel`, `rx.update`, `perm` |
| `agent` | `task.classified`, `persona`, `skill.load`, `memory.load`, `llm.call`, `llm.return`, `action`, `tool.start`, `tool.done`, `stream.reset`, `run.finished` |
| `agent` (future cron) | `cron.fire`, `cron.tick`, `cron.error` — reserved vocabulary, no code now |

### Payloads & size (firehose consequence)

"Everything at default" means the trace carries the agent's full event stream
with payloads: `llm.call` (n, n_messages), `llm.return` (cost, n_actions,
`content_preview` — capped at 120 chars by `TracingAgent`), `action` (command),
`action.done` (returncode, output_bytes). Full action OUTPUT text is already
carried by the live tool-call updates the TUI renders; the trace records the
command, returncode, and byte count. LLM response text is previewed (120 chars),
not full — widening it is a one-line change in `tracing_agent.py` if ever needed.
The trace can therefore contain prompt commands and response previews.
It lives under `harness/runs/<ts>/`, the same directory the CLI already writes
`events.jsonl` to. `harness/runs/` is already gitignored (`.gitignore:4`), so
the trace — including full payloads — is excluded from git by the existing rule.
No new privacy surface beyond what the CLI path already creates.

## Gating

Precedence mirrors the existing model/yolo resolution
(flag > env > done.conf > default):

1. `--debug` CLI flag on `tui_main.py`, passed through to the `acp_main.py`
   subprocess via argv (same mechanism as `--model` / `--yolo`).
2. `HARNESS_DEBUG=1` env — `export` once, leave on across runs.
3. `done.conf` `[harness] debug = true` — pin per project.
4. Default **off** — `/dev/null` Emitter stays `/dev/null`, `TuiClient` skips
   all trace work, no file created, `with_meta` payloads omit the `trace` key.
   The ACP wire stays **byte-identical to today** (preserves the no-op
   invariant, same discipline as the persona work).

## Cron seams (designed-in, NOT built)

No cron logic is built in this PR. Scope is limited to:

- Document `cron.fire` / `cron.tick` / `cron.error` in the schema.
- Keep `DebugTracer.emit(source, type, **data)` generic, so future cron code can
  call `tracer.emit("agent", "cron.fire", job=…, schedule=…)` with no new
  infrastructure.

## Textual devtools (secondary, same PR)

- Add `textual-dev` to dev dependencies in `pyproject.toml`.
- Replace TUI-side `print()` with `self.log()` at the trace chokepoints, so
  `textual console` shows the dn side live without corrupting the alt-screen.
- Document the two-terminal workflow (`textual console` + `textual run --dev`).

The file is the source of truth; the console is an optional live view, and it
only ever sees the dn side (the agent subprocess cannot reach it).

## Components & touch points

| Unit | File | Change |
| --- | --- | --- |
| `DebugTracer` | new `harness/debug_trace.py` | Thin wrapper around `Emitter`: opens `runs/<ts>/trace.jsonl`, `emit(source, type, **data)` stamps `source` and writes; no-op object when disabled. |
| Flag resolution | `harness/tui_main.py` | Parse `--debug`; resolve flag>env>done.conf>default; pass through to `acp_main` argv; construct `DebugTracer`; hand it to the TUI app. |
| Agent flag | `harness/acp_main.py` | Parse `--debug`; gate whether `with_meta` payloads include `trace`. |
| Agent relay | `harness/acp_agent.py` | When `--debug`: repoint the `/dev/null` Emitter to feed events into the relay; stamp each event into `with_meta(message_chunk(""), {"trace": event})`. |
| TUI write chokepoint | `harness/tui/client.py` (or `app.py` handler) | In `session_update`, read `field_meta["harness"]["trace"]`; when present, `tracer.emit("agent", …)`. Also emit `dn`-side `rx.update`. |
| TUI tx/perm | `harness/tui/app.py` | `tracer.emit("dn", "tx.prompt"/"tx.cancel"/"perm", …)` at send/cancel/permission sites. `print()` → `self.log()`. |
| Deps + docs | `pyproject.toml`, short doc | `textual-dev`; document console + trace-file workflow. |

## Testing

- **`DebugTracer` unit**: off → no file, no payload (byte-identical no-op);
  on → file created, events written, `source` stamped, seq monotonic.
- **Relay round-trip**: agent stamps a `trace` payload via `with_meta` →
  `TuiClient.session_update` unpacks and writes → assert it lands with
  `source:"agent"`.
- **Precedence**: flag > env > done.conf > default (mirror existing model/yolo
  precedence tests).
- **No-op guarantee**: with `--debug` off, `field_meta["harness"]` payloads are
  unchanged from today (extend the existing persona no-op tests).
- Full suite green: `.venv/bin/python -m pytest tests/ -q` from the worktree.

## Out of scope

- Any cron/scheduler implementation (only the trace vocabulary is reserved).
- An in-TUI debug pane (the user chose file-first; a pane can be a later PR).
- A `--verbose` tier (the user chose a single full-firehose `--debug`).
- Log rotation / retention policy (inherits the existing `runs/` behavior).
