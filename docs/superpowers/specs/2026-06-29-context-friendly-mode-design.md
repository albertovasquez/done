# Compress-Aware Mode — Design

**Date:** 2026-06-29
**Status:** Design approved → Phase 1 tracked in #186
**Issue:** https://github.com/albertovasquez/done/issues/186 (Phase 1)
**Branch:** `context-friendly-mode`
**Mode name:** `compress-aware` (user-facing mode, `done.conf` setting, `dn`
subcommand, and footer chip all use this name).

## Goal

Cut the per-turn token tax of prose context files (soul / identity / user /
MEMORY.md / AGENTS.md / CLAUDE.md) by loading pre-compressed **shadow copies**,
without ever mutating the human-facing originals, without any on-the-fly
compression on the hot path, and with a clean opt-out.

**The key distinction is input vs output.** Compression applies to what we
*send into* the model (input tokens). It must **never** change how the main
agent *responds* (output) — the agent's personality is the product's core
differentiator and must come through in full voice. See "Input vs output"
below. (Sub-agents are the exception on the output side — their responses may
be caveman, since no user reads them.)

Done is an opinionated system: this mode is **ON by default**. Users can turn it
off (live) and pin the preference.

## Non-goals

- **No runtime / in-flight compression.** We never compress context as it is
  assembled into a prompt. All compression is offline. (Explicitly rejected
  during brainstorming: per-turn LLM compression adds latency to the
  two-process hot path and can silently corrupt agent behavior.)
- **No mutation of human-authored files.** Soul / identity / user / AGENTS.md /
  CLAUDE.md originals are never overwritten. (Memory is the one exception — see
  below.)
- **No compression of the main agent's response.** Compression is input-only.
  The main agent's output must always be full personality — never caveman.

## Input vs output (the core distinction)

The `caveman-compress` engine has two separable effects, and conflating them is
a trap:

1. **Input compression** — it shrinks the *text we send into* the model (fewer
   input tokens). This is what we want everywhere.
2. **Output style** — the caveman skill can also make the *model respond* in
   caveman talk (terse output). For the **main agent this is forbidden** — the
   user reads the response and the personality must come through fully.

**These do not have to travel together.** Compressing a soul/identity file on
the *input* side does not, on its own, force a caveman *response*: much of the
personality is the *meaning* of the identity, not its prose verbosity. So we
compress soul / identity / user when **sending** them into context, while the
agent still responds in full voice.

**Caveat (Codex #4): voice can live in the prose, not just the meaning.**
Identity files often encode voice through *style exemplars, cadence,
forbidden-phrasing lists, and deliberate repetition* — not just facts. A
compression that preserves the facts but deletes the example responses or the
"never say X" list can flatten the voice with no explicit output instruction.
**Mitigation:** voice-bearing files use a **stricter compression profile** that
treats example/style blocks and forbidden-phrasing lists as preserve-exactly
regions (like code blocks). This narrows what gets compressed in voice files to
genuinely redundant prose.

- **Main agent:** compress input (soul / identity / user / MEMORY / AGENTS /
  CLAUDE). Never touch the response style.
- **Sub-agents:** compress input **and** let the response be caveman — no user
  reads a sub-agent's output (it is returned to the controller as a tool
  result), so output style is free. See "Sub-agent compression".

### Voice bleed — watch, don't gate

The load-bearing assumption is that **a compressed identity does not bleed into
the response style.** We ship voice-file input compression **ON** rather than
blocking on a formal proof, for two reasons:

1. **Prior signal it compartmentalizes.** Heavy `caveman-review` use (~36 runs,
   no issues) shows the model handles caveman text in one part of the prompt
   without it leaking into unrelated output. This is *suggestive, not
   identical* evidence — `caveman-review` is a scoped per-task invocation,
   whereas a compressed identity is the always-on persona prefix. So it lowers
   the risk; it does not eliminate it.
2. **Safe to be wrong.** The design makes bleed cheap to detect and revert:
   - it is **input-only** (never touches response generation directly);
   - the off-ramp is **one step**: delete the voice-file sibling(s) → verbatim
     identity returns, no code change; or flip the toggle off entirely.

