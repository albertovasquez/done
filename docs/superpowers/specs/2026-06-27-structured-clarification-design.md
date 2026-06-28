# Structured clarification — design (issue #66)

**Status:** design / spec (no implementation). Hand-off to writing-plans.
**Date:** 2026-06-27
**Author:** Alberto Vasquez (with Claude Opus 4.8)
**Tracks:** GitHub issue #66 — roadmap item 2.7 of
`docs/superpowers/specs/2026-06-27-claude-code-system-prompt-gap-analysis.md`
("structured clarification & richer permission UX"). The gap analysis flags this
as a "nice early TUI win."

**Scope of this slice (decided in brainstorming):**
- Ship **structured clarification only**: the router emits 2–4 candidate
  interpretations as options; the existing `DecisionPrompt` widget renders them;
  a selection is submitted as the next user turn.
- **Permissions split out:** graduated permission modes (`acceptEdits`, `plan`,
  beyond binary Allow/Reject) become a **separate follow-up issue**. The gap
  analysis already calls them separable; they touch a different surface
  (`PermissionModal` / the yolo chip) and would double the blast radius.

**Hard constraint:** zero edits under `upstream/` (AGENTS.md #4); work in a git
worktree, never `main` (AGENTS.md #1). All file:line refs verified against the
worktree at authoring time — re-verify before acting (AGENTS.md #6).

**This completes prior intent, not new ground.** The TUI design-system spec
(`docs/superpowers/specs/2026-06-26-tui-design-system-design.md`) already set this
up: §5.1 "`awaiting_decision` is recognized from a `field_meta['harness']` chip
now"; line 398 "Wire `awaiting_decision` to the meta chip"; line 401
"clarification renders inline with numbered options + dimmed rationale + free-text
fallback." This slice finishes that wiring.

---

## 0. What already exists vs. the gap

Most of the chain is already built (`components.md` tags `DecisionPrompt`
"built·unwired"). Verified in code:

**Exists, reused unchanged:**
- `DecisionView(question, options: tuple[(title, rationale), ...])` —
  `harness/tui/state.py:54`.
- `decision_from_meta(field_meta)` — `state.py:272`. **Already parses the exact
  shape** `field_meta.harness.decision = {question, options:[{title, rationale}]}`.
- `DecisionOpened` reducer + `AWAITING_DECISION` state — `state.py:143,186-187`.
- The TUI already folds the meta: `app.py:887` calls `decision_from_meta(...)` →
  `DecisionOpened(dv)` → `state.decision`.
- `DecisionPrompt` widget — `harness/tui/widgets/decision_prompt.py`. Renders the
  question + numbered options (title + dimmed rationale) + two fallbacks
  ("Type something" = `TYPE_SOMETHING` -1, "Chat about this" = `CHAT_ABOUT_IT`
  -2), and posts a `DecisionPrompt.Selected(index)` message on enter/digit.

**The gap (what this builds):**
1. The router returns a **flat `clarifying_question` string with NO options**
   (`router.py:40,138-141`). `Classification` has no `options` field.
2. The clarify emit path sends the question as **plain prose** and ends the turn
   (`acp_agent.py:333-343`) — it never attaches `field_meta.harness.decision`,
   so `DecisionPrompt` never fires for clarifications.
3. `DecisionPrompt` is **never mounted** anywhere (`grep "DecisionPrompt("` finds
   only the class def) and its `Selected` message is **never handled**. The
   reducer sets `state.decision`, but nothing mounts the widget or acts on a pick.

---

## 1. Architecture & data flow

```
router.classify()
    └─emits─► Classification.options: list[(title, rationale)]        [NEW field]
              (cheap model returns an `options` array; absent/garbage → [])
                         │
                         ▼
acp_agent clarify path (acp_agent.py:333)
    └─attaches─► field_meta.harness.decision = {question, options}    [NEW meta attach]
                 (only when options present; else today's plain prose, byte-identical)
                         │
                         ▼
TUI app.py:887  decision_from_meta()  ─► DecisionOpened(dv) ─► state.decision   [ALL EXIST]
                         │
                         ▼
app.py  mounts DecisionPrompt(state.decision)                        [NEW: mount]
                         │
                         ▼
DecisionPrompt.Selected(index)
    └─handled─► submit option.title as the NEXT prompt               [NEW: handler]
                (fallbacks: Type something / Chat about this)
```

**Boundaries.** The router change is self-contained (JSON contract + one dataclass
field + tolerant parse). The emit change is one `with_meta` attach. The two TUI
changes (mount + `Selected` handler) live in `app.py` beside the existing
`DecisionOpened` fold. No new module; no `upstream/` edit; the chat path is
untouched.

---

## 2. Router contract & graceful degradation

`Classification` gains:

```python
options: list[tuple[str, str]] = field(default_factory=list)   # (title, rationale)
```

mirroring `DecisionView.options`.

The router's `_system_prompt` JSON contract gains an `options` key: *when the
request is ambiguous or low-confidence, also return `options` — a list of 2–4
objects `{title, rationale}`, each a concrete interpretation the agent could act
on* (e.g. `{"title": "Fix the failing test", "rationale": "Run the suite and
repair the red test"}`). When the request is clear, the model returns `options:
[]` or omits the key.

Parse follows the **exact tolerance pattern already in `classify()`** (the same
defensive style used for `skills`/`confidence`/`reasoning`):

```python
raw_opts = data.get("options")
raw_opts = raw_opts if isinstance(raw_opts, list) else []   # scalar/str isn't a list
options = [(str(o["title"]), str(o.get("rationale", "")))
           for o in raw_opts
           if isinstance(o, dict) and o.get("title")]
```

### The load-bearing rule: options are purely ADDITIVE

Every existing path is unchanged when `options == []`:
- The flat `clarifying_question` string is **still always set** (today's behavior,
  `router.py:138-141`).
- The unparseable-JSON branch (`classify()` lines 114-124) already returns a
  clarification with no options — it stays exactly as-is.
- If the cheap model returns garbage options, the filter drops them → `[]` → the
  plain question is the fallback. **No new failure mode is introduced.**

Worst case = "no clickable options, just the question" — identical to today. This
is the safety property that makes the slice low-risk.

---

## 3. Emit + the selection→prompt round-trip

### 3.1 Emit (`acp_agent.py:333-343`)

Today the clarify branch sends `message_chunk(q)` and returns `end_turn`. The
change: when `cls.options` is non-empty, attach the decision meta to that chunk,
reusing the existing `with_meta(...)` helper the persona/memory emits already use:

```python
if cls.needs_clarification or cls.task_type == "ambiguous":
    q = cls.clarifying_question or "Could you clarify the task?"
    chunk = message_chunk(q)
    if cls.options:
        chunk = with_meta(chunk, {"decision": {
            "question": q,
            "options": [{"title": t, "rationale": r} for t, r in cls.options]}})
    await self._conn.session_update(session_id, chunk)
    # unchanged below: record clarify turn, write user turn, trace, return end_turn
```

The `{question, options}` shape is exactly what `decision_from_meta` parses. When
`options` is empty the chunk is **byte-identical to today** (plain prose). The
turn still ends (`end_turn`) — matching the "selection becomes the next prompt"
decision; no in-turn resume machinery.

> NOTE: confirm the exact `with_meta` meta-key nesting at the call site — the
> existing persona/memory emits wrap under `{"harness": {...}}` or pass the inner
> dict; match whichever `decision_from_meta` reads (`field_meta.harness.decision`).
> `decision_from_meta` expects `field_meta["harness"]["decision"]`, so the attach
> must land there. Mirror the persona_load/memory_load emits at `acp_agent.py:319-331`.

### 3.2 Mount (`app.py`, at the `DecisionOpened` fold ~:887-889)

Where the reducer sets `state.decision`, mount `DecisionPrompt(state.decision)`
into the transcript region if not already mounted. The `AWAITING_DECISION` state
already flows; this makes the widget appear. Mirror the nearby `PermissionModal`
mount pattern (the catalogued sibling).

### 3.3 Round-trip — handle `DecisionPrompt.Selected`

A Textual message handler on the app:

```python
def on_decision_prompt_selected(self, msg: DecisionPrompt.Selected) -> None:
    view = self._active_decision_view()     # read from state.decision (the snapshot)
    if view is None:
        return
    if msg.index == TYPE_SOMETHING:         # -1: focus composer, let them type
        self._focus_prompt()
    elif msg.index == CHAT_ABOUT_IT:        # -2: prefill a chat-routable prefix
        self._focus_prompt(prefill="Let's discuss: ")
    else:
        self._submit_prompt(view.options[msg.index][0])   # option TITLE → next prompt
    self._dismiss_decision()                # unmount widget, clear state.decision
```

- Picking option N submits its **title** as a fresh user turn via the existing
  submit path; the router re-classifies the now-concrete request.
- "Type something" focuses the composer (no submit). "Chat about this" prefills a
  prefix that routes to the chat path.
- The live `DecisionView` is read from `state.decision` (the snapshot) — do NOT
  thread separate state. Index→title mapping uses `view.options[msg.index]`.

`TYPE_SOMETHING`/`CHAT_ABOUT_IT` are already exported from `decision_prompt.py`
(-1 / -2); import them in `app.py`.

---

## 4. Test plan

Mirror how `tests/test_router*.py` / `tests/test_tui_state.py` / the app pilots
already test each layer. TDD per the repo discipline.

**Router (`tests/test_router*.py`):**
- Stub JSON with a valid `options` array → `Classification.options ==
  [(title, rationale), ...]`.
- `options` absent → `options == []`, `clarifying_question` still set (degradation).
- Malformed options (scalar; entry missing `title`; non-dict entries) → filtered
  to `[]`, no raise.
- Clear/high-confidence request → `options == []`, `needs_clarification` False.

**State/parser (`tests/test_tui_state.py`):** `decision_from_meta` parses the
router's exact emitted `{question, options:[{title, rationale}]}` into a
`DecisionView` (extends existing coverage).

**Emit (`tests/test_acp_*.py`):** drive the clarify path with a classification
carrying options → assert the `session_update` chunk's
`field_meta.harness.decision == {question, options}`; with empty options → assert
**no** `decision` meta (the byte-identical-to-today guard).

**TUI mount + round-trip (app pilot):**
- `state.decision` set → `DecisionPrompt` is mounted.
- `Selected(0)` → app submits `options[0].title` as a prompt (assert the submit
  path receives the title).
- `Selected(TYPE_SOMETHING)` → composer focused, no submit;
  `Selected(CHAT_ABOUT_IT)` → prefill present.
- After any selection → widget unmounted, `state.decision` cleared.

**Suite green:** `.venv/bin/python -m pytest tests/ -q` (run with the WORKTREE as
cwd — primary venv, no editable-install shadowing). `upstream/` untouched; primary
checkout clean.

---

## 5. Deferred / out of scope (tracked, not built here)

1. **Graduated permission modes** (`acceptEdits`, `plan`, …) — a separate
   follow-up issue; different surface (`PermissionModal` / yolo chip).
2. **In-turn resume** (keep the turn open, dispatch the selection without a second
   router pass) — rejected in favor of selection-as-next-prompt (smaller, reuses
   the existing end-turn flow). Revisit only if the extra router pass proves
   costly.
3. **A formal ACP clarification signal** — `decision_from_meta`'s docstring notes
   a future swap from the `field_meta` chip to a formal ACP signal "with no widget
   change." Out of scope; the meta-chip path is the contract for now.

---

## 6. Provenance

File:line claims verified against the worktree at authoring time (2026-06-27):
`router.py:32-41` (`Classification`), `:58-75` (`_system_prompt` JSON contract),
`:102-144` (`classify` + tolerant parse + flat `clarifying_question`);
`acp_agent.py:333-343` (clarify emit, `end_turn`), `:319-331`
(persona_load/memory_load `with_meta` emit pattern to mirror);
`state.py:54-56` (`DecisionView`), `:143,186-187` (`DecisionOpened` reducer),
`:272-294` (`decision_from_meta`, parses `{question, options:[{title,rationale}]}`);
`app.py:887-889` (decision fold), `:39` (imports);
`decision_prompt.py:17-18` (`TYPE_SOMETHING`/`CHAT_ABOUT_IT`), `:24-27`
(`Selected` message), `:79-104` (select/key handling).
Prior intent: `2026-06-26-tui-design-system-design.md` §5.1, lines 398, 401.
Re-verify before acting (AGENTS.md #6).
