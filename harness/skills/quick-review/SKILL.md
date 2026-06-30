---
name: quick-review
description: Fast, cheap code review on a small separate model. Use for "/quick-review" — a quick independent pass.
---

# Quick review (fast independent model)

Same as `/review`, but a **fast/cheap** pass. Call the `review` tool with
`content` and `quick=true`.

The tool resolves the model from `done.conf [harness] quick_review_model`, then
`QUICK_REVIEW_MODEL`. If neither is set, propose a small/fast model from
`/models` (prefer one different from your own), confirm, run with `model=<picked>`,
and offer to persist it (`[harness] quick_review_model`). Print findings inline.
