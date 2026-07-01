# Worker activity card + footer redesign — design

**Date:** 2026-07-01
**Branch:** `feat/worker-card-footer`
**Status:** design approved; spec under user review

Two independent features, sharing one small extracted helper (`tui/fmt.py`):

1. **Worker activity card** — a live, per-worker read-out when `subagent` fires N
   parallel workers, modelled on Claude Code's "N background agents launched" card.
2. **Footer redesign** — reclaim vertical/horizontal space, make the footer
   always-visible, and replace the current plain `ctx N/W` text with a visual bar.

The two features are separable and can ship as two PRs. `tui/fmt.py` (extracted
token/elapsed formatting) is a shared prerequisite; whichever ships first
introduces it.

---

## Background: verified facts about the live code

These were confirmed by reading source (not summaries). They are load-bearing;
the design depends on them and any drift invalidates the corresponding section.

### Worker dispatch is opaque to the TUI today
- `SubagentTool.execute(args, env)` runs N workers in a `ThreadPoolExecutor`
  (`harness/tools/subagent.py:141-149`) and returns ONE digest dict. The workers
  are invisible to the TUI — not by omission but by architecture.
- Each `_run_one_worker` builds an agent via `build_persona_agent(...)`
  (`agent_build.py:67`), which **always** returns a `MiniSweAgentRunner`, then
  iterates `for _ in runner.run(task_str): pass` (`subagent.py:99`) — **discarding
  every yielded event.**
- `MiniSweAgentRunner.run()` builds a `QueueEmitter` internally
  (`runner.py:88`), runs a `TracingAgent` on a worker thread, and **yields every
  event** (`runner.py:121`): `run.started`, `llm.return` (carries `usage`),
  `action` / `action.done`, `run.finished` (carries `elapsed_s`, `total_cost`).
  So the per-worker token/elapsed data the card needs **already flows** — it is
  simply thrown away at `subagent.py:99`.

### The tool-execution model has NO mid-execute progress channel
- `AcpEnvironment.execute` (`acp_env.py:43-99`) is strictly
  `on_command("start")` → **opaque tool run** → `on_command("done")` (in
  `finally`, line 87). `on_command` is the ONLY emitter of ACP updates, and
  nothing calls it between start and done. Tools receive `env` but `env` has no
  progress-emission method today.
- **Consequence:** a *live* worker card is impossible without adding a
  mid-execute progress capability. This design adds exactly one:
  `env.emit_progress(meta)`.

### Two-process boundary is real
- The TUI spawns the agent as a **separate OS process** (`tui/app.py:278`,
  `acp.spawn_agent_process`), JSON-RPC over stdio. No shared memory. Worker
  events must cross the pipe; the only mid-turn writer is the ACP connection.

### ACP emission concurrency (verified)
- `on_command` marshals via
  `asyncio.run_coroutine_threadsafe(self._conn.session_update(...), loop)`
  (`acp_agent.py:632`), `loop` captured from the agent's event loop
  (`acp_agent.py:365`). `run_coroutine_threadsafe` from N threads is **safe** —
  the single event loop serializes sends FIFO.
- **But** `.result()` **blocks** the calling thread until the loop round-trips.
  With up to 4 worker threads emitting, over-eager emission would serialize the
  workers and defeat the parallelism that is the whole point of `subagent`.
  Mitigation: coalesce behind a time gate so emission is rare (a few/sec total).
- `field_meta` is safe for concurrent sends: each emit builds a **fresh** update
  object; `acp_emit.with_meta` mutation is scoped per-send (`acp_agent.py:594`,
  `618`, `640`).

### The worker→events routing is INCIDENTAL, not contract (RISK)
- `MiniSweAgentRunner`'s docstring (`runner.py:1-5`) says it is the CLI/dev
  bridge, used **only** by `run_traced.py`; "the production ACP path drives
  TracingAgent directly and does NOT use this." Workers get yieldable events
  purely because `build_persona_agent` happens to return this runner. There is
  **zero** design doc/test/comment asserting workers depend on it.
- **This design turns the accident into a documented contract** (test + comment),
  so a future refactor cannot silently sever the bridge.

