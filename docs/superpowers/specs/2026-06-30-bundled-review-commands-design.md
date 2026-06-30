# Bundled `/review` + `/quick-review` — Design

**Date:** 2026-06-30
**Status:** Design approved, pending spec review
**Branch:** `review-commands`

## Goal

Two bundled harness commands that run a code review on a **configurable model
of your choice**, separate from the model the agent is currently using. The
point is **independence**: a model reviewing its own work has blind spots; a
*different* model catches more. These commands make "review with different eyes"
a first-class, one-command action.

- `/review` — a thorough pass (default to a strong model).
- `/quick-review` — a fast/cheap pass (default to a small model).

Both carry a **copy of the `caveman-review` prompt** (terse, actionable
findings: location, problem, fix) as the review instruction.

## Why a sub-call, not a plain skill

A normal skill is just injected prose: `load_skill` reads `SKILL.md` and dumps
its body into the agent's context, so it runs **in the agent's current turn, on
the agent's current model**. That cannot deliver "review on a *different*
model." So these commands **dispatch a model-bound sub-call** instead — reusing
the existing subagent model-binding path (`harness/agent_build.py:build_persona_agent`
+ a `resolve_*_model`-style resolver, mirroring
`harness/subagent_config.py:resolve_subagent_model` and the `compress_model`
resolution shipped in `compress_cli`).

## What gets reviewed: content is passed in

The command is a **generic "review this on a different model" primitive.** The
**agent supplies the content** to review (a diff, a file's contents, pasted
code) as the argument. The command does NOT gather git diffs or fetch PRs
itself — the agent owns *what* to review; the command owns *running it on
independent eyes*.

```
/review <content>        -> review <content> on the review model
/quick-review <content>  -> review <content> on the quick-review model
```

(The agent will typically run `git diff` itself and hand the result in, but
that is the agent's job, not the command's.)

## Model resolution (per command)

Each command resolves its model independently, in this precedence:

1. **`done.conf` `[harness]`** — `review_model` / `quick_review_model` (the
   persistent home, same pattern as `compress_model`). Read via the existing
   `config.harness_setting(key)`.
2. **env override** — `REVIEW_MODEL` / `QUICK_REVIEW_MODEL` (optional one-offs).
3. **Propose** — if nothing is configured, the agent:
   - picks a sensible model from the available `/models` list — a **strong** one
     for `/review`, a **fast/cheap** one for `/quick-review`, **preferring a
     model different from the author's current model** (sensible default, not a
     rule);
   - tells you its pick and why;
   - **asks to confirm** before running;
   - on confirm, **offers to pin it** to `done.conf` (via the generic
     `config.set_harness_setting(key, value)` writer) so it stops asking.

### No independence enforcement

Independence is **the user's responsibility, not the tool's.** There is **no
same-model check, no warning, no block.** If the resolved model equals the
author's current model, the review runs on it quietly. The proposal step
*prefers* a different model only as a sensible default when it has to pick — it
never polices a configured/explicit choice.

## Output

Findings print **inline** in the current Done conversation as the command's
result (terse caveman-review format — one line per finding: location, problem,
fix). The agent can then act on them. No file writing in Phase 1.

## `/review` vs `/quick-review`

Identical mechanism. They differ only in:
- the `done.conf` key they read (`review_model` vs `quick_review_model`);
- the env var they honor (`REVIEW_MODEL` vs `QUICK_REVIEW_MODEL`);
- the **default tier the proposal prefers** (strong vs fast/cheap).

## Components (isolated units)

1. **Review-model resolver** — `resolve_review_model(*, quick: bool) -> str | None`:
   `done.conf [harness]` key → env var → None (signal "propose"). Reuses
   `config.harness_setting`. Pure-ish (reads config/env), no LLM. The `quick`
   flag selects which key/env/tier applies.
2. **Review dispatcher** — takes `(content: str, model_name: str)`, runs the
   caveman-review prompt + content as a model-bound sub-call via the subagent
   path, returns findings text. The one genuinely new piece of machinery.
3. **Two bundled skills** — `harness/skills/review/SKILL.md` and
   `harness/skills/quick-review/SKILL.md`: a copy of the caveman-review prompt +
   a per-command note on the model tier and the resolve→propose→confirm→pin
   flow. These are the user-facing `/review` / `/quick-review` entry points.
4. **Proposal/persist UX** — when the resolver returns None: pick from `/models`
   (prefer different-from-author + the command's tier), announce, confirm, run,
   offer to pin via `config.set_harness_setting("review_model"|"quick_review_model", model)`.

## Data flow

```
/review <content>
  -> resolve_review_model(quick=False)
       done.conf [harness] review_model? -> use it
       REVIEW_MODEL env?                 -> use it
       else                             -> propose (pick from /models, prefer
                                            != author + strong tier), confirm,
                                            offer to pin to done.conf
  -> review dispatcher: (content + caveman-review prompt) on the resolved model
       via the subagent model-binding path (build_persona_agent)
  -> findings print INLINE in the conversation
```

`/quick-review` is identical with `quick=True` (quick_review_model /
QUICK_REVIEW_MODEL / fast tier).

## Error handling

- **No model configured AND proposal declined / no models available:** report
  "no review model configured; set `[harness] review_model` in done.conf" and
  do nothing (mirror the `dn compress` "unavailable" pattern). Never crash.
- **Sub-call fails (auth/model error):** surface the error inline; do not retry
  silently. The review simply didn't run; the user can pick another model.
- **Empty/missing content:** report "nothing to review" and stop.
- **`done.conf` write on pin fails:** the review still ran; report that the pin
  didn't persist (config write is best-effort, like other config writes).

## Testing

- **Resolver:** table-driven over (`done.conf` key set / unset × env set / unset
  × quick True/False) → asserts the right model or None. Reuses the
  `isolated_config` fixture; no LLM.
- **Dispatcher:** inject a fake model callable (the same pattern compress uses) →
  assert the caveman-review prompt + content are passed and findings returned;
  assert it runs on the *given* model name (no implicit fallback to author).
- **Pin writer:** `set_harness_setting` round-trip in an isolated `done.conf`;
  assert `[harness]` and other sections are preserved (the `_serialize(preserve=)`
  path).
- **No-model path:** asserts the "unavailable / propose" branch is taken and
  nothing crashes when no model is configured.

## Reuses what's already shipped

- `config.harness_setting()` — the `[harness]` reader (built for `compress_model`).
- `_serialize(preserve=)` + `set_*` config writers — preserve-on-write plumbing.
- The subagent model-binding path (`build_persona_agent`, `resolve_subagent_model`
  pattern) — for the model-bound sub-call.
- The caveman-review prompt — copied in as the review instruction.

Genuinely new: the **review dispatcher** (content + prompt → sub-call on a chosen
model → inline findings) and the two thin bundled skills.

## Non-goals (Phase 1)

- No git-diff / PR fetching by the command (agent passes content in).
- No independence enforcement (no same-model warning/block).
- No file output (findings are inline only).
- No `--comment`/PR-posting (that's what the separate `/code-review ultra` does).
- Not replacing `caveman-review` for the user's own manual review use; this is a
  bundled, model-bound *copy*.

## Open questions

None at design time. All forks resolved during brainstorming:
execution (model-bound sub-call, not prose); content (passed in by the agent);
model resolution (`done.conf [harness]` → env → propose+confirm+pin);
enforcement (none — user's responsibility); output (inline); two commands
differing only by model tier.
