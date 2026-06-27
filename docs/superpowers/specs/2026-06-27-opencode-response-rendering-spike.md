# Research spike — response & tool-call rendering, an OpenCode-informed methodology

**Status:** research spike (no implementation). Hand-off for a refinement team.
**Date:** 2026-06-27
**Author:** Alberto Vasquez (with Claude Opus 4.8)
**Scope:** how the `dn` Textual TUI structures **agent responses** (the response
stream itself) and **tool calls** into legible *blocks* — the gap the existing
design system deliberately left open.
**Primary reference:** OpenCode (`sst/opencode`), read from source at commit on
`main`, 2026-06-27.
**Builds on (does not replace):**
`docs/superpowers/specs/2026-06-26-tui-design-system-design.md` and the catalog
`harness/tui/styles/components.md`. This spike fills **group B (responses)** and
sharpens **group C (work)**; it does not re-open the token source-of-truth, the
reducer (`state.py`), or the four-category event model — it *uses* them.

---

## 0. TL;DR

1. **The brief was "borrow OpenCode's clean block approach."** The deeper ask,
   surfaced in brainstorming, is bigger: **a way of *thinking* — a design
   methodology — so rendering decisions (like "are tool calls inline or pinned?")
   resolve by principle instead of taste.** This spike leads with that framework
   (§3) and uses OpenCode as the evidence that pressure-tests it.
2. **Honest caveat up front (§2):** OpenCode has **fully migrated off
   Go/Bubbletea**. Its current terminal UI runs on **OpenTUI (`@opentui/solid`)**
   — a SolidJS-driven terminal **cell-grid** renderer, the direct analogue of our
   Textual. That's *good news*: its choices map almost 1:1 onto our constraints.
   The polished web/desktop app (`session-ui`, SolidJS + CSS) is a *secondary*
   reference for visual intent only — its CSS primitives do **not** translate.
3. **The core problem** ("everything is just thrown out, no blocks") is a
   **part model** problem, not a styling problem. OpenCode renders a message as a
   sequence of typed **parts** (text · tool · reasoning), each its own block with
   its own rhythm. We render the response as one undifferentiated Markdown blob.
   The fix is structural (§5), and it slots onto our existing reducer.
4. **Recommended now:** adopt a **message-parts model** for the transcript, an
   assistant-part **indentation + 1-line-rhythm** discipline, a **collapsible
   reasoning block**, and a **syntax/diff token expansion** (§6) — all expressed
   through the existing catalog and tokens.