### Footer today (audit)
- Single `#statusbar` container (`app.py:180`, populated `_mount_status_contents`
  `app.py:458-470`), docked `height: 2` (`app.tcss:89`). Children: YOLO chip,
  compress-aware chip, persona, cwd (`_status_left`), context (`_status_right` →
  `_context_tagline` `app.py:489`, format `ctx N/W | R left`). Event-driven
  refresh via `_refresh_status` (no timer). No O(n²) issues.
- The `5h`/`7d`/`ld:`/`mem:` elements in the reference screenshot are **Claude
  Code's** footer, not Done's — Done never had them.
- Cleanup candidates found: `_fmt_tokens` duplicated (footer + `activity_status`);
  `#statusbar-compress-aware` has no CSS rule (default styling); `_compacted`
  stored (`app.py:144`) but never rendered.

---

## Feature 1 — Worker activity card

### Approved behaviour
- **Fully live, per-worker:** each row updates in real time — elapsed ticks up,
  token count grows, status flips running → done/failed as it happens.
- **Display-only:** no keybindings, no expand, no cancel. A read-out, not a
  controller. (Matches how workers actually behave: fire-and-forget, parent
  blocks on `pool.map`.)
- **Live in `ActivityRegion`, then a collapsed summary persists in the
  transcript:** while running, the card lives in the pinned activity zone; when
  all workers finish, a one-line summary (`✓ N workers finished · 3m 12s ·
  ↓ 198.8k tokens`) lands in `#transcript` as a permanent record.

### Architecture (agent side)

**New capability — `AcpEnvironment.emit_progress(meta: dict)`** (`acp_env.py`):
- Builds a fresh update carrying `field_meta` via `acp_emit.with_meta(...,
  {"workers": meta})` and marshals it through
  `asyncio.run_coroutine_threadsafe(self._conn.session_update(session_id, upd),
  loop)`. **Progress emits are always fire-and-forget** — never call `.result()`
  on them. Progress has no ordering requirement vs. tool completion, and blocking
  on `.result()` from N worker threads is exactly what would serialize the
  workers (see §Background "ACP emission concurrency"). Contrast `on_command`,
  which DOES block because start/done must be ordered around the tool run.
- `LocalEnvironment` (worker env, cron/CLI path) gets a **no-op** `emit_progress`
  so the same call is safe off the ACP path. The worker's own env is a bare
  `LocalEnvironment` (`agent_build.py:57`) with no `_conn`; **workers never call
  emit_progress** — only the Collector does, on the PARENT env.

**Collector** (inside `SubagentTool.execute`, closes over the **parent** `env`):
- A `threading.Lock`-guarded per-worker state map: `{idx → {status, started_at,
  elapsed, tokens}}`.
- `_run_one_worker` gains an `on_event(idx, item)` callback. Its loop changes:
  `for _ in runner.run(task_str): pass`  →  `for item in runner.run(task_str):
  on_event(idx, item)`.
- `on_event` folds the event under the lock (`run.started` → seed `started_at` +
  status=running; `llm.return` → add `usage.total` to `tokens`; `run.finished` →
  status=done/failed + final `elapsed_s`). Then, **if ≥ ~80ms since last flush**,
  it serializes the whole map (still under the lock) and calls parent
  `env.emit_progress({"action": "progress", "workers": [...]})`. The time gate is
  enforced **before** marshaling, so emits stay rare.
- **Dispatch** (before `pool.map`): one `emit_progress({"action": "dispatched",
  "workers": [{idx, goal}, ...]})` so header + all rows appear instantly.
- **Finished** (after `pool.map`): one `emit_progress({"action": "finished",
  "summary": {ok, failed, total_elapsed, total_tokens}})`. Then the existing
  digest returns unchanged.

**Pin the incidental contract:**
- Add a test asserting workers (via `build_persona_agent` → `runner.run`) yield
  `run.started` / `run.finished`, and a comment at `agent_build.py` / `subagent.py`
  documenting that the worker card depends on `runner.run()` yielding events.

### Architecture (TUI side)

