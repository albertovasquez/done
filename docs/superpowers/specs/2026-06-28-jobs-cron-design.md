# Jobs / Cron subsystem — design spec

Date: 2026-06-28
Status: design approved (brainstorming complete) — ready for implementation plan
Basis: OpenClaw's proven cron model, ported to the Python harness. Hardened by
three adversarial Codex reviews (sessions incl. 019f0fe3) against live source.

---

## 1. Goal & non-goals

**Goal.** A jobs/cron subsystem: define scheduled jobs, fire them unattended on a
cadence, see them in a dashboard (create/remove/run-now/enable), and chart their
run history. Build *up to* the proper implementation incrementally — a clean spine
now, harder surfaces as labeled later phases.

**Non-goals (v1).** Channel/delivery fan-out (Telegram/webhook). Per-run token/$ cost
caps. `session_target='main'` live-session injection. *Enforced* per-job permission
grants (recorded now, enforced Phase 2).

**The proven basis.** This is a port of OpenClaw's cron SDK (`CronJob`/`CronSchedule`/
`CronPayload`/`CronJobState`, ops `add/list/update/remove/run/start/stop/status`), not
an invention. We keep its schema and lifecycle; we adapt storage + execution to this
harness and *add* an append-only run history (OpenClaw keeps only latest-in-state).

---

## 2. Scope decision (explicit)

v1 includes the **live background daemon** — real cron fires unattended from day one.
This was a deliberate choice over a smaller "run-now first, daemon later" spine: the
larger surface is accepted because unattended firing is a hard requirement, and the
reviews proved it is buildable with the safety constraints in §6/§7. `run-now` and the
daemon share **one** executor, so execution is proven once, used twice.

---

## 3. Architecture — spine + 2 deferred seams

```
 creation skill (gate UX) ──► create_job ext-method (the ONLY writer) ──┐
                              gates derived/confirmed → fail closed      │
                              writes Job WITH recorded Grant (data)      │
                                                                         ▼
                  jobs.json (LOCKED read-modify-write, versioned)
                  + runs/<id>.jsonl (append-only, 30-day cap)
                          ▲                              ▲
              CRUD/read   │                              │ append run records
                          │                              │
   ┌─ harness-cron daemon ┴───────────┐      ┌─ TUI dashboard ───────────────┐
   │  asyncio loop: next_run_at → due  │      │  CronRow roster (reads store)  │
   │  → executor → record run/state    │      │  'new' → invokes gate skill    │
   └───────────────┬───────────────────┘      │  run-now / enable / remove     │
                   ▼                           │  per-job detail + PlotextPlot  │
        run_headless_turn(job)  ◄── shared ──► (run-now)                        │
        persona-faithful headless executor    └────────────────────────────────┘
```

### Units (each: one purpose, testable in isolation)

1. **`jobs` core** — model + store + ops. Pure data + persistence; no UI/scheduling/
   engine. Store does **locked** read-modify-write (§6.4). Run log append-only.
2. **creation skill + `create_job` ext-method** — the gate conversation (skill = UX)
   behind a single privileged writer (ext-method). `add()` is **not** a normal tool
   (§5).
3. **`harness-cron` daemon** — new console script, own asyncio loop, lifecycle +
   logging. Computes next-run, fires due jobs, records runs. Never decides permissions.
4. **executor — `run_headless_turn(job)`** — the persona-faithful headless seam (§6).
   Shared by daemon and run-now. Isolated-only in v1.
5. **TUI dashboard** — read-mostly `CronRow` roster over the store; "new" launches the
   skill; per-job detail hosts `PlotextPlot` charts off `runs.jsonl`.

### Deferred seams (recorded now, migration-free)

- **Phase 2 — Grant enforcement.** v1 *records* `Grant` per job (data, `enforced=False`).
  Phase 2 adds the `Grant` object + per-run **filtered tool registry** (allowed tools,
  path-confinement resolver, read/write mode) into the env and flips headless to
  **deny-by-default** — closing audit **#102** (file-tools bypass) and **#106** (path
  confinement). No schema migration: the field already exists.
- **Phase 3 — `main` target.** v1 is `isolated`-only. `main` (output into a live
  session) is a later pure addition via a TUI IPC endpoint or a `runs.jsonl`-poll +
  notify. New `session_target` value, no rework.

### Honest v1 trade-off (stated, not hidden)
Until Phase 2, an unattended job runs under the *existing* (open) permission gate,
confined only by running in its own workspace. The job's recorded `Grant` carries
`enforced=False`, which the dashboard surfaces as "granted X — enforcement pending."
Nothing is silently full-access; it is *visibly* granted-but-unenforced.

---

## 4. Data model (frozen dataclasses → JSON)

