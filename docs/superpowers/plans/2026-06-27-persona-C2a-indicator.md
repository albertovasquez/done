# Persona C2a — Indicator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show which persona the running TUI is on — a status-bar chip sourced from the engine's real resolved persona id, via a structured `persona` _meta event → `FleetSnapshot.active_id`.

**Architecture:** The agent emits a `persona` _meta chip (`{id: workspace_dir.name}`) once per session. The TUI parses it with a NEW pure `persona_from_meta` (mirroring the existing `decision_from_meta`), folds a NEW `PersonaResolved` reducer event that sets `FleetSnapshot.active_id` + remaps the active `AgentSnapshot`, and renders a dedicated `#statusbar-persona` chip. This reuses the proven `decision_from_meta → DecisionOpened → _apply` structured path — NOT `harness_chips` (which only makes transcript strings).

**Tech Stack:** Python 3.11+, Textual, pytest (+ pilot tests). No new dependencies.

## Global Constraints

- **Structured path, NOT harness_chips:** persona flows via `persona_from_meta → PersonaResolved → _apply()`, mirroring `decision_from_meta → DecisionOpened`. Routing it through `harness_chips` would append a transcript line and hit the empty-meta-chunk→RESPONDING wart — forbidden.
- **Engine-truthful:** the chip shows `state.workspace_dir.name` (the resolved persona = the key C1 uses for model/persona/memory), never an echo of `--persona`.
- **Always emits:** the `persona` chip fires once per session for EVERY persona including `default` — NOT gated on `injected`/`personalized` (unlike `persona_load`). It fires on every dispatch path (chat/agent/clarify/ambiguous).
- **`workspace_dir is None` → id `"default"`** (the `_persona_key` fallback). In the real `dn-agent` path the default resolves to a concrete dir, so None occurs only for `HarnessAgent(workspace_dir=None)` (tests/mock).
- **No session-history/model/router behavior change:** `_meta` updates are not written to `SessionStore.history`/`SessionState.transcript` (verified). NOT a byte-identical-wire claim (one extra `_meta` chunk).
- **`FleetSnapshot.active` is a `@property`** (state.py:79), not `active_agent()`.
- **`FleetUpdated` (messages.py:35) is DEAD** — do not post/handle it. The app refreshes presentation directly via `_apply()` + an explicit status refresh.
- **Test command (from worktree root):** `.venv/bin/python -m pytest tests/ -q` (or the absolute `/Users/alberto/Work/Quiubo/harness/.venv/bin/python` with the worktree as cwd). Full suite must stay green.
- **Commit trailer:** end every commit with `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

---

### Task 1: `persona_from_meta` parser + `PersonaResolved` event + `reduce` case

The pure TUI-side core: parse the chip, define the event, fold it into the snapshot. All in `harness/tui/state.py` (where `decision_from_meta`, the reducer events, and `reduce` already live).

**Files:**
- Modify: `harness/tui/state.py`
- Test: `tests/test_tui_state.py`

**Interfaces:**
- Consumes: existing `FleetSnapshot`, `AgentSnapshot`, `reduce()`, the `decision_from_meta` pattern.
- Produces:
  - `persona_from_meta(field_meta: dict | None) -> str | None`
  - `@dataclass(frozen=True) class PersonaResolved: id: str`
  - `reduce(snapshot, PersonaResolved(id))` → snapshot with `active_id == id` and the active `AgentSnapshot.id == name == id`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_tui_state.py`:

```python
from harness.tui.state import (
    persona_from_meta, PersonaResolved, reduce, initial_snapshot,
)


def test_persona_from_meta_reads_id():
    assert persona_from_meta({"harness": {"persona": {"id": "fred"}}}) == "fred"

def test_persona_from_meta_tolerant_of_garbage():
    assert persona_from_meta(None) is None
    assert persona_from_meta({}) is None
    assert persona_from_meta({"harness": "nope"}) is None
    assert persona_from_meta({"harness": {"persona": {}}}) is None          # no id
    assert persona_from_meta({"harness": {"persona": {"id": 5}}}) is None   # non-str id

def test_reduce_persona_sets_active_id_and_remaps_agent():
    snap = initial_snapshot()                 # active_id="default", agent ("default","agent")
    out = reduce(snap, PersonaResolved("fred"))
    assert out.active_id == "fred"
    assert out.active is not None
    assert out.active.id == "fred"
    assert out.active.name == "fred"

def test_reduce_persona_idempotent():
    snap = reduce(initial_snapshot(), PersonaResolved("fred"))
    again = reduce(snap, PersonaResolved("fred"))
    assert again.active_id == "fred"
    assert again.active.id == "fred"
    assert len(again.agents) == 1             # no duplicate agent added
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_tui_state.py::test_persona_from_meta_reads_id -v`
Expected: FAIL with `ImportError: cannot import name 'persona_from_meta'`.

