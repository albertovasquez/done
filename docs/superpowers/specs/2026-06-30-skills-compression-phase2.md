# Skills compression — Phase 2 design note

**Date:** 2026-06-30
**Status:** Scoped, not started. Follow-up to compress-aware Phase 1 (#186).
**Depends on:** the shipped `harness/compress/` foundation (loader, sibling,
freshness, engine) — reuse it; do not rebuild.

## Question this answers

Phase 1 compresses the per-turn prose files (soul/identity/user, AGENTS.md,
CLAUDE.md, MEMORY.md) but **not skills**. Should it, and how?

## Finding: compress skill BODIES, not the menu

The skills system has two surfaces, and they have opposite ROI:

| Surface | What it is | When loaded | Size | Compress? |
| --- | --- | --- | --- | --- |
| **Menu** | one line per skill (`name` + `description` + `category`), built from **frontmatter only** | **every turn** | ~1.3 KB total | **No** |
| **Body** | the full `SKILL.md` prose after the frontmatter | **on demand** (when a skill is invoked), once per session per skill | 1.8–10 KB each, ~43 KB bundled total | **Yes** |

- **Menu: do not compress.** It's tiny (~1.3 KB) and is built from frontmatter
  that must be parsed exactly (`name` must match the dir, `description`/`flows`
  drive routing). There is nothing safe to compress and almost nothing to gain.
- **Bodies: the real target.** They are narrative prose (headings, examples,
  whitespace) — the high-yield compression class (think −40–60%, like verbose
  memory, not the −6% of already-tight AGENTS.md). The heavy hitters:
  `test-driven-development` (10 KB), `systematic-debugging` (9.8 KB),
  `receiving-code-review` (6.4 KB), `create-job` (6.5 KB).

## ROI is real but lower than per-turn files (why this is Phase 2, not Phase 1)

A skill body loads **on demand, once per session per skill** (deduped via
`env._loaded_skills` in `harness/tools/load_skill.py`). So compressing it saves
tokens only on sessions that actually invoke that skill, once. That is genuinely
lower and more intermittent ROI than soul/AGENTS/memory, which are injected on
*every* turn. The Phase-1 ranking was correct: do the per-turn files first.

But the per-load saving is large when it lands (a 10 KB body dropped mid-task
dominates that turn's context), so it is worth doing.

## The blast-radius caveat (the reason this needs care)

Unlike Phase-1 files, which live in a **per-persona workspace**, skills live in
**shared roots** (`harness/paths.py:skills_dirs`, lowest→highest precedence):

1. **bundled** — `<harness-pkg>/skills/` — **ships inside the wheel.** A
   `SKILL.compressed.md` here would be packaged and distributed.
2. `~/.claude/skills/` — **cross-tool**; other tools (Claude Code, etc.) read
   this directory. Dropping compressed siblings here litters a dir we don't own.
3. `~/.config/harness/skills/` — Done-native user dir.
4. `<cwd>/.claude/skills/` and 5. `<cwd>/.agents/skills/` — project dirs, also
   cross-tool.

Implications:
- A `*.compressed.md` sibling next to a shared/bundled skill is **not** the same
  low-risk, throwaway artifact it is in a persona workspace. Phase 2 must decide
  **where the compressed bodies live** — almost certainly **NOT** as siblings in
  the shared roots. Options:
  - a **side cache** keyed by source path+hash under
    `~/.config/harness/` (Done-owned), so we never write into bundled or
    cross-tool dirs; OR
  - siblings only for **project/user** roots, never bundled, never `~/.claude`.
- The `.gitignore` `*.compressed.md` rule we added covers project dirs, but a
  bundled-skill sibling would be inside the package — gitignore is irrelevant
  there; it'd be a build-artifact question.

## Technical hook points (reuse the Phase-1 loader)

All in `harness/skills.py`:

- **Body read — the swap point is `compose()` at line 146, NOT `_parse_skill_md`.**
  `_parse_skill_md(path) -> (frontmatter_dict, body)` (lines 67–77) is shared by
  BOTH the per-turn menu/catalog scan (`load_catalog_with_skips`, line 109) and
  the body-compose path (`compose`, line 146). Hooking `_parse_skill_md` itself
  would compress the menu too (see "RESOLVED" finding below). Leave
  `_parse_skill_md` reading the ORIGINAL; apply the compressed swap ONLY at the
  body read driven by `compose(roots, names) -> SkillLoad` (lines 134–161, the
  `_parse_skill_md` call at line 146) — i.e. compress the body that gets injected
  on `load_skill`, while the catalog frontmatter scan stays original-only.
- **CRITICAL — frontmatter must stay byte-exact.** `_parse_skill_md` splits on
  the `---` fences and `yaml.safe_load`s the frontmatter. Compression must apply
  to the **body only**; the compressed sibling's frontmatter must be preserved
  verbatim or `name`/`description`/`flows`/`disable-model-invocation` parsing
  breaks. (The compressor already treats structure carefully, but skills make
  "frontmatter is sacred" a hard requirement — likely: compress the body, then
  re-prepend the original frontmatter unchanged.)
- **Mode flag:** add a `_compress_on()` helper as in persona/agents/memory.
  Skills aren't persona-keyed, so use the `default` `compress_aware` flag (same
  as `agents._compress_on_dir`).
- **`dn compress` targets:** extend `_default_targets` (or a `--skills` flag) to
  walk the skill roots Done *owns* (per the blast-radius decision above) and
  rebuild their body caches.

## No references/ subdirs today

Bundled skills are single-file (`SKILL.md` only — no `references/*.md` injected
into context). If that changes, those become additional candidates, but nothing
to do now.

## Suggested phasing within Phase 2

1. **Decide the storage location** (side cache vs. project/user-only siblings) —
   this is the real design fork; everything else follows.
2. Wire `_parse_skill_md` through the loader with **body-only** compression
   (frontmatter preserved verbatim) + `_compress_on`.
3. Extend `dn compress` to rebuild skill body caches for Done-owned roots only.
4. Explicitly **exclude the menu** (document why).

## Open questions for Phase 2

- Side cache vs. restricted siblings — where do compressed skill bodies live?
- Do we ever compress bundled-skill bodies (shipped in the wheel), or only
  user/project skills? (Leaning: never touch bundled at runtime; if bundled
  bodies are worth compressing, do it at *build* time, not via `dn compress`.)
- Is the ~1.3 KB menu truly not worth a per-turn compression pass? (Current
  answer: correct, skip it — frontmatter is unsafe to compress and the size is
  trivial.)

## Review findings (caveman-review, 2026-06-30)

These poke holes in the design above. The two MUST-VERIFY items have now been
**resolved against live code** (2026-06-30); resolutions below. (A Codex review
of #188 was requested but the companion only returned an async task handle, not
findings — these two were verified directly instead.)

- **RESOLVED — dedup is per-TURN; body ROI is HIGHER than §ROI claims.**
  `harness/tracing_agent.py:155-159` resets `env._loaded_skills = set()` **each
  run (turn)** — explicit comment: "load_skill dedup is per-turn... a long-lived
  ACP session can re-pull a skill on a later turn." So a skill invoked across N
  turns is re-read from disk **N times**, not once per session. **Correction to
  §ROI:** body compression pays off on *every turn the skill is used*, not once —
  meaningfully higher ROI than "intermittent, once per session." Still lower than
  the always-on per-turn files (a skill is only read on turns it's invoked), but
  the gap is smaller than the spec states. Soften the "lower/intermittent"
  framing accordingly.
- **RESOLVED — the naive hook WOULD compress the menu; use line-146's read, not
  `_parse_skill_md`.** Confirmed: `load_catalog_with_skips` (the per-turn
  menu/catalog scan) calls `_parse_skill_md(child/"SKILL.md")` at
  `harness/skills.py:109` — the **same function** the body-compose path uses at
  `skills.py:146`. The catalog discards the body (`data, _ =`) but reads the whole
  file. So swapping the read **inside `_parse_skill_md`** would route the per-turn
  menu scan through the compressed sibling — the exact "compress bodies, not menu"
  contradiction. **Correct seam:** leave `_parse_skill_md` reading the ORIGINAL
  (the catalog needs exact frontmatter every turn); apply the compressed swap
  ONLY at the **body read in `compose()` (skills.py:146)** — e.g. a separate
  body-only read path, or read the sibling's body there while `_parse_skill_md`
  stays original-only. Do NOT hook `_parse_skill_md`.
- **Sibling SHAPE is underspecified.** Decide: body-only sibling (no frontmatter)
  vs whole-file sibling. `load_context_file` returns the sibling's bytes; if
  whole-file, the compressor must be forbidden from touching the frontmatter
  region; if body-only, the freshness `body-sha` + "header stripped" logic need
  rethinking for skills. This interacts with the storage-location fork.
- **Side cache must preserve the 3-part freshness contract.** A side cache has no
  in-file header to carry `source-sha` + `engine/rules-version` + `body-sha`.
  Encode all three in the cache key/metadata, or the rules-staleness leg (the
  #186 Codex-#3 fix) silently regresses for skills.
- **Project-skill flag ownership (§Mode flag).** A skill can come from a project
  root while the active persona is non-`default`. The doc assumes the `default`
  `compress_aware` flag always governs — confirm that's intended for project
  skills, not just convenient.
- **Bundled package-data globs.** Confirm whether `pyproject` `skills/**/*`
  package-data would sweep a stray `*.compressed.md` into the wheel. Either way it
  reinforces "bundled = build-time only, never runtime siblings."
- **`dn compress` surface: pick `--skills`, not `_default_targets`.**
  `_default_targets` is cwd-only (the #186 deferral); skill roots are global.
  A `--skills` flag is the clean fit; conflating the two scopes is not.
- **State savings per-invoked-skill, not the 43 KB aggregate.** You never load
  all 8 bundled skills at once; the aggregate overstates the real win.
