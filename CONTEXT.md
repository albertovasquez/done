# quiubo-harness

The harness wraps a Claude Code agent (a separate OS process, spoken to over ACP)
behind a Textual TUI. This glossary names the concepts that recur across that
boundary — the vocabulary to use in code, comments, and reviews. It is a glossary,
not a spec: no implementation detail lives here.

## Language

### Rendering

**Transcript**:
The scrolling column of the conversation — user messages, agent prose, tool
captions, chips, and per-turn footers, in arrival order.
_Avoid_: log, output, history (history is the agent-side turn record, a different thing).

**Delta**:
One incremental chunk of agent message text as it streams in. Many deltas
accumulate into a single rendered answer.
_Avoid_: chunk, token, piece.

**Stream Painter**:
The module that turns a series of deltas into correctly-placed, coalesced
Markdown in the Transcript. Owns the live answer widget and decides whether an
arriving delta opens a fresh block or extends an existing one. A deep module: a
small interface (`delta`, `boundary`, `end`, `reset`) over the buffering,
paint-coalescing, and block-placement logic. It owns stream-widget lifecycle
ONLY — resetting the whole conversation and rendering the user's own message
stay App orchestration and merely call into the painter.
_Avoid_: renderer, stream handler.

**Transcript View**:
The narrow seam the Stream Painter depends on: the handful of operations it needs
(inspect children, mount a widget, mount above the footer, schedule a repeating
paint, schedule a one-shot post-refresh paint, hide the working indicator). The
scheduling operations delegate to the App's message pump — never to the answer
widget — so the paint timer shares the App's lifecycle. The live TUI provides one
adapter over the real Transcript; tests provide a fake.
_Avoid_: transcript interface, DOM.

**Boundary**:
An in-turn event (a tool call, a thought, or an explicit stream reset) that ends
the current answer block while the agent keeps producing the same turn. The next
prose after a Boundary is a new step and opens its own block — distinct from a
Late Delta, which extends the block it belongs to.
_Avoid_: break, separator.

**Late Delta**:
A delta belonging to the just-finished answer that arrives after its turn ended
(agent-notification lag), possibly after a new user turn began. It extends the
answer's existing block in place — it must never open a stray block under the
next prompt.
_Avoid_: trailing delta, straggler.

**Footer** (run caption):
The dimmed, indented per-turn caption mounted below an answer once the turn ends,
carrying the run summary and the copy affordance. A Late Delta must render ABOVE
the Footer, not under it.
_Avoid_: meta line, caption (unqualified).