- [ ] **Step 3: Write the implementation**

In `harness/tui/state.py`, add the event next to `DecisionOpened` (after state.py:139):

```python
@dataclass(frozen=True)
class PersonaResolved:
    id: str
```

Add the parser next to `decision_from_meta` (mirror its tolerant shape):

```python
def persona_from_meta(field_meta: dict | None) -> str | None:
    """Recognize the active persona id from the harness meta chip.
    Tolerant: any missing/malformed shape yields None, never raises."""
    if not isinstance(field_meta, dict):
        return None
    harness = field_meta.get("harness")
    if not isinstance(harness, dict):
        return None
    persona = harness.get("persona")
    if not isinstance(persona, dict):
        return None
    pid = persona.get("id")
    return pid if isinstance(pid, str) and pid else None
```

Handle the event at the TOP LEVEL of `reduce()` (state.py:214-221) — it must change
`active_id` and the agent's identity, which `_reduce_agent` (per-active-agent) cannot.
Replace the `reduce` function body with:

```python
def reduce(snapshot: FleetSnapshot, event) -> FleetSnapshot:
    """Pure: fold one event into the snapshot, updating the ACTIVE agent only
    (single-agent today; fleet fan-out later targets event.agent_id)."""
    if isinstance(event, PersonaResolved):
        # Set the active persona id and rename the (single) active agent to it.
        # C2a is single-agent: remap the active snapshot's id+name to the persona.
        # (C2b reads active_id to highlight; C2c grows the tuple per real session.)
        agents = tuple(
            replace(a, id=event.id, name=event.id) if a.id == snapshot.active_id else a
            for a in snapshot.agents
        )
        return FleetSnapshot(agents=agents, active_id=event.id)
    agents = tuple(
        _reduce_agent(a, event) if a.id == snapshot.active_id else a
        for a in snapshot.agents
    )
    return FleetSnapshot(agents=agents, active_id=snapshot.active_id)
```

Confirm `replace` is already imported at the top of state.py (it is — `_reduce_agent` uses it). If not, add `from dataclasses import replace`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tui_state.py -q`
Expected: PASS — the 4 new tests AND all pre-existing `test_tui_state.py` tests (the non-persona `reduce` path is unchanged).

- [ ] **Step 5: Commit**

```bash
git add harness/tui/state.py tests/test_tui_state.py
git commit -m "feat(tui): persona_from_meta + PersonaResolved reducer event (the persona seam)

Mirrors decision_from_meta; reduce sets active_id + remaps the active agent.
The structured path C2b/c reuse — NOT harness_chips.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Engine emits the `persona` chip once per session

The agent reports its resolved persona id over ACP, after `task_classified`, before the clarify-return, ungated.

**Files:**
- Modify: `harness/acp_session.py` (add the `persona_emitted` flag)
- Modify: `harness/acp_agent.py` (emit the chip in `prompt()`)
- Test: `tests/test_acp_session_context.py` (THE prompt-driving harness — NOT test_acp_agent.py, which only covers ext_method)

**Interfaces:**
- Consumes: existing `with_meta`, `message_chunk`, `state.workspace_dir`, the `task_classified` emit site (acp_agent.py:195-196). Test harness: `_FakeConn` (records `session_update` calls in a list), `_ScriptedRouter`, `_build(router, worker_model_id=None)`, `_prompt(agent, sid, text)`, `_chat()`/`_clarify`/`_ambiguous` classification factories — all already in `tests/test_acp_session_context.py`.
- Produces: a `session/update` with `field_meta={"harness": {"persona": {"id": <id>}}}`, once per session.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_acp_session_context.py`, reusing its existing `_FakeConn`/`_build`/`_prompt`/`_chat` helpers (read the file's top for their exact signatures and how `_FakeConn` records updates — it appends `(session_id, update, kw)` to a list; extract `field_meta` from each recorded `update` the same way the existing emit assertions do). `_build` accepts `workspace_dir` via the agent it constructs — check how `_build` calls `build_harness_agent` and pass a workspace whose `.name` is `"fred"` (e.g. a `tmp_path/"agents"/"fred"` dir), or `None` for the default test.

```python
def _persona_ids(conn):
    """The persona ids emitted on conn during the turn(s)."""
    out = []
    for rec in conn.updates:                 # adapt to _FakeConn's actual record shape
        fm = getattr(rec_update(rec), "field_meta", None)
        h = fm.get("harness") if isinstance(fm, dict) else None
        p = h.get("persona") if isinstance(h, dict) else None
        if isinstance(p, dict) and isinstance(p.get("id"), str):
            out.append(p["id"])
    return out


