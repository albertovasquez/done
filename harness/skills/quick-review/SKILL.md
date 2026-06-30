---
name: quick-review
description: Fast, cheap independent code review on a small separate model. Use when the user says "quick review", "quick review of this/that", "do a quick review", or "fast review".
---

# Quick review (fast independent model)

Same review flow, but a **fast/cheap** pass. Call the `review` tool with
`content` and `quick=true`.

The tool resolves the model from `done.conf [harness] quick_review_model`, then
`QUICK_REVIEW_MODEL`. If neither is set, propose a small/fast model from
`/models` (prefer one different from your own), confirm, run with `model=<picked>`,
and offer to persist it (`[harness] quick_review_model`). Print findings inline.
