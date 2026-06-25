# Phase 0 — Traced fork of mini-swe-agent

**Date:** 2026-06-24
**Status:** Approved design, pre-implementation
**Scope:** Phase 0 only (of the larger harness roadmap). Learn the agent
primitive by watching it run live. Do **not** productize, do **not** build an
engine/protocol/TUI yet.

---

## 1. Goal & success criteria

**Goal:** Understand the core agent loop (prompt → LLM → action → observation →
loop) by instrumenting `mini-swe-agent` with a live event tracer, *without*
modifying upstream code.

**Done when:**

1. `harness/upstream/` holds an untouched clone of `mini-swe-agent` (its nested
   `.git` removed, version pinned in a note so the harness is one clean repo);
   `harness/trace/` holds only our code.
2. `python trace/run_traced.py` (default = mock model) prints a live event
   stream and writes `events.jsonl`, at zero API cost. The mock run is the
   **canonical, complete deliverable** — it must include the terminal submission
   seam.
3. The same runner targets **VibeProxy** via `--model vibeproxy` with no code
   change (env vars only). This is a **bonus, manually verified** path; it is not
   required to pass if VibeProxy rejects function-calling (see §4 known
   limitation). No text-based fallback is built in Phase 0.
4. `docs/learning-log.md` captures observations at each of the three seams.

> **Note on review:** §2–§5 claims about upstream were cross-checked against the
> vendored v2.4.2 source (Codex review, 2026-06-24) and corrected — in
> particular the override bodies for `query()`/`execute_actions()`, the required
> config templates, explicit dotenv loading, the `Submitted`-before-return
> terminal action, and `run.finished` in a `finally`.

---

## 2. Architecture

The design rests on one verified fact about `upstream/src/minisweagent/agents/
default.py`: the loop funnels through three overridable methods on a plain
`DefaultAgent` class. The tracer is therefore a **subclass**, not a patch.

```
harness/
  upstream/              # untouched clone of mini-swe-agent (v2.4.2)
    src/minisweagent/... # its own .git removed; pinned via UPSTREAM_VERSION note
                         # so the outer harness repo is a single clean git tree
  trace/                # OUR code only
    tracing_agent.py    # TracingAgent(DefaultAgent) — overrides 3 methods
    events.py           # Event dataclass + Emitter (console + JSONL sinks)
    run_traced.py       # entrypoint: wires model + env + agent; mock|vibeproxy
    models_mock.py      # canned DeterministicToolcallModel outputs for the demo
    runs/<runid>/events.jsonl   # written at runtime (gitignored)
  examples/sample-repo/ # tiny repo with one failing test to fix
  docs/
    learning-log.md     # notes, one section per seam
    superpowers/specs/  # this document
  .env.example          # committed config template
  .env                  # gitignored real config
```

### The three seams

Verified against `upstream/src/minisweagent/agents/default.py` (line refs from
the vendored v2.4.2 source).

| Seam          | Override method      | Events emitted                              |
|---------------|----------------------|---------------------------------------------|
| Loop lifecycle| `run()`              | `run.started` (begin), `run.finished` (end) |
| LLM call      | `query()`            | `llm.call` (real call), `llm.return`        |
| Shell exec    | `execute_actions()`  | `action` (per command), `action.done`       |

A naive "emit → `super()` → emit" wrapper does **not** work for two of the three
methods. The upstream bodies were read directly; each override is specified
below precisely.

**`run()` — reimplement the wrapper, not a pure pre/post.** Upstream `run()`
(default.py:88) re-raises on uncaught exceptions (default.py:115–117). For
`run.finished` to appear even on crash/connection error, the override wraps the
`super().run()` call in `try/…/finally` and emits `run.finished` in the
`finally`. `run.started` is emitted before `super().run()`. The run clock is a
tracer-local timer started here (NOT `DefaultAgent._start_time`, which `run()`
does not reset — default.py:50 sets it in `__init__`).

**`query()` — reimplement, do not pre-wrap.** Upstream `query()`
(default.py:128–150) performs limit checks (`LimitsExceeded`, `TimeExceeded`)
*before* any model call, then does `self.n_calls += 1; model.query(...)`. A
pre-`super()` `llm.call` would fire falsely when a limit aborts. The override
re-expresses the body: run the limit checks via a small guarded call to the
parent logic (or call `super().query()` inside a `try`), emit `llm.call` only
immediately before the real `self.model.query()`, emit `llm.return` after, and
read `cost`/`n_actions` from the returned message's `extra`.

**`execute_actions()` — reimplement the 2-line body.** Upstream
(default.py:152–155) is a list comprehension over `env.execute(action)` followed
by `format_observation_messages`. A `super()` wrapper sees only the batch, never
individual commands. Worse, the submit command makes `LocalEnvironment.execute()`
raise `Submitted` *before* returning (local.py:45–56, exceptions.py), so a
post-wrapper never emits `action.done` for the final action. The override copies
the two lines, but loops explicitly: for each action → emit `action` → call
`self.env.execute(action)` inside `try/except Submitted` → emit `action.done`
(re-raise `Submitted` after emitting so the loop terminates normally).