def test_emits_persona_chip_once_with_resolved_id(tmp_path):
    ws = tmp_path / "agents" / "fred"; ws.mkdir(parents=True)
    conn, agent, sid = _build_with_workspace(ws)   # build an agent whose workspace is fred
    _prompt(agent, sid, "what is X")
    assert _persona_ids(conn) == ["fred"]


def test_persona_chip_not_re_emitted_second_turn(tmp_path):
    ws = tmp_path / "agents" / "fred"; ws.mkdir(parents=True)
    conn, agent, sid = _build_with_workspace(ws)
    _prompt(agent, sid, "first")
    _prompt(agent, sid, "second")
    assert _persona_ids(conn) == ["fred"]          # once across both turns


def test_persona_chip_defaults_when_no_workspace():
    conn, agent, sid = _build_with_workspace(None)
    _prompt(agent, sid, "hi")
    assert _persona_ids(conn) == ["default"]


def test_persona_chip_fires_on_clarify_path(tmp_path):
    ws = tmp_path / "agents" / "fred"; ws.mkdir(parents=True)
    conn, agent, sid = _build_with_workspace(ws, router=_ScriptedRouter([_ambiguous()]))
    _prompt(agent, sid, "huh")
    assert _persona_ids(conn) == ["fred"]          # identity shows even on clarify
```

Implementation note for the test author: the file may not already expose a
`_build_with_workspace(ws, router=...)` helper. If `_build` doesn't accept a workspace,
add a thin local helper in the test that constructs the agent via `build_harness_agent`
with the desired `workspace_dir` + a `_FakeConn`, mirroring `_build`'s body. Reuse
`_FakeConn`, `_ScriptedRouter`, `_chat`/`_ambiguous`, and `_prompt` verbatim — do NOT
build a new connection/router harness. `rec_update(rec)` stands for however `_FakeConn`
stores the update (e.g. `rec[1]` or `rec.update`) — match the file.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_acp_session_context.py -k persona -v`
Expected: FAIL — no `persona` chip is emitted yet (`_persona_ids(conn)` is empty).

- [ ] **Step 3: Write the implementation**

In `harness/acp_session.py`, add the flag to `SessionState` (next to `persona_load_emitted`):

```python
    persona_emitted: bool = False
```

In `harness/acp_agent.py`, in `prompt()`, immediately AFTER the `task_classified`
emit (acp_agent.py:195-196) and BEFORE the `persona_load` block (acp_agent.py:198),
add:

```python
        # Active-persona identity chip (C2a): the persona the agent ACTUALLY resolved.
        # Unlike persona_load, this is NOT gated on injected/personalized — an identity
        # indicator must show for EVERY session (incl. default) and on every dispatch
        # path. Once per session.
        if not state.persona_emitted:
            pid = state.workspace_dir.name if state.workspace_dir else "default"
            await self._conn.session_update(session_id,
                with_meta(message_chunk(""), {"persona": {"id": pid}}))
            state.persona_emitted = True
```

Placement note: this is on the common path (before the `if cls.needs_clarification or
cls.task_type == "ambiguous": ... return` at acp_agent.py:219-228), so it fires on the
clarify/ambiguous path too — as required.

- [ ] **Step 4: Run tests to verify they pass + clarify-path tests still green**

