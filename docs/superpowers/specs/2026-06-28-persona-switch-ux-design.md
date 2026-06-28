# Persona switching UX — making personas first-class on screen

**Date:** 2026-06-28
**Status:** Design (pre-plan)
**Author:** product/UX review (verified against running TUI)
**Relates to:** C2a indicator (#46), C2b rail (#53), C2c in-process seats (#56)

---

## 1. The problem, verified live

The engine treats each persona as a **separate, persistent desk**: C2c (PR #56) gives
every persona its own durable *seat* — its own `session_id`, conversation history,
memory, and resolved model — and switching routes **in-process** to that seat. Switch
back and the same seat resumes. This was a deliberate, three-Codex-pass decision; the
design doc is blunt that re-exec is the wrong primitive. **The engine model is correct
and is not in question.**

The **UI contradicts that model.** Verified by driving the running TUI through a real
switch (boot → send a prompt as `default` → switch to `maya`):

| | before switch | after switch |
|---|---|---|
| `session_id` | (default's) | `sess-maya` ✔ engine routed to new seat |
| transcript children | 4 | **5** |
| persona A's message on screen | yes | **still yes** |

After-switch transcript (literal):

```
▌ MARKER42                       ← default's message
▣ Build · mock model             ← default's turn
[classified: chat_question…]     ← default's turn
done                             ← default's reply
now talking to persona: maya     ← appended BELOW — screen now mixes both
```

So the transcript **bleeds**: persona A's conversation stays on screen and persona B's
replies render below it, into the same scrollback — even though the engine has routed to
a different session. The code seam is `_apply_persona_switch` (`harness/tui/app.py`
~L1120): it repoints `self._session_id`, updates the indicator/footer/model, and calls
`self._notify_line("now talking to persona: …")` **into the existing transcript**. It
never clears or swaps the view. A `_reset_conversation` helper (app.py ~L732) exists and
clears the transcript, but it is wired to model-change/reload, **not** to persona switch.

**Net effect:** the surface lies about which conversation you're in. This is the
"fundamentally different mental model, presented without explanation" failure — except
the UI doesn't merely fail to explain the per-desk model, it visually *denies* it.

## 2. Goal & principle

**Goal:** make personas first-class citizens on screen — the surface should *equal* the
engine truth, and teach the persistence model without a manual.

**Principle (the one idea everything derives from):** a first-class thing **owns a place
you can return to and find unchanged.** Therefore:

> **The transcript follows the seat.** Switching changes *which conversation you are
> looking at*. Nothing is merged; nothing is lost. Switching is **navigation between
> rooms**, not resetting and not interrupting.

Copy north-star (one sentence, reinforced by behavior, never shown as a wall of text):

> *Each persona is a separate, persistent conversation. Switching changes which one
> you're looking at — nothing is lost, nothing is merged.*

"Rooms," not "modes." You walk between rooms; each room keeps its own state.

## 3. Why this model (vs. the alternatives)

Two rejected alternatives, each of which *demotes* personas:

- **Shared scrollback + per-turn "who said it" badges.** Treats personas as voices in
  one thread (like @-mentions in one channel). Contradicts the separate-seats engine;
  trains the user to expect one continuous memory, which breaks the instant persona B
  "doesn't remember" what A was told. Rejected: it's a multiplayer thread, not
  first-class agents.
- **Blank view on every switch.** Honest about "different desk" but *lossy*: the persona
  has real history the engine remembers, yet the screen pretends it doesn't. Makes
  switching feel destructive, so users hesitate to switch. Rejected: first-class
  citizens don't get on-screen amnesia while the backend quietly recalls everything.

**Transcript-follows-the-seat** is the only option where screen == truth: this persona,
its real history, persisting across switches.

## 4. The one real constraint (drives the phasing)

The TUI today has **no persona-switch replay path** (verified, Codex review). Precisely:

- `set_persona` returns only `{ok, id, session_id, model}` (acp_agent.py:222) — **no
  transcript**.
- History is **not** on the `Seat` object (it holds only `session_id` + `model`,
  persona_sessions.py) — it lives in the `SessionState` referenced by the seat's
  `session_id` (acp_session.py), as a plain `{role, content, origin}` transcript.
- An engine `load_session` method **does** stream stored history as `[resumed] …` chunks
  (acp_agent.py:248-261) — but it is **not wired to the switch path**, and its raw
  `{role, content}` shape is **not** the TUI's normal render path (the TUI renders ACP
  `SessionUpdate` objects via `render_update()` + stream/meta handling, app.py:906-980).

So "swap to that persona's real history on switch-back" **requires an engine addition** —
either extend `set_persona` to return the seat's transcript in render-ready form, or wire a
switch-time history method — **plus** a client routine to convert/replay it. `load_session`
is a useful precedent, not a drop-in.

That cleanly splits the work:

- **Phase 1 — make the switch honest.** No engine change. Stop the bleed; establish the
  rooms model on screen. Switch-back starts visually empty (engine history intact, just
  not yet replayed).
- **Phase 2 — make it persistent on screen.** Engine returns the seat's history; the UI
  replays it on switch-back and shows a resumed seam. Fully delivers first-class persistent
  rooms.

Phase 1 alone moves the product from *"the UI contradicts the model"* to *"the UI
honestly shows the model"* — the bulk of the clarity win.

---

## 5. Phase 1 — make the switch honest (no engine change)

### 5.1 Clear the transcript on switch (kills the bleed)

In the switch-apply path, before writing the room header, clear the transcript so persona
A's messages do not remain on screen.

**Async seam (verified, Codex review):** `_apply_persona_switch` is **synchronous**
(app.py:1120) but the existing clear helper `_reset_conversation` is **async** (app.py:732,
`await`ed by its current callers). A sync method cannot `await` it. Resolve by **extracting
a synchronous `_clear_transcript()`** that does only the visual reset (remove `#transcript`
children + the minimal stream-state needed), and call that from `_apply_persona_switch`.
Do **not** turn `_apply_persona_switch` async — it is called synchronously from the
create-persona modal callback (app.py:1149-1153, see §7), and making it async forces that
path to change too.

**`_reset_conversation` does MORE than clear the transcript (verified, Codex review).**
It also resets `_streaming_md`, `_stream_buf`, `_stream_closed=True`, `_boundary_after=False`,
`_tokens=0`, and `_snapshot = initial_snapshot()` (app.py:738-744). On switch we **want**
the stream-buffer reset (no late deltas bleeding into the new room) but we must **not** blow
away the fleet/persona snapshot we just set via `PersonaResolved`. So the extracted
`_clear_transcript()` must reset only: transcript children + `_streaming_md`/`_stream_buf`/
`_stream_closed`/`_boundary_after` (stream state), and **leave `_snapshot` to the
`PersonaResolved` apply** that already ran in `_apply_persona_switch`. Ordering: clear
BEFORE the `PersonaResolved` apply, or have clear not touch the snapshot — pick the latter
(less coupling).

After clearing, write a single **room header** line (not the old terse confirmation):

```
── now in Maya's conversation ─────────────────────────
   separate thread · remembers across switches
```

Acceptance: after switching `default → maya`, transcript child count for `default`'s
messages is **0**; only the room header (and subsequently maya's turns) appear.

### 5.2 Persistent identity frame (always-on "whose room is this")

A constant header band at the top of the conversation view showing the persona's **display
name**, so at any scroll position the user knows whose room they're in.

**Display name lookup (verified, Codex review).** `set_persona` returns only
`{ok, id, session_id, model}` (acp_agent.py:222) — **no name**. `_apply_persona_switch`
only has `resp["id"]`. The TUI already resolves display names locally via
`persona_config.read_name` (used for rail rows, app.py:1055-1065). The header must use that
**same local lookup** (`read_name(id)`, id fallback) — not the switch response.

**Per-persona accent color: DEFERRED out of Phase 1 (corrected, Codex review).** The rail's
active row uses the **global** `$accent` token, not a per-persona color (agent_rail.py:50-58);
the theme defines one brand accent with no dynamic per-persona slots (theme.py). So
per-persona accent is **not** "reuse an existing token" — it is net-new design-system work
(a palette + a stable id→slot mapping wired through the theme). Phase 1 ships the identity
frame in the **single brand `$accent`** (name only, no per-persona hue). Per-persona color is
tracked as a **separate follow-up** (§6.3 / backlog), not a Phase 1 deliverable.

### 5.3 Mid-turn switch: queue, don't silently ignore

Today `on_persona_selected` returns early and **silently** when `_turn_active` (app.py
~L1100). Clicking a persona and having nothing happen reads as broken on a first-class
surface. Phase 1:

- On a mid-turn selection, write a quiet line: *"Alex is still working — switching when
  this turn finishes."*
- **Queue** the requested switch in a **new** `_pending_persona: str | None` field (the
  existing `_queued` is a prompt FIFO only — verified, app.py:129/484/842 — and does **not**
  carry switches). **Last-wins:** a later mid-turn selection overwrites `_pending_persona`.
- Fire it on turn-end. The turn `finally` already runs `_drain_queue()` after
  `_turn_active=False` (app.py:829-835).

**Ordering trap (verified, Codex review) — pending switch must run BEFORE `_drain_queue`.**
`_drain_queue` sends the next queued prompt **on the current `_session_id`**
(app.py:837-842), and `_apply_persona_switch` **changes `_session_id`** (app.py:1125). If a
queued prompt drained first, it would run in the **old** room. Resolution: in the turn-end
`finally`, **apply `_pending_persona` first** (clear transcript + repoint session + room
header), **then** drain — so any prompt the user queued runs in the **new** persona's room.
(This is a deliberate product call: a switch is a stronger signal than a stale queued prompt.
Document it; if it surprises users, revisit. The alternative — drain-then-switch — runs old
prompts in the old room and is arguably also defensible, but mixing both rooms' work in one
turn-end is worse than either.)

**Late-delta safety (verified, Codex review).** The switch must fire only once the old turn
is fully finished. `_end_stream` intentionally retains `_streaming_md` for late deltas
(app.py:775-789) and `on_session_update` already drops updates whose `session_id !=
self._session_id` (app.py:906-916). Because the pending switch runs in the turn `finally`
(strictly after the turn completes) and `_clear_transcript` resets the stream buffer, no
late delta from A can render into B's room. The session-id filter is the backstop. The
immediate (idle) switch path keeps its current guard unchanged.

Acceptance: selecting persona B mid-turn shows the "still working" line and does **not**
switch immediately; when A's turn ends, the switch fires automatically (transcript clears,
room header for B appears). A prompt typed-and-queued during A's turn runs in **B's** room,
not A's. Rapid A→B→C mid-turn lands on C only.

### 5.4 Empty-room state (Phase-1-honest copy)

**Copy trap (verified, Codex review).** In Phase 1 the screen clears on every switch and we
do **not** replay history yet (§4). So a persona *with* prior turns still shows a **blank**
room on switch-back. Copy that says "remembers across switches" would promise persistence the
user **cannot see** — the blank screen actively contradicts it. Phase 1 copy must describe
only what is true *on screen now*.

Two facts make this safe to get right:
- Phase 1 **cannot distinguish** "brand-new persona" from "has history, just not replayed"
  from the switch response alone (no count/flag — acp_agent.py:222). So Phase 1 uses **one**
  empty-room line for both, and it must be true for both.
- The line therefore states **separateness** (always true, visible) and **not** visible
  recall (only true after Phase 2).

Phase 1 empty-room line (under the room header, shown until the first turn renders):

```
This is Maya's conversation — separate from your others. Say hello.
```

Phase 2 **upgrades** this: replay makes history visible, so the line gains the persistence
claim and only shows for genuinely-new personas (see §6.2).

### 5.5 Copy inventory (Phase 1 — honest about visible state)

| Surface | Copy |
|---|---|
| Room header | `now in {Name}'s conversation` + subline `a separate conversation` |
| Empty room | `This is {Name}'s conversation — separate from your others. Say hello.` |
| Mid-turn switch | `{Active} is still working — switching when this turn finishes.` |
| Switch failed | keep existing `persona: {error}` line |
| Rail row tooltip / hint | `Each persona keeps its own conversation. ↑↓ to choose · enter to switch` |

> The phrase "remembers across switches" is **deferred to Phase 2 copy** (§6.2), where replay
> makes that claim verifiable on screen. Phase 1 says only "separate," which is true and
> visible today.

### 5.6 Phase 1 non-goals

- No replay of prior on-screen history (engine doesn't return it yet → Phase 2).
- No change to seat/session engine behavior.
- No new `persona.toml` color field (derive accent from id for now).

---

## 6. Phase 2 — persistent on screen (needs engine support)

**Design decided (2026-06-28), grounded in the verified current-state seam map.** Two
architecture decisions, both settled:

1. **Transport: stream via `session_update`.** The engine replays the seat's transcript by
   emitting ACP session-update notifications — each historical message flows through the
   **same** `on_session_update → render_update → _stream_message` path the TUI already uses
   (app.py). The client writes **zero** render code; no second renderer. (This is option (a)
   from the prior draft; (b) is dropped.)
2. **Trigger: a separate explicit `harness/replay_session` ext-method**, called by the client
   *after* it clears the transcript. `set_persona` stays as-is except it gains a
   `has_history` (bool) / `message_count` field so the client knows whether to replay and can
   scope the empty-room copy to genuinely-new personas. Switch = repoint; replay = explicit,
   ordered after the clear. The two ext-methods each do one job and test independently.

### 6.1 Engine: `harness/replay_session` streams the seat's transcript

**Data source (verified):** `SessionState.transcript` (acp_session.py:22) holds
`list[{role, content, origin}]`, populated on **every** turn via `store.extend` (chat path
acp_agent.py:377, agent path :446/:475). So a switched-back persona's transcript genuinely
holds its prior user+assistant messages (true in mock mode too).

**NOT `load_session`'s precedent verbatim:** `load_session` (acp_agent.py:248-261) loops over
`state.history` (turn *summaries*, emitting `[resumed] {kind}: {prompt}`), which is the wrong
data. Phase 2 loops over `state.transcript` (the actual messages).

**New ext-method `harness/replay_session`** in `acp_agent.py`'s `ext_method` dispatch
(alongside `set_persona`, ~L177):
- Params `{id}` → resolve the persona's seat session_id via a **side-effect-free** seat
  resolver. **(Codex review — RISKY):** `_activate_seat` mutates active state
  (`_active_persona`, `_worker_model_id`, the session's `worker_model`, acp_agent.py:219-221).
  Replay must NOT re-trigger those — it only READS. So extract the pure `get_or_create` into a
  `_seat_for(pid) -> Seat` helper (no mutations) shared by both `_activate_seat` and
  `_replay_session`.
- `state = self._store.get(session_id)`.
- For each `m` in `state.transcript`, emit a `session_update` whose ACP update type renders as
  the right kind: assistant/agent messages → `update_agent_message_text(m["content"])`
  (renders as `kind="message"`); user messages → the user-message update
  (renders as `kind="user"`, the `▌` prefix). Map by `m["role"]`.
- **Boundary between messages (Codex review — message-merge):** the client's `_stream_message`
  keeps ONE markdown widget open across consecutive `message` deltas (app.py:936-987), so
  back-to-back replayed messages would MERGE into a single block. Before every message *after
  the first*, emit a `stream_reset` meta update (`with_meta(message_chunk(""),
  {"stream_reset": True})`) — the existing multi-step-narration boundary the client already
  folds via `_end_stream(boundary=True)`. Each replayed message then renders as its own widget.
- After the loop, emit ONE seam update: an `update_agent_message_text("")` carrying
  `field_meta={"harness": {"resumed": True}}` (via `with_meta`, acp_emit.py:41) so the client
  renders the `── resumed ──` divider as a distinct chip (not a message). The client folds
  `harness.resumed` in `on_session_update` before `render_update`, the same way it already
  folds `stream_reset` / `task_classified` / `persona` meta (app.py ~L1013-1044).
- Return `{ok: True, count: <n>}`.

**`set_persona` add `has_history`:** in `_activate_seat` (acp_agent.py:205-222), after
resolving the seat, read `len(self._store.get(seat.session_id).transcript)` and add it to the
return dict: `{..., "message_count": n}`. (Cheap — one dict lookup; does NOT send the
transcript, so the `set_persona` payload stays small — that's why replay is a separate call.)

### 6.2 UI: replay on switch-back + resumed seam + upgraded copy

**Insertion point (verified):** `_apply_persona_switch` (app.py ~L1228), immediately after
`self._clear_transcript()` and before the room-header block.

**Async seam (Phase-1 constraint still binds):** `_apply_persona_switch` is **sync** (Phase 1
I1 fix + create-modal depend on this — do NOT make it async). The replay is an `await
ext_method(...)`. Resolve exactly as Phase 1's deferred switch did: schedule the replay as a
worker — `self.run_worker(self._replay_session(resp["id"]), thread=False)` — where
`_replay_session` is async and `await`s the ext-method. The streamed `session_update`s arrive
and render through the normal path. Only replay when `resp.get("message_count", 0) > 0`.

**Render flow:** each replayed `session_update` is filtered by `on_session_update`'s
session-id/gen guards (app.py ~L995) — they pass because `_session_id` was just repointed to
the new seat and gen is current — then `render_update` → `_stream_message`/user-line renders
it into the freshly-cleared transcript. The `resumed` seam update is folded by a new
`harness.resumed` meta branch into a themed `── resumed ──` divider line.

**Resumed seam (rendered):**
```
   …earlier with Maya…
── resumed ────────────────────────────────────────────
   you: ▌
```
The seam teaches persistence better than any tooltip — the user *sees* "this is where I left
off," not "fresh start."

**Copy upgrade (the promise §5.4 deferred to here):**
- Room subline → `a separate conversation · remembers across switches` (now TRUE on screen).
- Empty-room line shows **only** when `message_count == 0` (genuinely-new persona) →
  `This is {Name}'s conversation. It's separate from your others and remembers across
  switches. Say hello.`
- A persona WITH history skips the empty-room line entirely (replay fills the room instead).

**Acceptance:** send a turn as Maya → switch to Alex → switch back to Maya → Maya's earlier
user+assistant turn(s) render above a `── resumed ──` seam, her model/session restored
(already true in the engine); switching to a brand-new persona shows the empty-room line and
no seam.

### 6.3 Phase 2 non-goals

- Live multi-persona animation / concurrent visible threads (a later cycle; C2c notes flag
  fleet animation as "gravy").
- Cross-persona references / hand-off UI.

---

## 7. Edge cases (full matrix)

| Case | Phase 1 behavior | Phase 2 behavior |
|---|---|---|
| Switch to **active** persona (no-op) | close drawer, refocus, no clear (already correct in code) | same |
| Switch **mid-turn** | queue + "still working" line; fire on turn-end | same |
| Switch with **unsaved composer text** | **Decided: keep the draft in the composer** across switch (drafts are per-*view*, not per-persona; the text you typed stays where your cursor is — least surprising). `_clear_transcript` touches the transcript + stream state only, **not** the composer value, so this is the default behavior — confirm no code clears the input. | same |
| **First send** from landing (no persona switch yet) | unchanged (landing → conversation) | unchanged |
| Switch to **brand-new** persona (just created) | empty-room line (§5.4) | empty-room line (only here, §6.2) |
| Switch to persona with **history** | blank room + header (no replay yet; copy says only "separate", §5.4) | **replay history** + resumed seam (§6.2) |
| **Switch failed** (engine error) | existing error line; no view change (clear happens only AFTER an `ok` response — verified app.py:1115-1118) | same |
| **Create** persona (rail `n`) | routes through `_apply_persona_switch` (app.py:1149-1153); gets clear + room header for free **provided `_apply_persona_switch` stays sync** (§5.1). If it ever goes async, the modal callback must be adapted. | same |
| Rapid **A→B→C** mid-turn | `_pending_persona` is last-wins (overwrite); only C lands on turn-end (§5.3) | same |
| Queued prompt typed during a mid-turn switch | runs in the **new** persona's room (pending switch applies before `_drain_queue`, §5.3) | same |

## 8. Code seams (where each change lands — verified against live code)

- `harness/tui/app.py :: _apply_persona_switch` (L1120, **sync** — keep it sync) — call the
  new `_clear_transcript()` before the room header; replace the terse `now talking to
  persona:` line with the room header (display name via `persona_config.read_name(id)`, §5.2).
- `harness/tui/app.py` — **new `_clear_transcript()` (sync)**: extract the transcript +
  stream-state reset from the **async** `_reset_conversation` (L732). Do **not** call
  `_reset_conversation` from the sync switch path (async seam, §5.1), and do **not** reset
  `_snapshot` in the extracted helper (`PersonaResolved` owns it).
- `harness/tui/app.py :: on_persona_selected` (L1099) — mid-turn: write the "still working"
  line + set **new `_pending_persona`** field (last-wins) instead of silent return.
- `harness/tui/app.py` turn-end `finally` (L829-835) — apply `_pending_persona` **before**
  `_drain_queue()` (ordering trap, §5.3).
- conversation header widget — new persistent identity band (**display name; single brand
  `$accent`**, per-persona color deferred — §5.2).
- `harness/tui/widgets/agent_rail.py` — row tooltip/hint copy (§5.5).
- **Phase 2:** `harness/acp_agent.py` — extend `set_persona`/`_activate_seat` (L205-222) to
  return history, or add a switch-time history method modeled on `load_session` (L248-261);
  history is read from the `SessionState` behind the seat (`harness/acp_session.py`), not the
  `Seat` (`harness/persona_sessions.py`). Plus the client replay/convert routine (§6.1).

> Seam-module correction (Codex review): `set_persona`, `_activate_seat`, and `load_session`
> live in **`harness/acp_agent.py`**, not `acp_session.py`. `acp_session.py` holds
> `SessionState`/transcript; `persona_sessions.py` holds `Seat`.

## 9. Acceptance / verification

Reproduce the original bug first (pilot test: send as A, switch to B, assert A's message
text is gone from the transcript and child-count for A's turns is 0). Then per-phase
acceptance from §5 and §6. Verify TUI layout **visually** (screenshot/SVG render), not
only via green tests — the C2/agents-drawer history shows tests pass while layout is wrong.

## 10. Open decisions (for the author before plan)

1. **Phase 1 switch-back shows a BLANK room** for a persona that has history (no replay until
   Phase 2). The §5.4 copy is written to be honest about this ("separate," not "remembers").
   Is shipping Phase 1 with that interim acceptable, or is Phase 2 replay a hard prerequisite
   to ship anything? *(This is the one decision that gates the phasing.)*

**Resolved in-spec (Codex review):**
- *Unsaved composer draft on switch* → **keep in the box** (`_clear_transcript` doesn't touch
  the composer; §7).
- *Persona accent color* → **deferred** out of Phase 1; rail uses the global `$accent`, so
  per-persona color is net-new design-system work, not reuse (§5.2). Revisit as a follow-up.
```