**Render decode** (`tui/render.py`):
- Extend the existing `field_meta` reader (the path `harness_chips` already uses)
  to pull `field_meta["workers"]` → `RenderedItem(kind="worker_batch",
  action=..., workers=[...], summary=...)`. **No new ACP dataclass, no new
  duck-typed class name** — it rides the meta seam that already crosses the pipe.

**Data model** (`tui/state.py`):
- New frozen `WorkerView { idx, goal, status, started_at, elapsed, tokens }`.
- Add `workers: tuple[WorkerView, ...]` and `worker_summary: WorkerSummary | None`
  to the snapshot.
- Three reducer cases in `_reduce_agent()` for `worker_batch`:
  - `dispatched` → seed `workers` from goals, all `pending`.
  - `progress` → merge deltas by `idx` in place.
  - `finished` → set `worker_summary`, clear live `workers`.

**Shared format helper** (`tui/fmt.py`, NEW):
- Extract `_fmt_elapsed` + `_fmt_tokens` + the
  `glyph label · (elapsed · ↓ tokens)` string. `ActivityStatus` and the worker
  row both call it. **This dedupes the footer's `_fmt_tokens` too.**
- Rationale: `ActivityStatus.line_for` (`activity_status.py:38,44`) blanks unless
  `snap.state ∈ _WORKING` and derives "done" from `snap.tools` — so the widget is
  **not** a drop-in for worker rows. Reuse the *format*, not the widget.

**Widget** (`tui/widgets/worker_card.py`, NEW):
- `WorkerCard(Vertical)`, modelled on `ActivityRegion`: header via existing
  `card_markup()` (`● N workers launched`) + one row per `WorkerView` rendered
  through `tui/fmt.py`. `update_from(snapshot)` re-renders each reduce.
- Lives in the pinned `ActivityRegion` while running.
- **Elapsed ticks TUI-side:** the existing 0.25s `_tick_elapsed` recomputes each
  row's elapsed from `started_at`; the agent side sends only state changes, no
  per-second heartbeat.

**Transcript handoff** (`tui/app.py`):
- On the `finished` reduce, `_apply` mounts a one-line `Static` summary into
  `#transcript` via the existing `_append_streaming_below_footer()`, then the
  live card clears when the turn ends (same lifecycle as tool rows).

### Reuse vs. new (honest tally)
- **Reused:** `card_markup`, `ActivityRegion` container pattern, `update_from`
  push, `_tick_elapsed`, `_append_streaming_below_footer`, the `field_meta`
  decode path, `with_meta`, the `run_coroutine_threadsafe` marshal path.
- **New:** `env.emit_progress` (+ no-op on `LocalEnvironment`), the Collector +
  `on_event` callback, `WorkerView` + `WorkerSummary` + 3 reducer cases,
  `WorkerCard`, `tui/fmt.py`, the `field_meta["workers"]` render branch, the
  contract-pinning test + comment.

### Error handling
- Worker failure: `_run_one_worker` already returns `(False, err)` and the digest
  renders `✗`. The Collector marks that row `failed` on `run.finished` with
  `ok=false`; the summary counts `failed`.
- `emit_progress` off the ACP path: no-op (cron/CLI). No crash, no card — correct.
- If the coalescing flush races the `finished` emit, `finished` is authoritative
  (it sets summary and clears `workers`); a late `progress` after `finished` is
  ignored by the reducer (no `workers` to merge into).

### Testing
- **Collector unit test:** feed synthetic `run.started`/`llm.return`/`run.finished`
  events for N workers; assert the coalesced `progress` payloads and the final
  `summary` (token totals, ok/failed counts, elapsed).
- **Time-gate test:** assert emission frequency is bounded (≥80ms spacing) under a
  burst of events — guards the worker-serialization regression.
- **Contract test:** assert `build_persona_agent(...).run(...)` yields
  `run.started`/`run.finished` (pins the incidental routing).
- **Reducer test:** `dispatched`/`progress`/`finished` fold into snapshot
  correctly, including late-`progress`-after-`finished` no-op.
- **Render/fmt test:** `tui/fmt.py` output matches the current `ActivityStatus`
  line format (no visual regression); footer `_fmt_tokens` output unchanged.