**Honest caveat (Codex #5):** "observable and instantly reverted" is weaker than
it sounds. Flattening may be *gradual, persona-specific, or task-specific*, so it
can run for many turns before anyone notices — and any memory written during
those turns is already compressed (no verbose original; see Memory writes). So
detection is not guaranteed-immediate.

**Posture:** ship ON, but with two guardrails that make "ship and watch"
honest rather than hopeful:
- voice files use the **stricter profile** above (preserve style/example blocks);
- a small set of **pre-ship regression prompts** per representative persona is
  run once (full vs compressed identity) so we are not relying solely on
  noticing drift in production.
A formal A/B remains available as an ongoing confirmation tool. None of this is a
hard ship *gate* — but it is no longer "ship blind."

## The shadow-file pattern

For any `FOO.md`, an optional sibling `FOO.compressed.md`.

- Each compressed file carries a header comment (the header is **metadata only**
  and is **stripped before prompt assembly** — see "Header is not sent"):
  - `source-sha256 <hash>` — sha256 of the **source file's bytes** (never the
    compressed file's). Source of truth for source-drift.
  - `engine-version <ver>` + `rules-sha256 <hash>` — identifies the compressor
    and its style rules at build time (see "Freshness key").
  - `body-sha256 <hash>` — sha256 of the compressed **body** (everything below
    the header). Detects hand-edited / tampered siblings (Codex #7).
  - a human-readable build date.
  - a notice: "generated by Done — do not edit by hand".
- **Loader rule (one chokepoint):** when compress-aware mode is ON *and* a
  **fresh** sibling exists → load the sibling (header stripped). Otherwise →
  load the original.
- **Presence = opt-in; absence = normal behavior.** Deleting a sibling instantly
  reverts that file to its original. The feature fails safe.

### Freshness — lazy + fallback

A sibling is **fresh** only when **all** of these match; any mismatch → stale:

1. `source-sha256` == sha256 of the current source bytes (source unchanged), AND
2. `engine-version` + `rules-sha256` == the currently-vendored engine/rules
   (so improving the compression rules invalidates every old sibling — Codex
   #3), AND
3. `body-sha256` == sha256 of the sibling's current body (the sibling has not
   been hand-edited — Codex #7).

- **All match → load compressed.**
- **Any mismatch → load the original** (degrade to the truthful source, never
  serve stale context) and mark the pair for rebuild. A hand-edited body is
  surfaced as a warning by `dn compress --status` rather than silently trusted.
- Hash-based (not mtime): immune to git checkouts, `touch`, file copies, and
  clock skew. The date is for humans; the hashes are authoritative.

### Header is not sent (net-savings correctness — Codex #8)

The metadata header (hashes, version, date, notice) is **stripped before the
compressed body is assembled into the prompt.** Only the compressed body counts
against the prompt. Without this, the header could erase savings on short files
or even make compressed context larger. The whole-prefix comparison tool (not
per-file body math) is the source of truth for *net* savings.

### Regeneration — offline only

- `dn compress` (re)builds stale or missing siblings for the configured target
  files.
- Optional daily cron calls the same code path.
- **Never on the hot path, never per-turn.** Cron is an optimization, not a
  correctness dependency — staleness already degrades to loading the original.
- **Atomic writes (Codex #6):** siblings are written to a temp file, fsync'd,
  then atomically renamed into place — never header-then-body in place. A turn
  starting mid-rebuild therefore sees either the old complete sibling or the new
  complete one, never a truncated file. A per-source advisory lock serializes
  concurrent `dn compress` / cron / memory-write paths.
- **Path safety (Codex #12):** sibling paths are derived by canonicalizing the
  source path and appending `.compressed.md`; siblings are resolved only within
  the same trusted root as their source. Symlinked `.compressed.md` files and
  siblings resolving outside the source's tree are rejected (prevents hidden
  prompt injection). A `.compressed.md` with no matching source is ignored.

## Compression engine — vendored into the harness

The engine is the `caveman-compress` Python scripts
(`detect → compress via Claude → validate → retry`) plus the `caveman` style
rules. **These are vendored (copied) into the harness as a bundled internal
feature** — the `ask-done` pattern — not called from the global
`~/.agents/skills/` install. The harness owns the code; there is **no external
skill dependency** at runtime.

Consequence (deferred cleanup): once this feature ships with the engine
vendored, the standalone global skills `caveman`, `caveman-compress`, and
`caveman-commit` can be removed from `~/.agents/skills/` (symlink + target).
`caveman-review` stays — it is the user's actively-used review tool, unrelated
to this engine. See "Skill cleanup (post-implementation)".

The engine is used for **input compression only** — it rewrites *files we send
into context*, never the main agent's response. Its exact-preservation rules
make it safe even on voice-bearing files (it keeps the substance; see "Voice
bleed — watch, don't gate" under "Input vs output"). Its rules already:

- preserve code blocks, inline code, URLs, file paths, commands, env vars,
  dates, version numbers, and proper nouns **exactly**;
- preserve markdown structure (headings, lists, tables, frontmatter);
- validate output and retry up to 2× on failure, leaving the source untouched
  if it cannot produce valid output.

That validation pass is our safety net (see Memory writes).

## Memory writes — the one destructive exception (documented tradeoff)

Memory files are **agent-authored**, not human-authored — there is no precious
hand-crafted original to protect. Therefore memory is compressed **at write
time**: when the agent writes memory, the compressed form is what persists; no
verbose original is kept.

- **This is the only irreversible spot in the system.**
- **Accepted risk 1 — silent semantic loss (Codex #1).** Validation only catches
  *malformed* output or *exact-token* drops (numbers, links, code, paths). It
  **cannot** catch a semantically load-bearing *clause* being dropped while the
  output stays valid markdown — e.g. compressing "Set `HARNESS_DEBUG`, but never
  in CI" down to "Set `HARNESS_DEBUG`" preserves the token and passes validation,
  yet inverts the meaning. With no verbose original, that loss is unrecoverable.
  The `--status`/error fallbacks do **not** help here because the compression did
  not *fail*. **The user has chosen to accept this risk** in exchange for
  born-compact memory and no regen step.
- **Accepted risk 2 — exempt from the "OFF is inert" guarantee (Codex #2).**
  Because memory is persisted compressed, turning compress-aware mode OFF
  **cannot** restore a verbose `MEMORY.md` — there is none. Memory is therefore
  an explicit, documented **carve-out** from the inert guarantee stated under
  "Toggle". OFF reverts *file loading* for all other files; it does not
  un-compress already-written memory.
- **Mitigation (partial, accepted):** the engine's validation pass + exact-
  preservation rules reduce (do not eliminate) loss. No backup is kept, per the
  destructive-at-write decision.
- This is a **conscious, documented decision** — both risks were surfaced by
  adversarial review and explicitly accepted, recorded here so they are not
  mistaken for oversights.

## Toggle — YOLO-style chip (live-vs-pin contract)

Model the toggle on the existing YOLO yellow footer chip (PR #33):

- **Default ON** (opinionated default).
- **Clickable footer chip** (label: `compress-aware`) toggles the mode **live**
  for the session.
- **Pinnable** to `done.conf` to persist the default.
- **Contract (inherited from YOLO):** a click toggles the live value but **never
  persists**; persisting is a deliberate pin action. (See memory note
  `yolo-persist-chip-merged` — "click never persists" is a known trap.)
- The `done.conf` flag (`compress_aware`) is the **pinned** value; the chip is
  its **live** override.

When the mode is OFF, originals always load and the **file-loading** feature is
inert. **Exception:** destructive memory writes are *not* reversed by OFF — see
"Memory writes" (a documented, accepted carve-out from the inert guarantee).

## Sub-agent compression (separate runtime mechanism)

Sub-agents are special: **no user ever reads a sub-agent's output** — its final
message is returned to the controller as a tool result, never shown to a human.
So for sub-agents both sides are free to compress:

- **Input side:** the same shadow-file input compression applies to whatever
  context a sub-agent is dispatched with.
- **Output side (the new part):** sub-agents may **respond in caveman talk.**
  Because only the controller parses the return (and relays in its own voice),
  there is zero personality risk and a direct cut to the controller's context,
  since sub-agent returns land straight in it.

**Implementation — prompt-level, not post-process.** Instruct the sub-agent, in
its dispatch prompt, to write its final return in compressed/caveman style. The
agent produces compact output natively — **no extra LLM round-trip, no hot-path
post-processing.** This is the cheap, safe version. (Rejected alternative:
returning normally then running output through the engine — adds a hot-path LLM
call, the very thing this design avoids.)

**Preserve required evidence (Codex #10) — scope: sub-agents only.** This rule
does **not** apply to the main agent (whose response is never compressed at all,
so there is no evidence to protect). It exists *only* because sub-agent output
*is* compressed.

It is **not a new mechanism — just extra text appended to the sub-agent's
dispatch prompt**, riding along with the caveman-return instruction itself. No
post-processor, no validator, no file. The added instruction says: compress the
*prose padding*, but keep load-bearing content **verbatim** — file paths, line
numbers, code snippets, error text, citations, and any structured fields the
controller contract expects. Caveman style applies to the connective prose around
that evidence, never to the evidence itself. (Mirrors the engine's
exact-preservation rules, expressed as a prompt instruction rather than file
rules.)

Cost/benefit: a few extra instruction tokens *out* to the sub-agent, dwarfed by
the tokens saved on the compressed return coming *back*. Because the risk is
real, caveman returns are **opt-in per sub-agent type**, not a blanket default —
a reviewer/explorer that returns findings keeps full evidence; only genuinely
prose-heavy returns are compressed.

**Degrades safely:** if the sub-agent ignores the instruction, you simply get
normal verbose output — correct, just larger. No failure mode.

**Distinct from shadow-files:** this is a *runtime* mechanism (live dispatch
prompt), not the offline shadow-file system. It shares the "caveman is fine when
no human reads it" principle but none of the file/sibling/freshness machinery.

## Status / measurement tools

Two distinct tools — kept separate because they measure different things:

1. **`dn compress --status` (Phase 1)** — static, cheap, no model call. Walks
   the sibling pairs and reports per-file and total byte/token delta
   ("AGENTS.md 980→410, −58% … total saved/turn: X") plus a list of stale pairs.
   Measures files **in isolation** (sum of per-file savings).

2. **On/off prefix-comparison tool (Phase 2/3)** — assembles the **real composed
   prompt prefix both ways** (compressed-off vs compressed-on) across all
   input-compressible context (soul / identity / user / AGENTS.md / CLAUDE.md)
   and reports the true end-to-end per-turn token delta a user would actually
   see. Heavier; this is the honest "should I turn it on?" number.
   **Excludes memory** (destructive-at-write, so there is no "off" version to
   compare against — an on/off diff is undefined for it).

## Components (isolated units)

0. **Vendored engine** — the `caveman-compress` scripts + `caveman` style rules
   copied into the harness (the `ask-done` bundled-feature pattern). No runtime
   dependency on `~/.agents/skills/`. This is the foundation the Compressor
   wraps.
1. **Compressor** — wraps the vendored engine; prose-in →
   validated-compressed-out. One clear job; testable against fixture files.
2. **Sibling I/O** — header read/write, sha256 of source, freshness verdict
   (`fresh | stale | missing`). No LLM, no network. Pure + testable.
3. **Loader hook** — at the context-assembly chokepoint, decides
   sibling-vs-original **per file**. Read-only, no LLM. Honors the mode flag.
4. **`dn compress` CLI** — `--status` (report) and rebuild (default action).
5. **Memory write path** — compress-at-write integration for memory files only.
6. **Setting + chip** — `done.conf` `compress_aware` flag (pinned value) read
   once at the loader chokepoint; YOLO-style footer chip for the live override.

## Data flow

```
Authoring / cron:
  source FOO.md --[dn compress]--> compressor (vendored caveman engine)
                                  --> validate --> FOO.compressed.md (+ header: sha256, date)

Memory write (destructive):
  agent writes memory --> compressor --> validate --> persisted compressed memory

Per turn (read-only, no LLM) — INPUT side only, never touches response style:
  context chokepoint --> for each target file (soul/identity/user/MEMORY/AGENTS/CLAUDE):
      mode OFF? --> load original
      sibling missing? --> load original
      stale? (source-hash OR engine/rules-version OR body-hash mismatch)
              --> load original, mark for rebuild
      fresh? (all three match) --> load FOO.compressed.md (header stripped)
```

## Error handling

- **Compression fails / invalid output:** leave the source untouched, do not
  write a sibling (engine already retries 2×). For memory writes, fall back to
  writing the uncompressed form rather than losing content.
- **Corrupt / unreadable sibling header:** treat as stale → load original, mark
  for rebuild.
- **Mode flag missing from `done.conf`:** default to ON (opinionated default).

## Testing

- **Sibling I/O:** unit tests for header round-trip; 3-part freshness verdict —
  stale on source-hash mismatch, on engine/rules-version mismatch (Codex #3), and
  on body-hash mismatch / hand-edit (Codex #7); corrupt-header → stale.
- **Header stripping (Codex #8):** assert the metadata header is removed before
  the body is handed to prompt assembly.
- **Atomic write (Codex #6):** assert temp-then-rename; a reader during rebuild
  never sees a partial sibling.
- **Path safety (Codex #12):** assert siblings outside the source root and
  symlinked `.compressed.md` are rejected; orphan sibling (no source) ignored.
- **Loader hook:** table-driven tests over (mode on/off × sibling
  missing/fresh/stale) → asserts which file is loaded; assert **no LLM call** on
  the read path.
- **Compressor:** fixture-based — feed prose with code blocks / URLs / version
  numbers / `[[links]]`, assert exact preservation; assert validation rejects
  malformed output.
- **`dn compress --status`:** asserts delta math and stale detection on a
  fixture tree.
- **Memory write:** asserts compressed-at-write; asserts fallback-to-verbose on
  compression failure (no content loss).
- **Voice-bleed check (confirmation, not a gate):** optional A/B harness — same
  prompt with full vs compressed identity; eyeball that response personality is
  preserved. Available to confirm/diagnose if bleed is ever suspected; does not
  block shipping.
- **Sub-agent caveman return:** asserts the dispatch prompt carries the
  compressed-return instruction; asserts a verbose return is still accepted
  (graceful degradation).

## Phasing

- **Phase 1:** shadow-file pattern + loader hook + freshness + `dn compress` /
  `--status` + memory-write integration + the YOLO-style chip & `done.conf`
  flag. Targets (input compression): **soul / identity / user / `MEMORY.md` /
  `AGENTS.md` / `CLAUDE.md`** — the per-turn prefix files where the compounding
  token tax lives. Voice-file compression ships **ON**; bleed is watched for in
  real responses and reverted per-file if observed (not a pre-ship gate).
- **Phase 2/3:** `SKILL.md` bodies (same mechanism, lower ROI — loaded only on
  invocation); the on/off whole-prefix comparison tool (excludes memory); and
  **sub-agent caveman returns** (prompt-level — distinct runtime mechanism; can
  ship independently of the shadow-file work).

## Skill cleanup (post-implementation)

Once the engine is vendored into the harness and Phase 1 has shipped, the
standalone global caveman skills become redundant and are removed:

- `~/.agents/skills/caveman` — vendored into the harness. **Remove** (symlink +
  target).
- `~/.agents/skills/caveman-compress` — vendored into the harness. **Remove**
  (symlink + target).
- `~/.agents/skills/caveman-commit` — not used by this feature (≈2 historical
  uses). **Remove** (symlink + target).
- `~/.agents/skills/caveman-review` — **KEEP.** Actively used (~36 runs); the
  user's review tool; unrelated to the compression engine.

Ordering is a hard requirement: **vendor first, verify the harness feature works
without the global skills, then remove.** Removing before vendoring would break
the engine.

**Audit before removing (Codex #11).** "Not used by *this feature*" ≠ "safe to
delete globally." Before removing `caveman` / `caveman-compress` /
`caveman-commit`, grep configs/workflows/other skills for references (and confirm
`caveman-review` does not share a rules file with them). Remove only what is
proven unused outside the vendored engine.

## Open implementation questions

Design forks are all resolved (see "Resolved decisions"). These are
implementation-level questions raised by adversarial review, to be settled in
the plan, not the design:

- **Per-persona vs global siblings (Codex #9).** Soul/identity/user are
  *per-persona* (one set per workspace); AGENTS.md/CLAUDE.md are repo/global.
  Where does each `*.compressed.md` live, and does `dn compress` walk every
  persona workspace plus the repo roots? The loader must resolve the *right*
  sibling for the *consuming* persona.
- **Compression profile selection.** The design names two profiles (strict
  voice-preserving vs standard factual). How does the compressor know which file
  gets which? Options: a fixed filename→profile map, or a `profile:` field in the
  header. The chosen profile should also be part of the freshness key (a profile
  change should invalidate the sibling, same as a rules change).
- **`--status` token accounting.** Report real tokenizer counts, or label the
  number approximate (bytes÷4)? The whole-prefix comparison tool is the
  authoritative net-savings number regardless.

## Resolved decisions

engine (vendor caveman-compress + caveman rules into the harness, input-only;
remove standalone skills post-ship after audit, keep caveman-review); home
(bundled in harness); freshness (**source-hash + engine/rules-version +
body-hash**, all three must match); memory writes (destructive-at-write, both
adversarial risks **explicitly accepted**: silent semantic loss + exempt from
OFF-is-inert); toggle (YOLO-style chip, default ON, flag `compress_aware`);
comparison tool (Phase 2/3, excludes memory); header **stripped before prompt**;
atomic temp+rename writes; voice files use a **stricter profile** + pre-ship
regression prompts. **Core principle: compress INPUT everywhere; never compress
the main agent's RESPONSE (voice is the product); sub-agents may opt into caveman
OUTPUT while preserving evidence, since only the controller reads it.**
