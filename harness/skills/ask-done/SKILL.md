---
name: ask-done
description: Ask which skill or flow fits your situation. A router over the harness's skills and flows — invoke it when you are unsure where to start.
disable-model-invocation: true
---

# Ask Done

You do not remember every skill, so ask.

This skill is **user-invoked only** — Done never runs it on its own. When the
user explicitly asks which skill or flow fits, read the available skills (the
`# Skills` menu already in context, and the flow map if one is shown) and
recommend the shortest sensible path. Do not start any work; this is navigation,
not execution.

## How to answer

1. Restate, in one line, what the user is trying to accomplish.
2. Name the **flow** it belongs to (or "general" if it is a one-off).
3. Recommend the specific skill(s) to load next, in order, and say why each.
   Reference them by name so the user (or Done) can `load_skill` them.
4. If the request is a *question*, say so and point at `clarify-before-acting` —
   the answer may not need any work at all.

## The shape of work

Most substantial work runs along one spine, and you can join it at any point:

1. **clarify-before-acting** — is this a question or a work order? Settle that first.
2. **planning-before-coding** — lock architecture, edge cases, and the test surface.
3. **test-driven-development** — write the failing test, then the minimal code.
4. **systematic-debugging** — when something breaks: root cause before any fix.
5. **verification-before-completion** — prove it works before claiming done.
6. **receiving-code-review** — fold feedback with rigor, not reflexive agreement.

Standalone skills and specialized **flows** (e.g. marketing, SEO, copywriting,
when enabled for this persona) live outside the spine — recommend them when the
task is clearly in their domain. Keep the recommendation short: the user asked
for a direction, not a lecture.