- **No-op env test:** `LocalEnvironment.emit_progress` is a safe no-op.

---

## Feature 2 — Footer redesign

### Approved behaviour
- **Icon-ify the two default-ON toggles:** glyph-only when ON (the expected
  default), full labeled chip when OFF (the surprising state worth spelling out).
  - ON:  `▶▶` (bypass, coloured for its danger state) and `▤` (compress-aware).
  - OFF: `▶▶ bypass OFF`, `compress-aware OFF`.
- **Always-visible:** footer stays docked and cannot wrap off-screen.
- **ctx as a visual bar** replacing the plain `ctx N/W | R left` text.
- **Cleanup** folded in.
- **5h/7d rate-limit bars: DROPPED** — see decision below.

### `StatusChip` collapse mode (`tui/widgets/status_chip.py`)
- `for_yolo` / `for_compress_aware` render **glyph-only** when the state is ON,
  coloured by state; expand to the full labeled chip when OFF.
- Keep the existing click-to-toggle action binding on the glyph (discoverability
  without hover). The "label only when OFF" rule self-solves discovery: the state
  you'd want to notice announces itself.

### ctx bar (`tui/app.py` `_context_tagline` / `_status_right`)
- Replace `ctx N/W | R left` text with a compact bar + readout:
  `ctx ██░░░░ 8% · 92k/1M`, coloured accent → warning → error as it fills toward
  the window limit. **Pure display on existing data** (`self._tokens` +
  `resolve_ctx_window()`); rides the existing `_refresh_status` cadence. No new
  plumbing.

### Always-visible + layout
- Footer already docks `height: 2` (`app.tcss:89`); the dock is what guarantees
  visibility. Glyphs use `width: auto`, path stays `1fr`; reclaimed horizontal
  space goes to the path + ctx readout. Add the **missing
  `#statusbar-compress-aware` CSS rule** (copy `-mode`).

### Cleanup (all from the audit)
- `_fmt_tokens`: use the shared `tui/fmt.py` helper (kills the duplication).
- `_compacted` (`app.py:144`): **surface it** as a dim inline note (`· compacted`)
  when a compaction happened this turn, rather than leave it stored-but-dead.
- Add the `#statusbar-compress-aware` CSS rule.

### Decision: 5h/7d rate-limit bars DROPPED
- Investigation (proxy probe) confirmed **no truthful source exists**:
  CLIProxyAPI exposes only `/v0/management` (auth) + `/v1/models` — no `/usage`,
  `/quota`, or rate-limit endpoint. The harness parses **no** rate-limit signal
  and persists **no** time-windowed usage; token data is per-turn ephemeral
  (reset to 0 each turn, `app.py:1091`). Providers do not expose per-account
  windows to third parties.
- A local rolling-accounting bar would measure *Done's own spend*, not the
  provider's real limit — a number that looks like Claude Code's rate-limit bar
  but means something else. Rejected as misleading.
- **The 5h/7d concept is Claude Code's, not Done's need.** Dropped entirely — no
  follow-up issue, no rolling-usage ambition. The footer ships all-truthful.

### Testing
- `StatusChip` render test: ON → glyph-only; OFF → full labeled chip; colours by
  state; click binding intact.
- ctx bar render test: bar fill + colour thresholds at representative token/window
  ratios; readout format.
- CSS: `#statusbar-compress-aware` rule present; footer height stays 2; no wrap.
- `_compacted` note renders when a compaction occurred, absent otherwise.

---

## Out of scope (explicit)
- Worker cancel/kill, expand/collapse drill-in (display-only was chosen).
- Any new ACP update *type* (we ride `field_meta`).
- 5h/7d bars and any rolling-usage accounting (dropped).
- Changing how workers execute (thread pool, model, toolset unchanged).
- `ld:`/`mem:` system metrics from the reference screenshot (never Done's).

## Sequencing
Two PRs, either order; `tui/fmt.py` lands with whichever ships first:
- **PR A — footer:** self-contained TUI, lower risk, fast win.
- **PR B — worker card:** the `env.emit_progress` bridge + Collector + widget;
  carries the contract-pinning test. Higher risk; its own adversarial review.
