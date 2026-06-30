# Handoff — compress-aware Phase 2 (#188), to resume after a `/compress`

**Date:** 2026-06-30
**Issue:** https://github.com/albertovasquez/done/issues/188 (OPEN)
**Baseline to branch from:** `origin/main` (was `95efe96` at write time — fetch fresh).

This doc lets a fresh session resume Phase 2 cold, with no prior context.

## Start here (do this first, every time)

1. `cd` to the repo, `git fetch origin main`, branch a worktree off **origin/main** (NOT local main — see "Gotchas"):
   ```bash
   git worktree add .worktrees/<task-name> -b <task-name> origin/main
   cd .worktrees/<task-name>
   ```
2. Read the spec + plan that are already on main:
   - `docs/superpowers/specs/2026-06-30-context-friendly-mode-design.md` (Phase 1 design)
   - `docs/superpowers/specs/2026-06-30-skills-compression-phase2.md` (skills, shipped)
   - `docs/compress-aware.md` (user-facing reference — read this to know current behavior)
3. Test command (from the worktree root; the worktree shares the main checkout's venv):
   ```bash
   /Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/ -q
   ```
4. **Known-green baseline:** 1 failing test is PRE-EXISTING and unrelated —
   `tests/jobs/test_service_launchd.py::test_build_plist_has_runatload_keepalive_and_program`.
   Also a flaky Textual-Pilot cluster (`tests/test_tui_pilot.py`,
   `tests/test_tui_always_interactive.py`) can fail ONLY under the broad run while
   passing in isolation — that's the known flake, not a regression. Anything else
   failing IS yours.

## What's already DONE (do not redo)

- **Phase 1** (merged, #186): shadow `FOO.compressed.md` siblings for
  soul/identity/user/MEMORY/AGENTS/CLAUDE; 3-part-header freshness; loader at the
  read chokepoints (`persona.py`, `agents.py`, `memory.py`); `dn compress` /
  `--status`; `compress_aware` flag (default ON) + footer chip + `/compress-aware`;
  vendored engine (`harness/compress/{rules,validate,engine,sibling,loader}.py`);
  destructive memory compress-at-write (`memory.compress_on_write`, helper only).
- **Skills compression** (merged, #190): Done-owned side cache
  (`harness/compress/skill_cache.py`), keyed by `sha256(source_body + rules)`;
  `skills.compose` serves cached body when fresh (menu untouched);
  `dn compress --skills` rebuild.
- **Skill cleanup**: all four caveman skills removed (capabilities vendored).

## Remaining Phase 2 items (7) — ordered easiest→highest-value

Each is independent; batch the small ones into one PR if you like. Anchors are on
`origin/main`.

### A. Minor hardening (small, mechanical)
- **`_serialize` array/nested round-trip** — `harness/config.py:142` `_serialize(preserve=)`
  cannot round-trip a TOML array or nested sub-table inside a preserved top-level
  section (e.g. a future `[harness] tags = [...]`); it stringifies via repr →
  invalid TOML. Today `[harness]` is scalar-only so it's latent. Fix: emit lists
  as TOML arrays / nested dicts as sub-tables, OR raise rather than silently
  mangle. Add a test with `[harness] x = ["a","b"]` surviving a write.
- **Non-`CompressionError` propagation test for `compress_on_write`** —
  `harness/memory.py:45`. The helper catches `CompressionError` → falls back to
  verbose; a different exception propagates (documented, intentional). Add a test
  pinning that contract.

### B. Per-persona target walk in `dn compress` (small)
- `harness/compress_cli.py:55` `_default_targets()` is cwd `AGENTS.md`/`CLAUDE.md`
  only. Extend it (or add a flag) to walk each persona workspace's
  SOUL/IDENTITY/USER/MEMORY so `dn compress` covers the voice/memory files
  without explicit paths. Mind the per-persona workspace dir resolution (see
  `harness/persona.py` / `paths`). Keep bundled/cross-tool dirs out (same caution
  as skills).

### C. Route the agent's memory WRITES through `compress_on_write` (medium)
- The helper exists (`harness/memory.py:45`) but is **unwired** — the agent writes
  memory via the generic Write/Edit tools, so compress-at-write never fires. Decide
  the chokepoint: either teach the agent (prompt) to call a memory-write path, or
  intercept writes to `MEMORY.md` in the workspace. **Caution:** this is the one
  DESTRUCTIVE spot (no verbose original kept) — the spec's accepted risks apply;
  do not silently intercept ALL Write-tool calls. Re-read the Memory section of
  `docs/compress-aware.md` and the spec's "Memory writes" before building.

### D. Thread the LIVE chip override into the read sites (medium)
- Today the footer chip toggles a LIVE value (`app.py:115` `self._compress_aware`)
  but the read sites (`persona.py`/`agents.py`/`memory.py` → `_compress_on`) read
  the PINNED config flag (`config.compress_aware_pinned`). So clicking the chip OFF
  changes the label but files still load compressed until you pin. Thread the live
  session value down to the loader decision. The 4 read sites all call
  `compress.loader.load_context_file(..., mode_on=...)`; the `mode_on` source needs
  to honor the live override, not just the pinned flag. Cross-process wrinkle: the
  TUI and the agent are two processes (see memory `two-process-boundary-rationale`)
  — the live value must reach the agent side (likely via the existing set-flag
  ext-method path used for YOLO/compress pin).

### E. Stricter voice-preserving profile for soul/identity/user (medium/high value)
- Phase 1 uses ONE compression profile for all files. Voice files (soul/identity/
  user) risk losing the *style exemplars / forbidden-phrasing* that create the
  agent's voice (the caveman-review/Codex finding). Add a stricter profile that
  treats example/style blocks + "never say X" lists as preserve-exactly regions.
  Lives in `harness/compress/rules.py` (`build_compress_prompt` + a profile
  selector) and/or `engine.compress_text`. **Bump `RULES_VERSION`** when you change
  the prompts (it's in the freshness key — old siblings/cache auto-invalidate).
  Decide how a file picks its profile (filename map vs a param).

### F. On/off whole-prefix comparison tool + sub-agent caveman returns (larger)
- Two sub-items from the spec: (1) a tool that assembles the REAL composed prompt
  prefix both ways (compress on vs off, excluding memory) and reports the true
  per-turn token delta — the honest "what does this save?" number; (2) sub-agents
  may return in caveman style (prompt-level instruction at dispatch;
  `harness/tools/subagent.py` / `subagent_prompt.py`), preserving evidence
  verbatim. These are separable; do (1) first if you want the savings number.

### G. VERIFY-FOR-REAL (do this with a live model — highest signal, do EARLY) ⭐
- The whole premise is "compress INPUT, voice still comes through." It has NEVER
  been tested on a real voice file with a live model. Once a model is configured
  (`[harness] compress_model` in done.conf, e.g. `claude-haiku-4-5-20251001`):
  ```bash
  dn compress <persona-workspace>/SOUL.md     # build the sibling
  cat <persona-workspace>/SOUL.compressed.md  # eyeball: facts kept? voice cues kept?
  # then run a normal turn and judge: does the agent still SOUND like itself?
  ```
  If voice flattens → that's the signal to prioritize item E (stricter profile) or
  to exclude voice files from compression. If it holds → the premise is validated.
  **This is the cheapest way to de-risk the rest of Phase 2.** Delete the sibling
  to revert instantly (`rm SOUL.compressed.md`).

## Recommended order

1. **G (verify-for-real)** — earliest, decides whether E is urgent. Needs only a model + eyeballing; no code.
2. **A + B** — small, mechanical, one PR.
3. **C or D** — pick by what you want working (memory write-compression, or a chip that actually toggles loading).
4. **E** — if G showed voice bleed.
5. **F** — last; nice-to-have (the savings number + sub-agent returns).

## Gotchas (learned this session — don't relearn them)

- **Branch from `origin/main`, not local `main`.** Local main has carried
  unpushed/uncommitted proxy work this session; cutting from it pollutes the
  branch. (At this write, local==origin, but always confirm with
  `git rev-list --count origin/main..main` = 0 before trusting local main.)
- **Subagents must work in the worktree.** A subagent committed to the PRIMARY
  checkout on `main` once this session (the editable install resolves `harness`
  from the primary tree). Every dispatch: `cd` to the worktree + verify
  `git branch --show-current` first; controller verifies the commit landed on the
  feature branch AND origin/main is unchanged BEFORE reviewing.
- **Run the FULL suite before the final review.** Adding bundled skills/tools trips
  EXACT-inventory tests (`tests/test_system_skills.py`,
  `tests/test_load_skill.py`) that the feature's own test files don't cover. (Not
  relevant if you add no bundled skills/tools, but run it anyway.)
- **`RULES_VERSION` is load-bearing.** Any change to the compression prompts must
  bump it (`harness/compress/rules.py:4`) — it's hashed into both the sibling
  freshness header and the skill-cache key, so a bump cleanly invalidates stale
  compressed output everywhere. Forgetting this serves outdated compressions.
- **Read path must NEVER raise.** The loader and `skill_cache.cached_body` degrade
  to the original on any error (catch `OSError` + `UnicodeDecodeError`). Preserve
  this for any new read-path code — a corrupt-cache crash was a Critical caught in
  the skills work.
- **Finish as a PR** (AGENTS.md), or run `/ship` to auto-merge+prune (the
  maintainer override). Don't local-merge by hand.

## Process to use

Brainstorm is mostly done (the spec covers these). For each item: worktree →
writing-plans (or straight to TDD for the tiny ones) → subagent-driven-development
with the guardrails above → final review → PR/ship. Tick the item in #188's body
and comment when it lands.
