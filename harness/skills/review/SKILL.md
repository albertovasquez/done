---
name: review
description: Independent code review on a separate, more capable model — a different model than the author catches more. Use when the user says "review this", "review that", "review the diff/PR/changes", "code review", or "do a review".
---

# Review (independent model)

The user wants a code review run on a **different model** than the one writing
the code — independent eyes catch more.

To do it:
1. Gather what to review (e.g. run `git diff`, or use the content the user gave).
2. Call the `review` tool with `content` = that text (omit `quick`).
3. The tool resolves the review model from `done.conf [harness] review_model`,
   then the `REVIEW_MODEL` env var. **If neither is set**, propose a sensible
   strong model from the available models (`/models`), prefer one different from
   your own current model, tell the user your pick and why, and ask to confirm.
   On confirm, run the tool with `model=<picked>`, then offer to persist it
   (`[harness] review_model` in done.conf) so it stops asking.
4. Print the findings inline.