### `Job`
| field | type | notes |
|---|---|---|
| `id` | `str` | slug |
| `name` | `str` | |
| `description` | `str = ""` | |
| `agent_id` | `str` | **REQUIRED, non-null** — drives workspace/model/memory |
| `enabled` | `bool = True` | |
| `delete_after_run` | `bool \| None = None` | tri-state; `None` → OpenClaw default (auto-delete `at` after success) |
| `created_at` / `updated_at` | `float` | epoch seconds |
| `schedule` | `Schedule` | union |
| `payload` | `Payload` | union |
| `session_target` | `'isolated'` | v1 ONLY value; `'main'` reserved Phase 3 |
| `grant` | `Grant` | recorded; `enforced=False` in v1 |
| `cost` | `CostGate` | always-on hard gate |
| `state` | `JobState` | runtime |

### `Schedule` — tagged union
- `At(when_iso: str)` — one-shot.
- `Every(seconds: int, anchor: float | None = None)`.
- `Cron(expr: str, tz: str | None = None, stagger_ms: int | None = None)` — 5/6-field
  via `croniter`; `tz=None` = host-local (stored meaning); UTC is UI display only;
  keep `stagger_ms` (`None` = deterministic stagger, `0` = exact).

`next_run_at(schedule, now, state) -> float | None` — pure.

### `Payload` — tagged union
- `Reminder(text: str)` — OpenClaw `systemEvent`; logs/notifies, no inference.
- `AgentTurn(message: str, model: str | None = None, agent_options: ExtBag = {})` —
  runs the owning persona. `agent_options` is the lossless extension bag preserving
  unmodeled OpenClaw `agentTurn` fields (fallbacks/thinking/lightContext/…). `model`
  is an explicit override; absent it, the executor resolves via
  `resolve_session_model(agent_id)` — **never** the process default.

### `Grant` — recorded v1, enforced Phase 2
`tools: list[str] | 'inherit'`, `paths: 'workspace' | list[str]`, `write: bool`,
`exec: bool`, `network: bool`, `enforced: bool = False`.

### `CostGate` — always-on hard gate (the three confirmed at creation)
`timeout_s: int` (per-run wall-clock kill), `min_cadence_s: int` (cadence-floor
footgun guard), `max_consecutive_failures: int` (→ auto-disable).

### `JobState` — runtime
`next_run_at`, `running_since`, `last_run_at: float|None`;
`last_status: 'ok'|'error'|'skipped'|None`; `last_error: str|None`;
`last_duration: float|None` (chart fuel); `consecutive_errors: int = 0`;
`version: int = 0` (compare-and-swap for the locked store).

### `JobRun` — append-only history (chart series)
`job_id, started_at: float, duration: float, status, error: str|None` →
`runs/<job_id>.jsonl`, append-only, **30-day rolling cap**.

---

## 5. Creation skill + the single door

**One door.** A job comes into being only through the **`create_job` ext-method**
(pattern: `harness/set_persona` ext-methods). `add()` / store internals are **not**
exposed to the normal tool registry — that is what makes "single door" a real
boundary, not convention (Codex: skills are text in model context, cannot enforce a
write boundary). The dashboard "new" button invokes the **same** skill → same
ext-method. No second write path.

**Gate flow (fail closed — `job exists ⇔ every gate answered`):**
1. Human describes the job in natural language.
2. Skill **derives** capability/path/network needs from the description; proposes
   sensible defaults; **asks only when a concern is genuine**. The answers populate the
   recorded `Grant`.
3. Skill **always hard-gates cost**: confirm cadence ≥ floor, set `timeout_s`, set
   `max_consecutive_failures`.
4. If any gate is unanswered/unapproved → **refuse to create** (nothing written).
5. On success the ext-method writes the `Job` with its explicit recorded `Grant` +
   `CostGate`.

**Runtime never decides permissions** — it enforces the recorded grant (Phase 2) or
runs under the open gate with the grant labeled `enforced=False` (v1). Creation
decides; runtime enforces.

---

## 6. Executor — the persona-fidelity rule (the load-bearing safety invariant)

> **`run_headless_turn(job)` resolves persona/model/workspace/memory through the SAME
> chokepoint as the interactive path. A job for persona A is indistinguishable, in
> composition, from A typing the prompt live. The daemon never short-circuits
> `compose_context`.**

This exists because the *natural* factoring source, `run_traced.py`, **already
bypasses the persona chokepoint** the ACP/TUI path uses (`acp_agent.py:497` →
`compose_context`, `persona.py:89/111`). `run_traced.py` builds blocks manually
(`:166-195`) and resolves model via `vibeproxy.default_model()` (`:150`, reads
`VIBEPROXY_MODEL` env) instead of `resolve_session_model(agent_id)`
(`persona_sessions.py:20`). So:

**Do NOT factor `run_traced.py` as-is — correct its chokepoint + model bypass during
extraction.** `run_headless_turn(job)` MUST:

1. **Compose via the chokepoint** — `persona.compose_context(...)` for
   prompt/persona/memory/skills blocks (not manual assembly).
2. **Resolve model per job** — `resolve_session_model(job.agent_id, ...)`; construct a
   fresh model per run with that explicit id; **never** rely on/mutate `VIBEPROXY_MODEL`.
3. **Drive workspace/memory from `agent_id`** — `resolve_workspace(job.agent_id)`;
   missing persona → **fail closed / auto-disable**, never fall back to default.