5. **The one genuinely contested decision — tools *inline* (OpenCode) vs *pinned*
   (our principle #7) — is left as a worked open question (§4, §7),** resolved
   *by the methodology* rather than decreed, because that is exactly the kind of
   call the framework exists to make.

---

> **Codex adversarial review applied (2026-06-27).** The first draft overstated
> what exists today and undersold the engineering cost of its own proposals. The
> corrections are folded inline and flagged **[corrected]**; the load-bearing one
> is §8/§6.3 — the "blocks" widgets are **blocked on a missing ordered-parts model
> in the reducer**, so they are NOT "no new architectural seam." Read §8 first if
> you're scoping the work.

## 1. Why this spike exists

The existing design system is mature: tokens, an agent-state reducer, a clean
four-category model (responses / work / decisions / future), and an ~18-component
catalog. But it made one explicit punt: in the catalog, `AnswerStream` is marked
*"exists today — kept unchanged… Do not replace."* The response **stream itself**
— how prose, code, reasoning, and results are visually *sectioned* — was never
fully designed.

**[corrected]** It is *not* one undifferentiated Markdown widget. `app.py`
already closes the live Markdown block at **in-turn boundaries** (a tool call, a
thought, a stream reset) and opens a fresh one for the next prose
(`_end_stream(boundary=True)` / `_boundary_after`, `app.py:103,635,648,727`). So
today's transcript already has *coarse* block structure between prose runs. What
it lacks is **per-part typing and styling**: a code fence, a diff, and the model's
reasoning all still render as undifferentiated Markdown inside those runs.

That gap is the user's complaint: *"Blocks is one thing we're not doing well…
everything is just thrown out."* Prose vs. code vs. a result vs. reasoning are not
visually distinct *kinds*. This spike designs that missing typing+styling layer,
using OpenCode — which solved exactly this in a comparable terminal renderer — as
the reference, and abstracts the solution into a reusable methodology.

---

## 2. What OpenCode actually is now (read this before trusting any screenshot)

A material finding from reading the source, not the marketing:

| Surface | Stack | Useful to us as… |
|---|---|---|
| **`packages/tui`** (the real terminal UI) | **OpenTUI** (`@opentui/solid`) — SolidJS reactivity → terminal **cell grid** | **Primary, near-1:1 reference.** Same problem space as Textual: boxes, padding, flex, fg/bg, custom border chars, capped output. |
| `packages/session-ui`, `packages/ui` | SolidJS + CSS (web/desktop app) | **Secondary, intent only.** Its `data-attribute` CSS, Shiki, `light-dark()`, hover-opacity reveals **do not** map to a terminal. Borrow the *feel* (layered backgrounds, 24px rhythm), never the mechanism. |
| `packages/tui` (historical) | ~~Go + Bubbletea/Lipgloss~~ | **Gone.** Zero `.go` files in the tree. Any "OpenCode is a Go TUI" lore is stale. |

**Consequence for this document:** every concrete pattern below is cited from
the **OpenTUI** terminal code (`packages/tui/src/...`), because that is the only
fair apples-to-apples reference. Where a web-app idea is mentioned it is labelled
*intent-only*.

---

## 3. The methodology — "how we think" (the actual deliverable)

A design system is not a pile of widgets; it's a **way of deciding** that makes
the pile coherent. The existing spec already has the *laws* (H1–H5) and *tokens*.
What it lacks — and what makes the tools-inline-vs-pinned question feel
unresolvable — is an explicit **decision procedure** for rendering questions.
Here is a small one. The refinement team should treat §3 as the load-bearing
part of this spike; §4–§7 are it applied.

### 3.1 The unit of thought: the **part**, not the message

> **P1 — A turn is a sequence of typed *parts*, and every part is an independent,
> classifiable block.** Prose, a tool call, the model's reasoning, a result, a
> diff — each is a *part* with a *kind*. The renderer's first job is to know the
> kind; styling follows from kind.

This is OpenCode's central structural decision (§4.1) and the direct antidote to
"everything thrown out." **[corrected]** `render.py` produces typed
`RenderedItem`s, but the kinds are coarse — `message | thought | user | tool |
tool_update` (`render.py:15`) — and the *reducer drops them*: `state.reduce` has
cases only for `message | tool | tool_update` (`state.py:178-190`), with **no
`thought` case and no per-turn ordered-part list** on `AgentSnapshot`
(`state.py:60-69` carries `tasks`/`tools`, not a transcript). So the part-typing
this principle needs **does not exist yet end-to-end** — fenced code and diffs are
sub-spans *inside* a `message`, and reasoning is silently dropped. The methodology
says it must be made to exist; §8 scopes that as the gating work.

### 3.2 The four decision lenses

For **any** "how should this render?" question, pass it through these four lenses
in order. They are derived from the existing laws, not invented:

- **L1 — Kind.** *What part-kind is this, and what does the engine actually emit
  for it?* (engine-truthful, per H1/H5). Never style something you can't name
  from a real signal.
- **L2 — Persistence.** *Is this a permanent record of what happened, or a
  transient view of what's happening now?* Permanent → it belongs in the
  scrollback transcript. Transient → it belongs in the pinned activity zone.
  **This single lens resolves the tools tension (§4).**
- **L3 — Density & default state.** *How much does the user need by default vs.
  on demand?* Pick the collapsed-by-default vs. expanded default, and the line
  cap, from this — not from the data's size. (OpenCode's `collapseToolOutput`,
  reasoning-collapsed-by-default.)
- **L4 — Restraint (H4).** *Does any motion/color here communicate a state
  change, or is it decoration?* Decoration is cut. One looping glyph, ≤250ms
  transitions, monochrome-survivable status. This lens is where we **reject**
  parts of OpenCode (its Knight-Rider gradient spinner, §6.4).