**Zero upstream edits** — all three are overrides in `trace/tracing_agent.py`;
`git pull` inside `upstream/` stays clean. The cost is that two overrides
duplicate ~2–6 lines of parent logic; this is an accepted, documented coupling
to v2.4.2 (pinned), revisited if upstream changes those bodies.

### Data flow

```
run_traced.py
   ├─ loads harness/.env explicitly (load_dotenv before reading any var)
   ├─ loads a mini config (supplies required system/instance templates)
   ├─ builds Model        (mock | litellm→vibeproxy)
   ├─ builds Environment  (LocalEnvironment, cwd=examples/sample-repo)
   └─ TracingAgent(model, env, emitter=emitter, **config)
          run() ──emit──▶ Emitter ──▶ console (pretty) + events.jsonl
            └─ loop: query() / execute_actions()  ◀── overridden, each emits
```

`TracingAgent.__init__(self, model, env, *, emitter, **kwargs)` consumes
`emitter` and forwards the rest to `super().__init__(model, env, **kwargs)`.
`**kwargs` MUST include the required `system_template` and `instance_template`
(`AgentConfig`, default.py:19–25) — supplied from a loaded mini config, not
hand-written here.

The `Emitter` is a simple `emit(Event)` call with two sinks (console + JSONL).
It is the embryo of a later event bus, but Phase 0 imposes **no** SSE-oriented
interface choices — it stays a plain function.

---

## 3. Event model

One dataclass, serialized one-per-line to JSONL.

```python
@dataclass
class Event:
    seq: int      # monotonic counter, ordering without wall clocks
    t: float      # seconds since run start
    type: str     # see table below
    data: dict    # type-specific payload
```

