# Persona C2c — HANDOFF (start here)

**Audience:** a fresh agent picking up C2c after a context compaction. This is a
self-contained brief — you should be able to start from `main` with only this doc,
the two specs it references, and the live code it cites. Read it top to bottom once,
then begin with the **Brainstorming** step in §8.

**Date written:** 2026-06-27 · **Base:** `main` @ `57f5d25` (C2b merged, PR #53).

---

## 1. What C2c is (one paragraph)

C2c is the **last piece of the persona-fleet C2 arc** and the **irreversible engine
fork**. It makes the agent **switch personas in-process** — one long-lived
`HarnessAgent` process serves **N persona sessions**, and switching routes to a
loaded session instead of restarting the process. Each persona's model resolves at
**session-start** (overriding base). Optionally (reduce scope — see §6) it also
shows live per-agent state in the rail (the literal mockup). It does NOT add persona
*creation* (Phase D) or crons (Phase E).

## 2. The single most important thing — DO NOT re-exec to switch

**C2b tried to switch personas by re-execing the agent with a new `--persona`. It
failed across THREE Codex review passes** — every fix leaked per-persona state (the
old persona's model / yolo / env leaking into the re-exec'd child; backend-vs-model
flag conflicts that re-broke each other). **Re-exec is the wrong primitive.**

**Research into the standard is conclusive and settles the architecture:**
- **OpenClaw** (the model this whole roadmap is ported from): one long-lived
  **Gateway** holds N stateful sessions (id + history), processes one message per
  session at a time via a Command Queue, and **ticks** sessions via heartbeats.
- **Hermes** (NousResearch): one long-running process; a **Dispatcher** loop ticks
  every N seconds; per-agent state persists across restarts.
- **OpenCode**: single-process, event-driven; **`Tab` cycles agents**; switching is
  in-process, no re-exec.
- **Codex #12047**: "session/model switching are **deterministic REPL ops handled
  WITHOUT invoking the agent**"; per-agent `config.toml` **overrides** base config.

**The universal rule: switch agents IN-PROCESS — route to an already-loaded session,
never restart; resolve each agent's model at session-start.** C2c follows this.
This is the analog of C1's "single-home model" decision: get it right once, with a
Codex review, before writing code.

## 3. The two architecture options (decide in the C2c brainstorm, with Codex review)

The maintainer deferred this fork to C2c's own spec. Both are legitimate; the
research leans toward the first.

**Option A — In-process N-sessions (research-recommended, smallest delta to today):**
- ONE `HarnessAgent` process. `new_session` takes a **persona/workspace argument**
  (today it hardcodes `self._workspace_dir` — see §4). `SessionStore` keys sessions
  by persona. The model factory resolves **per-session** (from `done.conf[persona]`).
- Switching = the TUI asks the agent to activate a different session (a new ACP
  `new_session` bound to the target persona, or a `harness/set_persona` ext-method —
  mirror `harness/set_model`/`harness/set_yolo` at `acp_agent.py:60-76`).
- Concurrency can start **cooperative** (one turn at a time, others idle) — true
  parallel agent loops are a later refinement, not required for switching.
- Reuses C1's per-session `workspace_dir` pipe + C2a's `FleetSnapshot` (N agents).

**Option B — N subprocesses (one `acp_main` per persona):**
- The TUI spawns one `acp_main` per persona (each already correctly single-persona),
  and multiplexes N ACP stdio streams + lifecycles into one rail.
- Heavier client (N pipes, N lifecycles, fan-in of N `FleetSnapshot`s); genuine
  parallelism; per-process startup/memory cost scales with N.
- (Claude Code historically supported in-process + tmux + iTerm spawn backends;
  OpenCode chose single-process. The research leans single-process/in-process.)

**Recommendation to carry into the brainstorm:** Option A. Smaller change, matches
every reference harness, reuses the C1/C2a plumbing, and keeps the engine simple.
But run the brainstorm + Codex review before locking it — it's irreversible.

## 4. The exact code seams (verified on `main` @ 57f5d25)

**THE BLOCKER (the half-built pipe C2c finishes):**
- `harness/acp_agent.py:119-121` — `new_session(cwd, ...)` calls
  `self._store.new(cwd=cwd, workspace_dir=self._workspace_dir)`. It ALWAYS passes the
  agent's single `self._workspace_dir`. **C2c makes the persona/workspace
  per-session** (a `new_session` arg, or a session→persona binding).
- `harness/acp_session.py:37` — `SessionStore.new(self, cwd, workspace_dir=None)`
  ALREADY accepts a per-session `workspace_dir`. `SessionState.workspace_dir`
  (acp_session.py:27) ALREADY exists (Phase B). So the store side is ready; the gap
  is `new_session` choosing the persona and the model factory resolving per-session.
- `harness/acp_agent.py:55` — `_persona_key()` = `self._workspace_dir.name` (the
  single persona). C2c needs a per-SESSION key (derive from `state.workspace_dir.name`).
- `harness/acp_agent.py:38,43` — `self._workspace_dir` and `self._store =
  SessionStore()` are the single-persona singletons to generalize.

**The model factory (per-session resolution):**
- `harness/acp_main.py:111-148` constructs `HarnessAgent` with a `model_factory` and a
  single resolved model. C2c must resolve the worker model **per session** from
  `done.conf[persona].model` (the precedence ladder lives in `harness/model_resolve.py`
  + `harness/tui_main.py:_resolve_model`; reuse it keyed per persona). NOTE: the
  per-persona model is single-homed in `done.conf [agents.<id>]` (C1 decision — do NOT
  reintroduce a second model home).

**The TUI switch trigger (where the user picks a persona):**
- `harness/tui/app.py:953` — `on_persona_selected(event)` is currently a **no-op**
  (C2b made the rail view-only). THIS is where C2c wires the switch: on selection,
  ask the agent to switch session/persona (via `new_session` or a `set_persona`
  ext-method), then update the active session client-side.
- The rail already lists personas + highlights the active one (`_persona_rows`,
  `_current_persona()`, `roster.persona_rows`). C2c just makes selection DO something.
- ACP ext-method pattern to mirror: `harness/acp_agent.py:60-76` (`set_model`,
  `set_yolo`). A `harness/set_persona` would fit the same shape.

**The TUI session machinery:**
- `harness/tui/app.py:175` `_new_session()` calls `self._conn.new_session(cwd=...)`.
  C2c's switch likely calls `new_session` with a persona, OR keeps per-persona
  sessions client-side and routes prompts to the active one.

## 5. KNOWN watch-for inherited from C2a (must fix in C2c)

`harness/tui/state.py:222-235` — the `reduce()` `PersonaResolved` case renames the
active agent's `id` **in place**: `replace(a, id=event.id, name=event.id)`. This is
correct at N=1, but in a **multi-agent tuple** it can produce **two agents sharing one
id** (reproduced: `[a,b]` active="a" + `PersonaResolved("b")` → `['b','b']`).
**C2c MUST restructure this** — key the reducer fold on a **stable, immutable
`agent_id`** rather than mutating `id`, or de-dup after the remap. This is a
first-class C2c task, not an afterthought; the multi-agent rail depends on it.

## 6. Scope reduction (the maintainer's explicit steer)

"Reduce complexity and scope." Start with the **proven core**:
1. In-process switching (route to a loaded session) + per-session model resolution.
2. The rail's selection actually switches (wire `on_persona_selected`).

**DEFER if they add complexity** (not required for a working switch):
- Live concurrent *ticking* of idle personas (OpenClaw heartbeats) — start with the
  active session ticking; others idle until selected.
- Live per-agent **state dots** in the rail (running/idle/scheduled) — nice, but the
  switch works without them. Add only if cheap on the chosen architecture.
- True parallel agent loops (multiple turns at once) — cooperative (one-at-a-time)
  is fine for v1.

A working "click a persona → it becomes active in the same process, with its own
model + session + memory" is the bar. Live fleet animation is gravy.

## 7. Guardrails (load-bearing constraints — do not violate)

- **No re-exec for switching** (§2). In-process routing only.
- **Model single-homed in `done.conf [agents.<id>]`** (C1). Resolve per-session at
  session-start; never add a second model home (persona.toml stays non-model:
  `read_skills`, `read_name` only).
- **No per-persona code BRANCH** (D3): `default` is just persona #0, threaded through
  general functions. No `if persona_id == "default":` in the routing/model path.
- **The no-op guarantee** (A/B/C1): no persona files + no `--persona` → engine-default
  model, zero injection, byte-identical. Don't break it.
- **Persona + memory resolve from `state.workspace_dir`** (Phase B invariant), now
  per-session. Both must agree on the session's workspace.
- **Work in a worktree, never the primary checkout** (AGENTS.md #1). Editable-install
  shadowing: run worktree pytest with the WORKTREE as cwd (verify with
  `import harness.X; print(X.__file__)` if a result surprises you).

## 8. The workflow to run (this project's proven loop)

1. **Brainstorm** (`superpowers:brainstorming`) — resolve the Option A vs B fork
   (§3) and the switch mechanism (`new_session`-with-persona vs `set_persona`
   ext-method). ONE genuine fork = one AskUserQuestion. Then write the C2c spec.
2. **Codex review the spec** (`codex:codex-rescue`, read-only, adversarial) BEFORE
   any code — the engine fork is irreversible; this is where the architecture gets
   pressure-tested. (Codex caught spec-level errors in C2a + the C2b switch — trust
   it; verify its findings against live code, it can false-positive.)
3. **Writing-plans** (`superpowers:writing-plans`) → TDD task plan.
4. **Subagent-driven build** (`superpowers:subagent-driven-development`): fresh
   implementer per task, task review (spec+quality) each, **Codex review on the
   engine-multiplexing tasks + the reducer-id fix** (§5), opus whole-branch review at
   the end.
5. **Finish + ship** (`superpowers:finishing-a-development-branch`; the maintainer
   runs `/ship` to auto-merge). Rebase onto moved `main`, re-verify the full suite,
   PR against `main`, merge on instruction (or `/ship`), prune.

## 9. References (read these — they have the full context)

- `docs/superpowers/specs/2026-06-27-persona-C2-drawer-arc-design.md` — the **arc
  spec**. Its C2c section now carries the research-grounded direction (§3 here is
  distilled from it) and the reducer-id watch-for.
- `docs/superpowers/specs/2026-06-27-persona-C2b-rail-design.md` — the C2b spec with
  the REVISION BANNER explaining why switching was removed + the research.
- `docs/superpowers/specs/2026-06-27-persona-C2a-indicator-design.md` — the persona
  seam (`persona_from_meta → PersonaResolved → FleetSnapshot`) C2c's rail reads.
- `docs/superpowers/specs/2026-06-27-persona-phaseC1-selection-design.md` — the
  per-persona model home (`done.conf [agents.<id>]`) + the precedence ladder C2c
  reuses per-session.
- GitHub **issue #29** — the multi-agent tracker; comment a C2c kickoff there.
- The C2 memory note: `~/.claude/.../memory/persona-C2-drawer.md` (background context;
  reflects what was true when written — verify against live code before acting).

## 10. Definition of done (C2c)

- One process serves N personas; selecting a persona in the rail (or `/persona <id>`)
  **switches in-process** to it — its own session, memory, and model — **without
  re-execing**. Switching back returns to that persona.
- Per-session model resolves from `done.conf[persona]` at session-start.
- The reducer-id watch-for (§5) is fixed: no duplicate ids at N>1.
- No re-exec; no second model home; no per-persona branch; no-op preserved.
- Own spec (Codex-reviewed) + plan + PR; full suite green.
- (Live fleet ticking/state-dots optional per §6.)