> **Worked example (the contested one):** "Where does a tool call render?"
> L1: kind = tool (engine emits start/progress/end). L2: a *completed* tool call
> is a permanent record of what the agent did → transcript; a *running* tool is a
> transient view → pinned zone. **The lens reveals the tension isn't binary —
> it's a lifecycle.** See §4.

### 3.3 The hierarchy ladder (how blocks separate, cheaply)

OpenCode's "clean" look comes from a strict, cheap separation ladder — **no full
boxes**. We adopt the same ladder as policy:

```
strongest  ─ left-edge bar (┃, colored)      → a whole authored turn (user msg)
           ─ 1 blank line + left indent (2–3) → a part within a turn
           ─ background shift (panel/element) → interactive/hover/nested state
weakest    ─ muted color + weight             → metadata, timestamps, rationale
```

> **P2 — Separate blocks with the *weakest* device that still reads.** Indent
> before border; border before box; box ~never. A full bordered box per message
> is the "AI slop" look we're avoiding.

### 3.4 Streaming is a first-class state, not an afterthought

> **P3 — Every block kind must define its *streaming* appearance, not just its
> settled one.** OpenCode passes `streaming={true}` into markdown/code/diff and
> shows reasoning with a live spinner-titled header that *settles* into a
> collapsed `+ Thought… · 1.2s` line. Our `AnswerStream` already streams prose;
> the methodology extends the obligation to **every** part kind.

---

## 4. Worked example #1 — the tools tension, resolved by lens (not decree)

This is the decision the brainstorm explicitly asked to handle "by how we think."

**The conflict.** OpenCode renders tool calls **inline** as message parts
(`ToolPart` → `InlineToolRow` / `BlockTool`), interleaved with prose, collapsible
([`session/index.tsx` ToolPart ~1701-2032]). Our committed **principle #7** says
the opposite: *transcript = responses only; tool activity is pinned + transient
in `ActivityRegion`, never inline.*

**Apply L2 (persistence) — the tension dissolves into a lifecycle:**