4. **Fresh-everything-per-job** — fresh model + fresh `LocalEnvironment(cwd, env=job_env)`
   + fresh runner per run. Compute per-job env **as data**; never write `os.environ`;
   do not call `paths.load_env` after startup. (`TracingAgent` resets
   `_loaded_skills`/`_loaded_memories` per run — safe iff each job has its own env.)
5. **Timeout** — enforce both `LocalEnvironmentConfig.timeout` and an outer
   asyncio/process timeout (`CostGate.timeout_s`).

### Multi-persona safety ledger (verified vs live source)
| dimension | with the rule above |
|---|---|
| prompt/memory/skills composition | SAFE — routes through `compose_context` |
| model identity | SAFE — `resolve_session_model(agent_id)`, no env leak |
| workspace/memory isolation | SAFE — driven by `agent_id`, fail-closed |
| concurrent jobs in one daemon | SAFE — fresh model/env/runner, env-as-data |
| storage ownership | SAFE — global `jobs.json` keyed by `agent_id` does not conflict with persona dirs |

### Orphan handling
Persona truth = directory existence (`persona_select.py:59`); no delete API, but manual
deletion happens. **Every daemon tick validates `agent_id` via `resolve_workspace`;
unknown-persona jobs auto-disable and surface separately in the dashboard.** Never
silently recreate or fall back.

---

## 7. Storage mechanics (Codex MAJOR-3)

- `jobs.json` = `{version: 1, jobs: [...]}` — one global store keyed by `agent_id`.
- **Every mutation** takes an **interprocess `flock`** around read-modify-write; write
  via temp + `os.replace` *inside* the lock. **Do NOT copy `config.py`'s atomic-rename
  pattern — it is unlocked** (`config.py:142,160`) and would lose updates with daemon +
  TUI + skill all writing.
- `JobState.version` → compare-and-swap: daemon writing run-state cannot clobber a TUI
  enable/disable.
- Run logs append-only, single writer per file (the run that owns it); 30-day cap.

---

## 8. Daemon lifecycle

- New console script `harness-cron = "harness.jobs.cron_main:main"` (none exists today;
  only `dn`/`dn-agent` registered — `pyproject.toml:29`).
- asyncio loop: tick → load jobs (locked read) → for each due+enabled job, validate
  `agent_id`, run via `run_headless_turn`, record `JobRun` + update `JobState`
  (compare-and-swap), recompute `next_run_at`.
- Concurrency: bounded; each job gets fresh model/env/runner (§6.4).
- `wake_mode` honored for the daemon's fire timing. `start`/`stop`/`status` ops.

---

## 9. UI wiring (data source now exists)

- `CronRow` ← one `Job` (designed-only today: `components.md:373-380`).
- `ScheduleBadge` ← `JobState.next_run_at` (`components.md:360-371`).
- `ScheduleView(label, when)` stub ← `label=name`, `when=humanize(next_run_at)`.
- **Dashboard** = `CronRow` roster + create(skill)/remove/run-now/enable.
- **Charts** = `PlotextPlot` (add `plotext`/`textual-plotext` dep) over per-job
  `runs.jsonl` (durations, success rate) in a per-job detail panel — the dedicated
  surface where a live Textual chart widget is the right tool.

---

## 10. Testing strategy

- **`jobs` core** — pure: model round-trip (incl. OpenClaw import lossless via
  `agent_options`), `next_run_at` per schedule kind, store lock/version compare-and-swap
  (simulate concurrent writers), 30-day cap. No engine needed.
- **gate/creation** — fail-closed (unanswered gate → no write), cost-gate enforcement,
  single-door (no raw `add()` tool exposed).
- **executor persona-fidelity** — assert a job for persona A composes the SAME blocks +
  model + workspace as A's interactive turn (the core regression guard); orphan
  `agent_id` → auto-disable, no default fallback; per-job env isolation (no `os.environ`
  mutation across two personas' jobs).
- **daemon** — due-selection, compare-and-swap state update, auto-disable on
  `max_consecutive_failures`, timeout kill.
- No CI on repo → local full-suite green is the gate (per project norm).

---

## 11. Phasing summary

| phase | contents |
|---|---|
| **1 (v1)** | model + locked store + CRUD + gate skill/ext-method + run-now + **live daemon** + persona-faithful `run_headless_turn` + dashboard + charts |
| **2** | Grant **enforcement** (filtered tool registry + path confinement + headless deny-by-default; closes #102/#106) |
| **3** | `session_target='main'` (TUI IPC or runs.jsonl-poll+notify) |

---

## 12. Open items for the implementation plan
- `croniter` dependency (5/6-field + tz) vs hand-roll — pick at plan time (affects
  #103-style stdlib concerns; must support 6-field for OpenClaw import).
- Exact `flock` helper (stdlib `fcntl` vs a small dep) for the store.
- Bounded daemon concurrency limit (start small, e.g. 2-4).
- Where `runs/` and `jobs.json` live precisely (harness config dir, not site-packages —
  cf. audit #111).