Run: `.venv/bin/python -m pytest tests/test_acp_session_context.py -q`
Expected: PASS — the 4 new tests AND the existing turn tests. CHECK
`test_clarify_turn_writes_only_user_turn` specifically: it asserts the clarify path
writes only the user turn to the SESSION RECORD — that should still hold (the persona
chip is a `_meta` wire update, NOT a session-record write, per Task 4's guarantee). If
any existing test asserted an EXACT count/list of `_FakeConn` session_update calls, it
will now see the extra persona chunk — update that expectation to include it
(intentional per spec; annotate). Do NOT weaken an assertion to hide the new emit.

- [ ] **Step 5: Commit**

```bash
git add harness/acp_session.py harness/acp_agent.py tests/test_acp_session_context.py
git commit -m "feat(agent): emit a persona identity chip once per session (C2a)

Reports the resolved workspace_dir.name after task_classified, ungated, on every
dispatch path. The engine-truthful source for the TUI persona indicator.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Wire the parse into `on_session_update` + render the `#statusbar-persona` chip

Connect the engine emit to the reducer in the app, and show the result in the status bar.

**Files:**
- Modify: `harness/tui/app.py` (parse-call in `on_session_update`; mount + refresh the chip)
- Modify: `harness/tui/app.tcss` (style the persona chip — minimal)
- Test: `tests/test_tui_pilot.py`

**Interfaces:**
- Consumes: `persona_from_meta`, `PersonaResolved` (Task 1); the engine emit (Task 2); the existing `_apply()` (app.py:335-337), `_mount_status_contents` (app.py:220-226), `_refresh_status` (app.py:243-247), `self._snapshot`.
- Produces: a `#statusbar-persona` Static reflecting `self._snapshot.active`.

- [ ] **Step 1: Write the failing pilot test**

Add to `tests/test_tui_pilot.py` (mirror the existing pilot tests' app-bootstrap +
fake-update pattern — read the file first for the exact helper). Drive a fake
`session/update` carrying the persona chip and assert the status bar shows it:

```python
async def test_status_bar_shows_persona_after_chip():
    # Boot the app (existing pilot harness), then deliver a session/update whose
    # field_meta = {"harness": {"persona": {"id": "fred"}}}.
    # Assert the #statusbar-persona Static renders "fred".
    async with app_pilot() as (app, pilot):
        await deliver_meta(app, {"harness": {"persona": {"id": "fred"}}})
        await pilot.pause()
        persona = app.query_one("#statusbar-persona", Static)
        assert "fred" in persona.renderable_str()   # use the file's existing way to read a Static's text
```

Use the test file's established idiom for (a) booting the pilot app, (b) delivering a
`SessionUpdate`, and (c) reading a widget's rendered text. Do not invent new helpers if
the file already has them.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tui_pilot.py -k persona -v`
Expected: FAIL — no `#statusbar-persona` widget exists yet (`NoMatches`).

- [ ] **Step 3: Implement the parse-call + the chip**

(a) In `harness/tui/app.py` `on_session_update`, beside the existing
`decision_from_meta` call (app.py:777-779), add the persona parse + apply. Import
`persona_from_meta, PersonaResolved` from `harness.tui.state` (extend the existing
import at app.py:37-38):

```python
        pid = persona_from_meta(getattr(msg.update, "field_meta", None))
        if pid:
            self._apply(PersonaResolved(pid))
            self._persona_seen = True
            self._refresh_persona()
```

Do NOT route persona through `harness_chips` (that appends a transcript line).
(The `_persona_seen` flag is explained in part (c).)

(b) Add a `#statusbar-persona` Static in `_mount_status_contents` (app.py:220-226),
after the mode chip, before `statusbar-left`:

```python
        await bar.mount(Static(self._status_persona(), id="statusbar-persona", markup=True))
```

(c) Add the helpers near `_refresh_status` (app.py:243-247). The chip stays hidden
until a REAL persona chip lands — `initial_snapshot()` seeds a bootstrap agent
(`id="default"`, `name="agent"`), and we must not show `persona: default` from that
bootstrap alone, only after an actual `PersonaResolved`. Gate on a `_persona_seen` flag:

```python
    def _status_persona(self) -> str:
        if not getattr(self, "_persona_seen", False):
            return ""                          # LANDING / pre-first-turn: no claim
        a = self._snapshot.active
        return f"[$muted]persona: {a.id}[/]" if a is not None else ""

    def _refresh_persona(self) -> None:
        try:
            self.query_one("#statusbar-persona", Static).update(self._status_persona())
        except Exception:
            pass
```

Initialize `self._persona_seen = False` in `__init__` alongside the other status state
(near where `self._tokens` / `self._started` are initialized). In the parse-call block
(part (a)), set `self._persona_seen = True` inside the `if pid:` branch, right before
`self._refresh_persona()`. So the final part-(a) block is:

```python
        pid = persona_from_meta(getattr(msg.update, "field_meta", None))
        if pid:
            self._apply(PersonaResolved(pid))
            self._persona_seen = True
            self._refresh_persona()
```

(d) In `harness/tui/app.tcss`, add a minimal rule next to the other `#statusbar-*`
rules so the chip lays out inline (match the existing statusbar item style):

```css
#statusbar-persona { width: auto; color: $muted; padding: 0 2 0 0; }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tui_pilot.py -q`
Expected: PASS — the new persona pilot test AND all existing pilot tests (the status
bar gains one Static; nothing else changes).

- [ ] **Step 5: Commit**

```bash
git add harness/tui/app.py harness/tui/app.tcss tests/test_tui_pilot.py
git commit -m "feat(tui): #statusbar-persona chip wired from PersonaResolved (C2a)

on_session_update parses persona_from_meta → _apply(PersonaResolved) → refresh a
dedicated status-bar chip. Hidden until the first real persona chip lands.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: End-to-end truth lock + full-suite regression

Prove the chip equals the engine's resolved id end-to-end, that the emit writes nothing to session history/transcript, and that the whole suite is green.

**Files:**
- Test: `tests/test_persona_C2a_indicator.py` (NEW)

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Write the truth-lock tests**

Create `tests/test_persona_C2a_indicator.py`:

```python
from harness.tui.state import persona_from_meta, PersonaResolved, reduce, initial_snapshot


def test_resolved_id_round_trips_to_active():
    # The id the engine would emit (workspace_dir.name) round-trips through the
    # parser + reducer to FleetSnapshot.active — the truth invariant.
    meta = {"harness": {"persona": {"id": "fred"}}}
    pid = persona_from_meta(meta)
    snap = reduce(initial_snapshot(), PersonaResolved(pid))
    assert snap.active.id == "fred"            # chip would show exactly the resolved id


```

The second guarantee (the persona emit is wire-only, not session-recorded) is best
tested in `tests/test_acp_session_context.py` where the session store is reachable —
add it there, reusing the Task 2 harness and the way `test_clarify_turn_writes_only_user_turn`
inspects the recorded turns:

```python
def test_persona_chip_not_written_to_session_record(tmp_path):
    ws = tmp_path / "agents" / "fred"; ws.mkdir(parents=True)
    conn, agent, sid = _build_with_workspace(ws)
    _prompt(agent, sid, "what is X")
    # The persona id reached the WIRE (conn) ...
    assert "fred" in _persona_ids(conn)
    # ... but the _meta chip is NOT in the session record (history/transcript).
    recorded = repr(agent._store.get(sid).transcript) + repr(agent._store.get(sid).history)
    assert "persona" not in recorded            # _meta is wire-only
```

Match the file's actual accessor for the session state (it uses `agent._store` /
`store.get(sid)` — confirm the exact attribute names when you read the file; adjust if
the store is exposed differently).

- [ ] **Step 2: Run it**

Run: `.venv/bin/python -m pytest tests/test_persona_C2a_indicator.py -q`
Expected: PASS (2 tests).

- [ ] **Step 3: Run the FULL suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS — all pre-existing tests plus the C2a additions, zero regressions. If a
pre-existing test fails, STOP and fix the regression; only the clarify-path emit
expectation (Task 2) was intentionally changed.

- [ ] **Step 4: Commit**

```bash
git add tests/test_persona_C2a_indicator.py
git commit -m "test(persona): C2a end-to-end truth lock + history-isolation (full suite green)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Notes for the executor

- **Codex review points (per the user's 'codex reviews where needed'):** request a Codex review after **Task 1** (the reducer/parser — the seam C2b/c reuse; a wrong shape here propagates) and after **Task 2** (the engine emit — placement + the no-history guarantee). Tasks 3–4 are TUI wiring + locks; standard review suffices.
- **Read before you write:** Tasks 2–4 reuse existing test harnesses (`test_acp_agent.py`'s emit-capture, `test_tui_pilot.py`'s app-bootstrap, the `SessionState.transcript`/`SessionStore.history` inspection idiom). Read each file first and reuse its pattern — do not invent parallel harnesses.
- **The forbidden shortcut:** never route the persona through `harness_chips` to "save a step." That appends a transcript line and triggers the empty-meta-chunk→RESPONDING wart. The structured `persona_from_meta → PersonaResolved → _apply` path is mandatory (Global Constraints).
- **Order:** Task 1 (pure core) → Task 2 (engine emit) → Task 3 (app wiring, needs 1+2) → Task 4 (locks, needs all). Execute in order.