| Tool lifecycle phase | L2 says | Surface |
|---|---|---|
| queued / running / streaming output | **transient** ("happening now") | **Pinned `ActivityRegion`** (today's behavior — *correct*, keep) |
| completed / failed (settled) | **permanent record** ("what the agent did") | **A transcript part** — a collapsed `ToolResultBlock` left in scrollback |

So the honest synthesis is **neither** "OpenCode is right" **nor** "principle #7
is right." It is:

> **Proposal T (for the team to ratify):** tools render **transient while live**
> (pinned, as now) and **settle into a collapsed result block in the transcript
> when done** — a one-line `✓ Edit api.py · 3 hunks` part, click/`enter` to
> expand (OpenCode's `BlockTool` collapse model, §6.3). **[corrected — call it
> what it is]** This **amends** principle #7, whose committed text is unambiguous:
> "Transcript = user messages + agent responses **only**" and tool activity is
> "**never** in the transcript" (`components.md:28,151`). Proposal T's new rule —
> "the transcript holds responses **and settled tool records**; only *in-flight*
> activity is pinned" — is a genuine reversal of "never," not a clarification.
> Worth doing (below), but the team is amending a committed decision, eyes open.

This keeps the conversation clean **while it's happening** (the original #7 win)
and gives the user **durable history** (the OpenCode win) — and it fell out of a
lens, which is the point. **Left as an open question (§7) only because it amends a
committed principle and deserves the team's explicit sign-off — the methodology
produced the answer; ratification is theirs.**

---

## 5. OpenCode ground truth — the part model & block anatomy

All citations: `packages/tui/src/` in the clone. Line ranges are approximate to
the read.

### 5.1 A message is a list of parts (`routes/session/index.tsx`)

- Conversation is a `<scrollbox stickyStart="bottom" flexGrow={1}>` with a
  leading `<box height={1}/>` spacer; `<For each={messages()}>` switches on
  `message.role` → `<UserMessage>` / `<AssistantMessage>` (~1186-1230).
- **`AssistantMessage`** (~1449-1560) is **borderless**; it maps over the
  message's **parts** via a `PART_MAPPING` and renders each as a sibling block:
  `TextPart`, `ToolPart`, `ReasoningPart`, then a metadata footer
  `▣ {mode} · {model} · {duration}`.
- **[corrected]** *Prose and reasoning* parts are wrapped
  `<box paddingLeft={3} marginTop={1} flexShrink={0}>` (`TextPart` ~1684,
  `ReasoningPart` ~1601) and registered with `alwaysSeparate` (≥1 blank line
  before). Tool parts differ — `InlineToolRow` uses `paddingLeft={3}` with a
  *dynamic* sibling margin (~1924) and `BlockTool` uses `paddingLeft={2}` (~1996).
  So the indent isn't uniform, but the **pattern** holds: **a small left indent +
  a 1-line top margin per part** is the core "blocks" lever — parts are indented
  and breathing-spaced, not boxed. (Not literally "every part identical"; not "the
  entire secret.")

### 5.2 User vs assistant distinction (`UserMessage`, ~1350-1420)

- **User** = `<box border={["left"]} customBorderChars={SplitBorder} borderColor={agentColor} marginTop={1}>` wrapping `<box paddingTop=1 paddingBottom=1 paddingLeft=2 backgroundColor={hover? backgroundElement : backgroundPanel}>`. A **left `┃` bar in the agent's color**, padded, faint hover bg.
- **Assistant** = no border, parts indented `3`. Authorship is shown by
  *structure* (bar vs. indent), not a "User:" / "Assistant:" label.

### 5.3 Tool parts — two densities (`ToolPart` ~1701-2032)

- `ToolPart` dispatches on tool type to specialized renderers (`Shell`, `Edit`,
  `Read`, `Grep`, … else `GenericTool`).
- **`InlineToolRow`** (~1903-1982): compact one-liner — `<box flexDirection="row">`
  with an icon cell `<text width={ICON_WIDTH} fg={iconColor}>{icon}</text>` + a
  flex-grow content `<text>`. Pending shows `~ {label}`; done/failed shows
  icon+text; denied gets **strikethrough**.
- **`BlockTool`** (~1983-2032): `<box border={["left"]} paddingLeft={2}
  paddingTop=1 paddingBottom=1 marginTop={1} borderColor={theme.background}
  backgroundColor={hover? backgroundMenu : backgroundPanel}>` — the expandable
  output container. Output **collapsed by default**, click-to-expand.
- **Output capping:** `util/collapse-tool-output.ts` → `{ output, overflow }`,
  truncates by **max lines first, then max chars**, appends `…`. **[corrected]**
  We *do* have a per-subtype cap, but in `tool_call_row.py:cap_body` (not
  `task_tree.py`, which only joins labels), and it is **lines-only** — no char cap
  (`tool_call_row.py:17-25`). So adopting OpenCode here means *adding* the
  lines-**then**-chars step, not "same idea, already done."

### 5.4 Reasoning / thinking part (`ReasoningPart` ~1571-1677, `context/thinking.ts`)

- **Live:** `<Spinner color={fg()}>{`Thinking: ${summary}`}</Spinner>`.
- **Settled:** collapses to `+ Thought: {title} · {duration}` (click to expand
  in "hide" mode; always-open in "show" mode).
- Body = markdown at `syntaxStyle="subtle"`, color = `warning × thinkingOpacity`
  (a **theme-level alpha** dedicated to de-emphasizing reasoning).

### 5.5 Markdown / code / diff are first-class primitives

- `TextPart` (~1678-1700): `<markdown streaming content fg={markdownText}
  bg={background} tableOptions={{style:"grid"}} internalBlockMode="top-level">`.
- Code: `<code filetype="shell" syntaxStyle streaming content={output}/>`.
- Diff: `<diff view="split|unified" filetype syntaxStyle showLineNumbers/>` with
  add/remove **background** colors. **+/- is carried by bg + line numbers**, not
  just glyphs.

### 5.6 Borders & spinner (`ui/border.ts`, `ui/spinner.ts`)

- **`SplitBorder`**: an `EmptyBorder` (all chars `""`, horizontal `" "`) with
  **only** `vertical: "┃"` (U+2503 heavy vertical). The whole "block" vocabulary
  is *one heavy vertical bar*. Directly portable to Textual.
- **`spinner.ts`**: a "Knight Rider" bidirectional scanner — a red gradient trail
  (`#ff0000`→`#440000`), `holdStart=30/holdEnd=9` frames, block/diamond glyphs.
  **We will NOT adopt this** (§6.4): it violates H4 (one restrained glyph).

### 5.7 Theme shape (`context/theme.tsx`, `theme/`)

Flat semantic `Theme` of `RGBA`s actually consumed in terminal rendering:
`text`, `textMuted`, `primary/secondary/accent`, `error/warning/success/info`,
`background / backgroundPanel / backgroundElement / backgroundMenu`,
`borderActive`, `markdownText`, a **full `syntax*` set**
(`syntaxComment/Keyword/String/Number/Function/Type/Variable/Operator/Punctuation/Builtin`),
diff add/remove backgrounds, and a scalar **`thinkingOpacity`**. **[corrected]**
~30+ themes ship **bundled** (`DEFAULT_THEMES`, `theme/index.ts:130`), with
plugin/custom/system themes layered on at runtime; "40+" counted those runtime
additions. Either way the point stands: a theme is **this one flat object**,
swapped wholesale.

### 5.8 Spacing philosophy (the "clean" feel, mechanically)

- **1 blank line** between messages (`marginTop={index===0?0:1}`) and between
  parts (`marginTop={1}`).
- **`alwaysSeparate`** set → minimum 1-line gap before messages, reasoning, tool
  blocks, errors. (We'd model this as bottom-margin policy in TCSS.)
- Hierarchy by **indent (2–3) + muted color**, not rules or boxes. Metadata
  (model, duration, timestamps) always `textMuted`.

---

## 6. Translation to our Textual TUI

### 6.1 Primitive mapping (OpenTUI → Textual/TCSS)

| OpenTUI | Textual / TCSS | Note |
|---|---|---|
| `<box border={["left"]} customBorderChars={vertical:"┃"}>` | `border-left: heavy $accent;` on a `Container` | We already use accent left-bars (`.user-msg`, compose box). |
| `paddingLeft={3}` / `paddingTop={1}` | `padding: 1 0 0 3;` | Part indent = our "blocks" lever. |
| `marginTop={1}` + `alwaysSeparate` | `margin-top: 1;` policy on part widgets | Vertical rhythm. |
| `flexDirection="row" gap={1}` | `layout: horizontal;` + spacing | Inline tool row. |
| `backgroundColor={hover?A:B}` | `:hover` is weak in terminals → prefer **focus/expanded** state bg, not mouse-hover | L4: don't lean on hover. |
| `<markdown streaming>` | existing `AnswerStream` `Markdown` widget | Keep; wrap per-part. |
| `<code filetype syntaxStyle>` | Rich `Syntax` renderable inside a `Static` | Exists in Rich; needs syntax tokens (§6.2). |
| `<diff view>` | **custom widget** (Rich has **no `rich.diff`**) | **[corrected] Pilot risk** — must be built from `Syntax` + diff bg tokens; not a ready primitive. Verify in Textual before scoping. |
| `Spinner` (gradient scanner) | **our single `ActivityGlyph`** | Reject OpenCode's; keep ours (L4). |
| `collapseToolOutput` | extend `ToolCallRow.cap_body` | **[corrected]** `cap_body` exists but is **lines-only** (`tool_call_row.py:17`); adopting OpenCode = *add* the char cap. |

### 6.2 Token expansion — **recommended, additive** (per the palette decision)

The locked palette (5 brand + green/amber) cannot render code or diffs legibly.
OpenCode proves a **syntax + diff sub-palette** is unavoidable for this surface.
**Proposal:** add an additive, sanctioned **product sub-palette** to `theme.py`
(documented like green/amber were in §4.1 of the design spec), derived from the
brand hues where possible so it stays on-brand:

```
# Syntax (code blocks) — tuned toward brand blue/grey, not a rainbow
syntax-comment      muted-deep (#5B6577)        syntax-keyword   accent (#286CE9)
syntax-string       success-ish (#7ee787 dimmed) syntax-number   #9DB8E8 (code)
syntax-type         #9DB8E8                       syntax-func      fg (#E3E3E3)
syntax-punctuation  muted (#8690A3)

# Diff
diff-add-fg   fg        diff-add-bg   (success #7ee787 @ ~12%)
diff-del-fg   fg        diff-del-bg   (error   #E02F07 @ ~12%)

# Reasoning de-emphasis
thinking-opacity   ~0.7   (a scalar, applied to muted reasoning text)
```

> **Principle for the expansion (P4):** *the brand palette governs chrome; a
> documented, restrained sub-palette governs content (code/diff/reasoning).* Keep
> it few and brand-derived — this is the opposite of OpenCode's 40-theme
> maximalism, on purpose (L4).

### 6.3 The blocks answer — proposed catalog additions (group B)

> **[corrected] Prerequisite, not a free add.** These widgets are dumb+reactive,
> but they read a **per-turn ordered list of typed parts that the reducer does not
> emit today** (§3.1, §8). The gating work is engine/reducer-side — extend
> `render.py`'s kinds (split fenced-code / diff out of `message`; surface
> `thought`), add an **ordered `parts` field** to `AgentSnapshot`, and add the
> missing reducer cases. **Only after that** are the widgets below "just catalog
> additions." This is the seam the first draft wrongly called "no new
> architectural seam" — see §8.

Concrete new/changed catalog entries, all dumb+reactive, reading a part slice
**(once the part model above exists)**:

- **`MessagePartList`** *(new, the structural fix)* — the assistant message as an
  **ordered list of part widgets** instead of one `Markdown`. Reads the turn's
  parts in order; renders each part widget with the indent+rhythm policy (§3.3).
  This is the single change that turns "thrown-out text" into "blocks" — **and it
  is blocked on the ordered-`parts` field above.**
- **`TextBlock`** *(wraps existing `AnswerStream`)* — a prose part. Still the
  streaming `Markdown` widget; now one *part among siblings*, indented, with its
  own top-margin. **`AnswerStream` stays unchanged internally** (honors the
  catalog's "do not replace") — we just stop letting it own the whole turn.
- **`CodeBlock`** *(new)* — a fenced code part: Rich `Syntax` on the §6.2 tokens,
  a faint left-bar or muted language label, line cap + "expand". Distinct from
  prose so code stops bleeding into paragraphs.
- **`DiffBlock`** *(new)* — add/remove via §6.2 diff bg + `+`/`-` gutter + line
  numbers (OpenCode §5.5). Used by edit-tool results.
- **`ReasoningBlock`** *(new)* — collapsible thinking part: live
  `◐ Thinking… {summary}` → settles to `+ Thought · {duration}` (muted ×
  thinking-opacity). Matches OpenCode §5.4; reuses our one `ActivityGlyph`.
- **`ToolResultBlock`** *(new — the §4 synthesis)* — a **settled** tool call as a
  collapsed transcript part (`✓ Edit api.py · 3 hunks`, expand → `DiffBlock` /
  capped output). The transcript half of Proposal T.

`ActivityStatus`, `TaskTree`, `ToolCallRow`, `ActivityRegion` (group C) stay as
the **in-flight** half — unchanged. The split is exactly L2.

### 6.4 What we explicitly do NOT adopt (and why)

- **The gradient "Knight Rider" spinner** — violates H4/L4 (one restrained
  looping glyph). Keep `ActivityGlyph`.
- **Mouse-hover backgrounds as primary affordance** — unreliable across
  terminals; use focus/expanded state instead.
- **40-theme system** — out of scope and anti-brand; one DoneDone theme + the
  §6.2 content sub-palette.
- **Web-app CSS mechanisms** (Shiki, `light-dark()`, data-attr selectors,
  opacity reveals) — intent-only, not portable.
- **Role labels ("Assistant:")** — OpenCode shows authorship by structure; so do
  we (left-bar vs indent). Don't add labels.

---

## 7. Open questions for the refinement team

Each is framed *with the lens that bears on it*, so the team decides the way the
methodology intends.

1. **Ratify Proposal T?** (§4) — tools settle into a collapsed `ToolResultBlock`
   in the transcript, refining principle #7 to "responses **and settled
   records**; only in-flight activity is pinned." *Lens: L2.* This is the one
   committed-decision amendment and needs explicit sign-off.
2. **Code-block chrome:** faint left-bar, muted language label, or background
   tint to delimit code from prose? *Lens: L4 (weakest device that reads).*
   Recommend left-bar + language label, no full box.
3. **Reasoning default state:** collapsed-by-default (OpenCode "hide") or shown?
   *Lens: L3.* Recommend collapsed, `+ Thought · {duration}`, expandable.
4. **Syntax sub-palette scope:** ship the full §6.2 set, or a minimal
   keyword/string/comment/diff subset first? *Lens: L4.* Recommend the minimal
   subset first; expand only if code legibility demands.
5. **Streaming appearance of `CodeBlock`/`DiffBlock`:** render incrementally
   (OpenCode `streaming=true`) or buffer until the fence closes? *Lens: P3.*
   Tradeoff: live feel vs. re-highlight flicker. Needs a pilot.
6. **[corrected] Part-model build (the gate, not a question):** the reducer does
   **not** expose per-turn ordered parts today (`AgentSnapshot` has no `parts`
   field; `state.reduce` has no `thought` case — `state.py:60-69,178-190`). So
   this is **scoped work, not a yes/no**: extend `render.py` kinds, add an ordered
   `parts` field + reducer cases, then build `MessagePartList`. *Lens: P1.* Decide
   *how much* of the part model to build first (e.g. text+code+reasoning before
   diff). Everything in §6.3 is downstream of this.

---

## 8. Validation the refinement team should do before building

Per the project's debugging rule (validate against the running app, not theory):

- **[corrected] Build the part model first — it does not exist.** Verified
  against live code: `render.py:15` kinds are coarse
  (`message|thought|user|tool|tool_update`, no code/diff split), `state.reduce`
  has **no `thought` case** and `AgentSnapshot` has **no ordered `parts` field**
  (`state.py:60-69,178-190`). `MessagePartList` and every block in §6.3 depend on
  adding that; it is the **first** task, not a precondition to "confirm." Note the
  one thing that *does* help: `app.py` already breaks the Markdown stream at
  in-turn boundaries (`_end_stream(boundary=True)`, `app.py:635`), so the coarse
  prose-run split is a partial head start.
- **Pilot the rhythm on a real transcript** — indent+1-line-margin parts — before
  committing tokens, to confirm "blocks" reads in our terminal at real width.
- **Snapshot the new blocks** (idle/streaming/settled) like the existing
  `test_tui_*` suite, since each is a dumb widget over a snapshot slice.
- **Re-read OpenCode line ranges against live source** before quoting them as
  fact — they were read at one commit and the tree moves fast.

---

## 9. Summary

- **Deliverable is a methodology, not a paint job.** §3's part-model (P1), four
  lenses (L1–L4), hierarchy ladder (P2), and streaming-is-a-state rule (P3) are
  the reusable "how we think" the brainstorm asked for.
- **The "blocks" fix is structural:** render a turn as an ordered list of typed
  **parts** (`MessagePartList`), not one Markdown blob. Everything legible about
  OpenCode follows from that one decision.
- **OpenCode is a fair reference** because it's now an OpenTUI **cell-grid**
  renderer, not a Go TUI and not a web app — its patterns (left-`┃` bar, indent
  rhythm, inline-collapsible tools, first-class code/diff, flat semantic theme,
  `thinkingOpacity`) port almost directly.
- **The tools tension is resolved by lens L2 into a lifecycle** (transient while
  live → settled record in transcript), offered as **Proposal T** for the team to
  ratify rather than decreed.
- **A small, brand-derived syntax/diff sub-palette is recommended** (§6.2) —
  additive and documented like green/amber, the antithesis of OpenCode's
  40-theme maximalism.
- **Six new/changed catalog entries** (`MessagePartList`, `TextBlock`,
  `CodeBlock`, `DiffBlock`, `ReasoningBlock`, `ToolResultBlock`) express it
  through the existing tokens and catalog — **but on top of one real new seam**
  *(corrected from the first draft)*: a per-turn **ordered typed-part model** in
  `render.py`/`state.py` that does not exist today. That part model is the gating
  task (§8); the widgets are downstream of it. **Two committed-decision amendments
  need sign-off:** principle #7 (Proposal T, §4) and the palette (syntax/diff
  sub-palette, §6.2).
