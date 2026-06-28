# Structured Clarification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the router finds a request ambiguous, surface 2–4 concrete candidate interpretations as a clickable `DecisionPrompt`; picking one submits its title as the next user turn.

**Architecture:** Additive change across one flow. (1) Router's cheap-model JSON contract gains an `options` array → `Classification.options`. (2) The ACP clarify emit attaches `field_meta.harness.decision = {question, options}` (only when options present). (3) The TUI — which already folds that meta into `state.decision` — mounts the existing `DecisionPrompt` widget and handles its `Selected` message by submitting the chosen option's title through the same path a typed prompt uses.

**Tech Stack:** Python 3.11, pytest, Textual (TUI), litellm (router cheap model). Vendored mini-swe-agent engine (`upstream/`, never edited).

**Spec:** `docs/superpowers/specs/2026-06-27-structured-clarification-design.md` (issue #66).

## Global Constraints

- Always work in the git worktree, never on `main` (AGENTS.md #1). This plan runs on branch `worktree-structured-clarification`.
- **Zero edits under `upstream/`** (AGENTS.md #4).
- Run tests from the worktree root with the worktree as cwd: `.venv/bin/python -m pytest tests/ -q` (the primary checkout's venv; tests `sys.path.insert(0, ".")` so the worktree's `harness/` is imported — verified, no editable-install shadowing).
- Commit-message trailer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- **The additive-safety invariant:** when `options == []` every path must be byte-identical to today — the flat `clarifying_question` string is always set; the clarify emit with no options sends a plain chunk with no `decision` meta.
- Match surrounding style (AGENTS.md #5): the tolerant router parse mirrors the existing `skills`/`confidence` parsing; the emit mirrors the persona_load/memory_load `with_meta` emits at `acp_agent.py:319-331`.

---

### Task 1: Router emits structured `options`

**Files:**
- Modify: `harness/router.py:32-41` (`Classification` — add `options`), `:58-75` (`_system_prompt` — document the `options` key), `:125-144` (`classify` — tolerant parse + attach)
- Test: `tests/test_router.py` (add)

**Interfaces:**
- Consumes: nothing new.
- Produces: `Classification.options: list[tuple[str, str]]` (each `(title, rationale)`), default `[]`. Populated only when the cheap model returns a well-formed `options` array; malformed/absent → `[]`. Task 2 reads `cls.options`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_router.py  (add; reuse the module's _stub helper)
def test_classify_parses_options_array():
    payload = ('{"task_type": "ambiguous", "confidence": 0.2, "reasoning": "vague", '
               '"options": [{"title": "Explain how auth works", "rationale": "read the code"}, '
               '{"title": "Fix the auth bug", "rationale": "repair the failing check"}]}')
    cls = Router(_stub(payload), catalog=_CATALOG).classify("do the auth thing")
    assert cls.needs_clarification
    assert cls.options == [("Explain how auth works", "read the code"),
                           ("Fix the auth bug", "repair the failing check")]


def test_classify_options_absent_degrades_to_empty_and_keeps_question():
    payload = '{"task_type": "ambiguous", "confidence": 0.1, "reasoning": "unclear"}'
    cls = Router(_stub(payload), catalog=_CATALOG).classify("hmm")
    assert cls.options == []
    assert cls.clarifying_question                      # flat question still set


def test_classify_malformed_options_filtered_no_raise():
    # scalar options, an entry missing title, and a non-dict entry — all dropped
    payload = ('{"task_type": "ambiguous", "confidence": 0.1, "reasoning": "x", '
               '"options": [{"rationale": "no title"}, "junk", {"title": "Keep me", "rationale": "ok"}]}')
    cls = Router(_stub(payload), catalog=_CATALOG).classify("hmm")
    assert cls.options == [("Keep me", "ok")]


def test_classify_clear_request_has_no_options():
    payload = '{"task_type": "code_fix", "skills": [], "confidence": 0.95, "reasoning": "clear"}'
    cls = Router(_stub(payload), catalog=_CATALOG).classify("fix the add() bug")
    assert cls.options == []
    assert not cls.needs_clarification
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_router.py -k options -q`
Expected: FAIL — `Classification` has no `options` attribute / parse not implemented.

- [ ] **Step 3: Write minimal implementation**

In `harness/router.py`, add the field to `Classification` (after `clarifying_question`):

```python
    clarifying_question: str | None = None
    options: list[tuple[str, str]] = field(default_factory=list)  # (title, rationale)
```

In `_system_prompt`, extend the JSON-keys sentence (after the `reasoning` key) to document options:

```python
        "reasoning (one short sentence). When the request is ambiguous or "
        "low-confidence, ALSO return options: a list of 2-4 objects {title, "
        "rationale}, each a concrete interpretation the agent could act on "
        "(title = the rephrased task, rationale = one short why). Omit options "
        "or use [] when the request is clear.\n\n"
```

> The existing string ends `"reasoning (one short sentence).\n\nSkill catalog..."`.
> Splice the new sentences between `reasoning (one short sentence).` and the
> `\n\nSkill catalog` part — keep the catalog join intact.

In `classify`, after `reasoning`/`suggested` are parsed (around line 135-136) and
before the `needs`/`question` computation, parse options tolerantly:

```python
        raw_opts = data.get("options")
        raw_opts = raw_opts if isinstance(raw_opts, list) else []   # scalar/str isn't a list
        options = [(str(o["title"]), str(o.get("rationale", "")))
                   for o in raw_opts
                   if isinstance(o, dict) and o.get("title")]
```

Add `options=options` to the final `Classification(...)` return. (The early
unparseable-JSON return at lines 120-124 keeps its default `options=[]` — leave it
untouched.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_router.py -q`
Expected: PASS (new + existing router tests).

- [ ] **Step 5: Commit**

```bash
git add harness/router.py tests/test_router.py
git commit -m "feat(router): emit structured clarification options (degrade to none)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: ACP clarify emit attaches the `decision` meta

**Files:**
- Modify: `harness/acp_agent.py:333-343` (the clarify branch)
- Test: `tests/test_acp_clarify_decision.py` (create)

**Interfaces:**
- Consumes: `Classification.options` (Task 1); `with_meta` / `message_chunk` from `harness/acp_emit.py`.
- Produces: when `cls.options` is non-empty, the clarify `session_update` chunk carries `field_meta == {"harness": {"decision": {"question": q, "options": [{"title","rationale"}, ...]}}}`. When empty, the chunk has no `decision` meta (today's behavior).

> NOTE: `with_meta(update, m)` sets `update.field_meta = {**existing, "harness": m}`
> (acp_emit.py:41-44). So `with_meta(chunk, {"decision": {...}})` lands at
> `field_meta["harness"]["decision"]` — exactly what `decision_from_meta` reads.
> Confirm `with_meta` and `message_chunk` are already imported in `acp_agent.py`
> (the persona/memory emits at :319-331 use `with_meta` + `message_chunk`, so they are).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_acp_clarify_decision.py
import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

from harness.acp_emit import message_chunk, with_meta


def _decision_chunk(question, options):
    """Mirror the production emit so the test pins the exact shape the TUI parses."""
    chunk = message_chunk(question)
    if options:
        chunk = with_meta(chunk, {"decision": {
            "question": question,
            "options": [{"title": t, "rationale": r} for t, r in options]}})
    return chunk


def test_decision_meta_shape_round_trips_through_parser():
    from harness.tui.state import decision_from_meta
    chunk = _decision_chunk("Which did you mean?", [("Explain", "read"), ("Fix", "repair")])
    dv = decision_from_meta(chunk.field_meta)
    assert dv is not None
    assert dv.question == "Which did you mean?"
    assert dv.options == (("Explain", "read"), ("Fix", "repair"))


def test_empty_options_attaches_no_decision_meta():
    chunk = _decision_chunk("Clarify please", [])
    # byte-identical-to-today guard: no harness.decision meta when there are no options
    assert (chunk.field_meta or {}).get("harness", {}).get("decision") is None
```

> This test pins the SHAPE (the contract between emit and parser). Step 3 makes the
> production code at `acp_agent.py` build the chunk this exact way. A heavier
> end-to-end ACP test is unnecessary — the shape is the seam.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_acp_clarify_decision.py -q`
Expected: PASS for the helper-shape tests (they exercise `with_meta`/`decision_from_meta`, which already exist). If both pass, the SHAPE is proven; the failing part is wiring it into `acp_agent.py` — verify that next.

- [ ] **Step 3: Write minimal implementation**

In `harness/acp_agent.py`, replace the clarify emit (the current
`await self._conn.session_update(session_id, message_chunk(q))` inside the
`if cls.needs_clarification or cls.task_type == "ambiguous":` block at :333-335):

```python
        if cls.needs_clarification or cls.task_type == "ambiguous":
            q = cls.clarifying_question or "Could you clarify the task?"
            chunk = message_chunk(q)
            if cls.options:
                chunk = with_meta(chunk, {"decision": {
                    "question": q,
                    "options": [{"title": t, "rationale": r} for t, r in cls.options]}})
            await self._conn.session_update(session_id, chunk)
            self._store.record(session_id, {"prompt": text, "stop_reason": "end_turn",
                                            "kind": "clarify"})
            # ... rest unchanged (store.extend user turn, trace, return end_turn)
```

> Leave the `_store.record`, `_store.extend`, `_trace`, and `return
> acp.PromptResponse(stop_reason="end_turn")` lines exactly as they are.
> Ensure `with_meta` is imported at the top of `acp_agent.py` (grep; add to the
> `from harness.acp_emit import ...` line if absent).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_acp_clarify_decision.py -q`
Expected: PASS. Then ACP regression: `.venv/bin/python -m pytest tests/ -k acp -q`.

- [ ] **Step 5: Commit**

```bash
git add harness/acp_agent.py tests/test_acp_clarify_decision.py
git commit -m "feat(acp): attach decision meta to clarify chunk when options present

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: TUI — extract `_submit_text` (DRY the turn-start path)

**Files:**
- Modify: `harness/tui/app.py:468-487` (`on_prompt_area_submitted` — extract body into `_submit_text`)
- Test: `tests/test_tui_pilot.py` (add a pilot asserting `_submit_text` drives a turn)

**Interfaces:**
- Consumes: nothing new.
- Produces: `async def _submit_text(self, text: str) -> None` on `HarnessTui` — runs the existing turn-start sequence (add user message, clear+disable input, `TurnStarted`, spawn `_send_prompt` worker). `on_prompt_area_submitted` calls it after its slash/guard checks. Task 4's decision handler also calls it.

> WHY extract: Task 4 must submit a chosen option exactly like a typed prompt
> (same user-message + turn-start path). Duplicating lines 479-487 would drift;
> one helper keeps them identical (DRY).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tui_pilot.py  (add; reuse the file's app/pilot harness + FAKE_CMD)
import pytest


@pytest.mark.asyncio
async def test_submit_text_starts_a_turn(monkeypatch):
    # Reuse the module's app construction. Assert _submit_text routes through the
    # same path as a typed prompt: a user message lands in the transcript.
    app = HarnessTui(agent_cmd=FAKE_CMD)            # match how other pilots build it
    async with app.run_test() as pilot:
        await _send_first_prompt(pilot, app, "hello")   # establishes conversation state
        await app._submit_text("chosen option title")
        await pilot.pause()
        assert "chosen option title" in _transcript_text(app)
```

> NOTE: match the EXACT `HarnessTui(...)` constructor args the other pilots in this
> file use (grep `HarnessTui(` in `tests/test_tui_pilot.py`); the snippet's
> `agent_cmd=FAKE_CMD` is illustrative. Reuse `_send_first_prompt`/`_transcript_text`
> already defined in the file.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tui_pilot.py -k submit_text -q`
Expected: FAIL — `HarnessTui` has no `_submit_text`.

- [ ] **Step 3: Write minimal implementation**

In `harness/tui/app.py`, extract lines 479-487 of `on_prompt_area_submitted` into a
helper, and call it:

```python
    async def on_prompt_area_submitted(self, event: PromptArea.Submitted) -> None:
        text = event.text.strip()
        if text.startswith("/"):
            await self._run_slash(text)
            return
        if not text or self._conn is None or self._busy:
            return
        if not self._started:
            await self._enter_conversation()
        await self._submit_text(text)

    async def _submit_text(self, text: str) -> None:
        """Start a user turn for `text` — the shared path for a typed prompt AND a
        decision selection. Mirrors the original on_prompt_area_submitted tail."""
        self._add_user_message(text)
        inp = self._active_input()
        inp.value = ""
        inp.disabled = True
        self._turn_start = time.monotonic()
        self._turn_active = True
        self._apply(TurnStarted())
        self._send_gen = self._gen
        self.run_worker(self._send_prompt(text), thread=False)
```

> `_submit_text` assumes conversation state is established (input exists). The
> caller handles `_enter_conversation()` first — preserve that ordering. Task 4's
> caller runs while a conversation is already active (a clarify turn just ended),
> so it is safe there too.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tui_pilot.py -k submit_text -q`
Expected: PASS. Then the pilot suite: `.venv/bin/python -m pytest tests/test_tui_pilot.py -q`.

- [ ] **Step 5: Commit**

```bash
git add harness/tui/app.py tests/test_tui_pilot.py
git commit -m "refactor(tui): extract _submit_text (shared typed-prompt/decision path)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: TUI — mount `DecisionPrompt` + handle `Selected`

**Files:**
- Modify: `harness/tui/app.py:39` (imports), `:886-889` (the decision fold — mount the widget), and add `on_decision_prompt_selected`
- Test: `tests/test_tui_pilot.py` (add pilots for mount + the three selection paths)

**Interfaces:**
- Consumes: `_submit_text` (Task 3); `DecisionPrompt`, `DecisionPrompt.Selected`, `TYPE_SOMETHING`, `CHAT_ABOUT_IT`, `DecisionView`; `_mount_in_transcript` (app.py:717); `state.decision` (the snapshot).
- Produces: when `decision_from_meta` yields a view, a `DecisionPrompt` is mounted in the transcript. `on_decision_prompt_selected(msg)` submits the chosen option's title (option index), focuses the composer (`TYPE_SOMETHING`), or prefills a chat-routable prefix (`CHAT_ABOUT_IT`); then unmounts the prompt.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tui_pilot.py  (add)
from harness.tui.widgets.decision_prompt import DecisionPrompt, TYPE_SOMETHING, CHAT_ABOUT_IT
from harness.tui.state import DecisionView


def _decision_meta(question, options):
    return {"harness": {"decision": {
        "question": question,
        "options": [{"title": t, "rationale": r} for t, r in options]}}}


@pytest.mark.asyncio
async def test_decision_meta_mounts_prompt(monkeypatch):
    app = HarnessTui(agent_cmd=FAKE_CMD)
    async with app.run_test() as pilot:
        await _send_first_prompt(pilot, app, "hi")
        app._handle_update(NS(field_meta=_decision_meta("Which?", [("Explain", "r1"), ("Fix", "r2")])))
        await pilot.pause()
        assert app.query("#decision-prompt")            # widget mounted


@pytest.mark.asyncio
async def test_selecting_option_submits_its_title(monkeypatch):
    app = HarnessTui(agent_cmd=FAKE_CMD)
    submitted = []
    async with app.run_test() as pilot:
        await _send_first_prompt(pilot, app, "hi")
        monkeypatch.setattr(app, "_submit_text", lambda t: submitted.append(t) or _noop())
        app._handle_update(NS(field_meta=_decision_meta("Which?", [("Explain it", "r1"), ("Fix it", "r2")])))
        await pilot.pause()
        app.on_decision_prompt_selected(DecisionPrompt.Selected(1))   # pick "Fix it"
        await pilot.pause()
        assert submitted == ["Fix it"]
        assert not app.query("#decision-prompt")        # unmounted after selection
```

> `_handle_update` is the method that folds `field_meta` (it wraps the logic at
> app.py:882-898 — find its real name by grepping the method that calls
> `decision_from_meta`; the snippet calls it `_handle_update`). `_noop()` is a
> trivial awaitable: `async def _noop(): return None` defined in the test module.
> Match the real method name and the real `HarnessTui(...)` ctor args.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tui_pilot.py -k decision -q`
Expected: FAIL — no widget mounts (the fold sets `state.decision` but nothing mounts), and `on_decision_prompt_selected` does not exist.

- [ ] **Step 3: Write minimal implementation**

Add imports at `harness/tui/app.py` (near the existing `decision_from_meta` import
at :39, and the widget imports):

```python
from harness.tui.widgets.decision_prompt import DecisionPrompt, TYPE_SOMETHING, CHAT_ABOUT_IT
```

At the decision fold (where `dv = decision_from_meta(...)` then
`self._apply(DecisionOpened(dv))`, ~:887-889), mount the widget after applying:

```python
        dv = decision_from_meta(getattr(msg.update, "field_meta", None))
        if dv is not None:
            self._apply(DecisionOpened(dv))
            if not self.query("#decision-prompt"):
                self._mount_in_transcript(DecisionPrompt(dv))
```

Add the selection handler (anywhere among the app's message handlers):

```python
    def on_decision_prompt_selected(self, msg: DecisionPrompt.Selected) -> None:
        view = self._snapshot.active.decision if self._snapshot.active else None
        if msg.index == TYPE_SOMETHING:
            self._active_input().focus()
        elif msg.index == CHAT_ABOUT_IT:
            inp = self._active_input()
            inp.value = "Let's discuss: "
            inp.focus()
        elif view is not None and 0 <= msg.index < len(view.options):
            self.run_worker(self._submit_text(view.options[msg.index][0]), thread=False)
        self._dismiss_decision()

    def _dismiss_decision(self) -> None:
        for w in self.query("#decision-prompt"):
            w.remove()
        if self._snapshot.active and self._snapshot.active.decision is not None:
            self._apply(DecisionOpened(None))   # clear state.decision
```

> `DecisionOpened(None)` must clear the decision — confirm the reducer accepts a
> None view (state.py:186-187 sets `decision=event.view`, so `DecisionOpened(None)`
> sets it to None; verify, and if `DecisionOpened` is frozen with a non-optional
> `view`, widen its type to `DecisionView | None`). The read of the live view uses
> `self._snapshot.active.decision` (the snapshot the reducer maintains) — do not
> thread separate state.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tui_pilot.py -k decision -q`
Expected: PASS (mount + the selection paths). Then the full pilot file: `.venv/bin/python -m pytest tests/test_tui_pilot.py -q`.

- [ ] **Step 5: Commit**

```bash
git add harness/tui/app.py tests/test_tui_pilot.py
git commit -m "feat(tui): mount DecisionPrompt on clarify; selection -> next prompt

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Full-suite green + primary-checkout check

**Files:**
- Possibly modify: any test that asserted the old clarify behavior (clarify chunk == plain prose with no meta).

**Interfaces:**
- Consumes: everything above.
- Produces: a green suite; primary checkout untouched.

- [ ] **Step 1: Run the whole suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS (all green). If a pre-existing clarify test asserts the exact old
chunk shape, it stays valid (empty-options path is byte-identical); only update one
if it asserted "no field_meta ever" AND now exercises an options path — note any
change in the commit body.

- [ ] **Step 2: Verify primary checkout untouched**

Run: `git -C /Users/alberto/Work/Quiubo/harness status --short`
Expected: empty output.

- [ ] **Step 3: Confirm `upstream/` untouched**

Run: `git diff --name-only main...HEAD | grep '^upstream/' || echo "upstream untouched"`
Expected: `upstream untouched`.

- [ ] **Step 4: Commit any cleanup**

```bash
git add -A
git commit -m "test: structured clarification suite green

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- Spec §2 (router `options` field + tolerant parse + additive degradation) → Task 1. ✓
- Spec §3.1 (emit attaches `field_meta.harness.decision`, byte-identical when empty) → Task 2; the `with_meta` nesting NOTE from the spec is RESOLVED (`with_meta` wraps under `"harness"`, confirmed acp_emit.py:41-44). ✓
- Spec §3.2 (mount `DecisionPrompt` from `state.decision`) → Task 4 (mount at the fold). ✓
- Spec §3.3 (handle `Selected`: option→title→next prompt; fallbacks; read view from `state.decision`; unmount/clear) → Task 4 (`on_decision_prompt_selected`) + Task 3 (`_submit_text` shared path). ✓
- Spec §4 (test plan: router / parser / emit / TUI round-trip) → Tasks 1, 2, 4 tests; Task 5 full green. ✓
- Spec §5 (deferred: permissions, in-turn resume, formal ACP signal) → not implemented; selection-as-next-prompt reuses the end-turn flow (Task 3/4). ✓

**Placeholder scan:** No TBD/TODO. Every code step shows the code. Two NOTES direct the implementer to confirm real names against neighboring code (the `HarnessTui(...)` ctor args and the `_handle_update` method name in the pilot tests; the `with_meta` import in acp_agent) — these are "match the established harness" pointers, not placeholders, and the production code is fully shown.

**Type consistency:** `options` is `list[tuple[str, str]]` in `Classification` (T1), consumed as `cls.options` and emitted as `[{title, rationale}]` (T2), parsed back to `DecisionView.options: tuple[(str,str),...]` by the existing `decision_from_meta` (T2 test), and read as `view.options[index][0]` (T4). `_submit_text(text: str)` defined T3, called T4. `DecisionPrompt.Selected(index: int)`, `TYPE_SOMETHING` (-1), `CHAT_ABOUT_IT` (-2) match `decision_prompt.py`. `DecisionOpened(view: DecisionView | None)` — T4 widens it to Optional if needed to clear.

**One implementer caution:** Task 4's `DecisionOpened(None)` to clear state requires `DecisionOpened.view` to accept `None`. If the dataclass is currently `view: DecisionView` (non-optional), widen to `DecisionView | None` in `state.py` as part of Task 4 — the reducer body (`decision=event.view`) already handles None correctly.