| type           | data fields                                              |
|----------------|---------------------------------------------------------|
| `run.started`  | `task`, `model_name`, `cwd`                             |
| `llm.call`     | `n` (call #), `n_messages`                              |
| `llm.return`   | `n`, `cost`, `n_actions`, `content_preview` (~120 chars)|
| `action`       | `command`                                               |
| `action.done`  | `returncode`, `output_bytes`                            |
| `run.finished` | `exit_status`, `ok` (bool), `n_calls`, `total_cost`, `elapsed_s`, `exception_type` (null on success), `exception_str` (null on success) |

`run.finished` is emitted in a `finally`, so it reports both clean exits and
crashes: `ok=true` with `exception_*` null on a normal exit (last message role
`"exit"`, including the `Submitted` path), `ok=false` with the exception type/str
populated when `super().run()` raised. This keeps a single terminal event rather
than a separate `run.error`.

**Field derivations (not all are upstream-native):**

- `action.done.output_bytes` — computed by the tracer as
  `len(output["output"].encode())`; upstream `LocalEnvironment.execute()` returns
  `output`/`returncode`/`exception_info` only (local.py:29–43), no byte count.
- `llm.return.cost` / `n_actions` — read from the returned message's
  `extra` (`extra["cost"]`, `len(extra["actions"])`), populated by the model.

**Two sinks, same event:**

- **Console** → human-readable line, e.g.
  `[t=2.1s] llm.return  #1  cost=$0.004  actions=1`
- **File** → `trace/runs/<runid>/events.jsonl`, one JSON object per line.

**Deliberately excluded (YAGNI for Phase 0):** token counts (unreliable via
proxy), full message bodies, nested spans. Full message bodies are available
from mini's own trajectory writer *only if* `output_path` is set — so
`run_traced.py` MUST set `AgentConfig.output_path` to
`trace/runs/<runid>/traj.json` for the "cross-reference instead of duplicate"
claim to hold (default.py:118–119, 180–188). Add the excluded fields later only
if a learning need appears.

---

## 4. Model switching (mock ↔ VibeProxy)

Exactly two model paths in Phase 0. The `--model` flag selects between them;
`mock` is the default.

```
python trace/run_traced.py                 # mock (default), zero cost — THE deliverable
python trace/run_traced.py --model vibeproxy   # real run via VibeProxy — bonus, manual
```

**`mock`** → `DeterministicToolcallModel(outputs=MOCK_OUTPUTS, cost_per_call=0.0)`
from `models_mock.py`. `cost_per_call` defaults to `1.0` upstream
(test_models.py), so it is explicitly set to `0.0` for "zero cost" to read true
in `total_cost`/`llm.return.cost`. Outputs are built with `make_toolcall_output()`
(or include `tool_calls` + actions with matching `tool_call_id`) so they match
the tool-call observation protocol (test_models.py:31–44). Canned sequence: run
failing test → inspect file → edit file → re-run test → submit
(`echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`, which drives the `Submitted`
path). Proves the full loop, including the terminal submission seam,
deterministically.

**`vibeproxy`** → `LitellmModel` configured as:

```python
LitellmModel(
    model_name="openai/" + os.getenv("VIBEPROXY_MODEL", "gpt-5.1-codex"),
    model_kwargs={
        "api_base": os.getenv("VIBEPROXY_BASE_URL", "http://localhost:8317/v1"),
        "api_key":  os.getenv("VIBEPROXY_API_KEY", "dummy-not-used"),
    },
    cost_tracking="ignore_errors",   # proxy pricing unknown to litellm
)
```

The `openai/` prefix tells litellm to speak the OpenAI Chat Completions protocol
against `api_base`. VibeProxy serves that on `http://localhost:8317/v1`
(VibeProxy's `FACTORY_SETUP.md`: `base_url: http://localhost:8317`, `/v1` with
`provider: openai`, `api_key: dummy-not-used`). **This endpoint claim is an
external assumption** — it is documented in VibeProxy's setup guide but not
provable from the vendored mini-swe-agent source; the implementation verifies it
empirically by making one real request (see Known limitation below).

There is intentionally **no** `--model openai/<name>` free-form path in Phase 0
(it was ambiguous about whether VibeProxy env vars still apply). Only the two
named paths above exist. Pointing at a different VibeProxy model is done via the
`VIBEPROXY_MODEL` env var, which keeps the `api_base` wiring intact.

**Config** lives in gitignored `.env`, with a committed `.env.example`:

```
VIBEPROXY_BASE_URL=http://localhost:8317/v1
VIBEPROXY_MODEL=gpt-5.1-codex
VIBEPROXY_API_KEY=dummy-not-used
```

`run_traced.py` MUST call `load_dotenv("harness/.env")` (or the repo-root path)
explicitly before reading these vars: mini-swe-agent's own dotenv load targets
the *global* user-config dir, not the harness repo (`__init__.py:26–36`).
`cost_tracking="ignore_errors"` is passed directly to `LitellmModel` (not relied
on via the `MSWEA_COST_TRACKING` env var), since that env var is read at config
construction time and the timing is fragile.

**Known limitation (function-calling), Phase 0 decision:** `LitellmModel._query()`
always sends `tools=[BASH_TOOL]` (litellm_model.py:64–70). If VibeProxy's
endpoint or the backing subscription rejects function-calling, the `vibeproxy`
run will fail. Phase 0 does **not** build a text-based fallback (that would
require a different model class, `LitellmTextbasedModel`, *and* a different
prompt template, `mini_textbased.yaml` — out of scope). If function-calling is
rejected, we record the observation in `learning-log.md` and stop there. The
mock run remains the canonical, complete deliverable; a real VibeProxy run is a
manually-verified bonus.

---

## 5. Error handling & testing

**Error handling (light, Phase-0 scope):**

- The **console** sink must never crash the agent: if a console write fails,
  swallow and log; the run continues. Observation must not break the observed.
- The **JSONL** sink is different — it backs success criterion #2, so it must
  **fail loudly at startup** if the file cannot be opened (bad path,
  permissions). A silent JSONL failure would let a run "succeed" while producing
  no required artifact. Once open, per-line write failures are logged but do not
  abort (best-effort durability with a visible warning).
- VibeProxy down / wrong port → litellm raises a connection error; catch it at
  the `run_traced.py` top level and print a clear hint
  ("Is VibeProxy running on :8317?") instead of a raw stack trace. Because
  `run.finished` is emitted in `run()`'s `finally`, the terminal event with
  `ok=false` is still written before the process exits.
- The mock path has no network and always works for the demo.

**Testing:**

- **Test A (happy path):** run `TracingAgent` with the mock model against a temp
  dir; assert the event sequence is
  `run.started → llm.call → llm.return → action → action.done → … →
  run.finished`, that the final `run.finished` has `ok=true`, and that
  `events.jsonl` parses line-by-line.
- **Test B (terminal submission):** mock sequence whose last action is the
  submit command (`echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`). Assert that
  `action.done` IS emitted for the final action (the `Submitted`-before-return
  case Codex flagged) and that `run.finished` follows with `ok=true`. This is the
  regression guard for the trickiest seam.
- Both are deterministic (mock model), so they are real regression guards, not
  smoke tests.
- No automated test for the VibeProxy path (network-dependent); manual
  verification is documented in the learning log instead.

---

## 6. Learning deliverable

`docs/learning-log.md`, pre-seeded with the three seams as headers plus prompts
to answer by reading + running:

- **The loop** — what makes `run()` stop? (last message role `== "exit"`)
- **The LLM seam** — what does `model.query()` return; how do actions attach?
- **The shell seam** — how does an action dict become a real command +
  observation?
- **Interfaces I'd want to replace** — directly feeds the Phase-1 `AgentRunner`
  design.

---

## 7. Out of scope (explicitly deferred)

- Engine extraction, `AgentRunner` abstraction (Phase 1).
- HTTP/SSE protocol (Phase 2).
- AGENTS.md / profile / skills loading (Phase 3).
- CLI product, TUI (Phases 4–5).
- GitHub PR worker (Phase 6).
- Any edit to upstream source.
