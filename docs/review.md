# Independent review

Done can run a code review on a **different model** — separate eyes catch more
than self-review. A thorough review uses the configured `review_model`; a quick
review uses `quick_review_model`. Both gather the diff or content and dispatch
to the chosen model for findings, printed inline in terse caveman style:
location, problem, fix.

The independent model is the product. Different hands reviewing the same code
catch blind spots — the author's model and a fresh model together are more
effective than either alone.

## Triggering a review

Trigger a review with natural language:

- Natural language like *"review this"*, *"review that"*, *"review the diff"*,
  *"review the PR"*, *"code review"*, *"do a quick review of this"*, *"fast
  review of that"* — the agent auto-invokes the appropriate skill

The current TUI slash-command registry does not include `/review` or
`/quick-review`; type the request as normal chat.

The agent gathers the content (typically `git diff` for the working tree, or
context you provide) and passes it to the review command.

## Model resolution

Review models are configurable in `done.conf`'s top-level `[harness]` table:

```toml
[harness]
review_model = "claude-opus-4-8"
quick_review_model = "claude-haiku-4-5-20251001"
```

Done resolves a review model with this priority:

1. **`[harness]` table in `~/.config/harness/done.conf`** — the persistent home
   for both keys. This is where you lock a preferred model.
2. **`REVIEW_MODEL` / `QUICK_REVIEW_MODEL` environment variable** — one-off
   fallback when no `done.conf` value is set (useful for CI or scripting).
3. **Agent proposal** — if neither source resolves a model, the agent proposes
   a sensible default: a strong model (like Opus) for a thorough review, a fast
   model (like Haiku) for a quick review. **The proposal prefers a model
   different from the agent's own model** — the point is a fresh perspective,
   not a second pass by the same hands.

When the agent proposes, it asks for confirmation before running, and offers to
persist your choice to `done.conf` for future runs:

```
Agent: I'll review this with claude-opus-4-8. Should I run it?
[approve] [use a different model]
```

## What gets reviewed

The agent gathers content (e.g., `git diff` for the working tree) and passes it
to the review command. The command runs on the resolved model and returns findings
in a terse style — location + problem + fix — printed inline in the transcript.

## Independence is your responsibility

There is **no enforcement** that the review model is different from the author's
model — that's up to you. A same-model review is valid (it's just not as useful);
the tool does not block it. If you want to guarantee independence, set different
models in `done.conf`:

```toml
[harness]
# The persona's agent (author) uses the default from VIBEPROXY_MODEL
review_model = "claude-opus-4-8"        # always review with Opus
quick_review_model = "claude-haiku-4-5" # fast reviews with Haiku
```

For a one-run environment fallback, leave the matching `done.conf` key unset and
launch with `REVIEW_MODEL` or `QUICK_REVIEW_MODEL`.
