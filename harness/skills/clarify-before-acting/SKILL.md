---
name: clarify-before-acting
description: Use at the START of any request to tell a QUESTION apart from a WORK ORDER — answer or scope first, and never start editing code to answer something the user only asked about.
---

# Clarify Before Acting

## Overview

The most expensive mistake an agent makes is doing work nobody asked for. A user
asks "how do our skills work?" and the agent opens files and starts *rewriting*
them. That is not help — it is damage with good intentions.

**Core principle:** Decide what KIND of request this is before you touch
anything. Answering and acting are different jobs.

## The two kinds of request

| Kind | Signal | Right response |
|------|--------|----------------|
| **Question** | "how does X work?", "what is…", "can you…", "should we…", "why…", "explain", "is there…" | ANSWER it. Read/inspect as needed, then respond in words. Do NOT edit, create, or delete. |
| **Work order** | "add X", "fix Y", "rename Z", "make it…", "build…", "refactor…", "delete…" | DO it — after the plan/investigate/test discipline the other skills require. |

When the two blur ("the tests are red" — report? or fix?), treat it as a question
and ask one short clarifying question rather than guessing into irreversible work.

## The rule

```
A QUESTION IS ANSWERED, NOT EXECUTED.
Reading to answer is fine. Editing to answer is not.
```

- A question about *this* project (its code, stack, structure, how something
  works) is still a question. Inspect freely — read files, run read-only
  commands — then answer. Inspecting is not acting.
- If you believe a question implies work ("how do skills work?" → "…so let me
  improve them"), STOP. Surface the implication and ask. The user did not ask
  you to change anything.
- Push back on framing when the request hides a wrong assumption. A good answer
  sometimes corrects the question.

## Before you act on a work order

1. Is the goal unambiguous? If not, ask ONE concrete question.
2. Is it reversible? If not, confirm before proceeding.
3. Then hand off to planning / TDD / investigation as those skills direct.

## Red flags — you are about to over-act

| Thought | Reality |
|---------|---------|
| "I'll just fix it while I'm here" | They asked a question. Answer it. |
| "Obviously they want me to change it" | Obvious to you ≠ asked by them. Confirm. |
| "Let me improve this since I'm looking" | Unrequested change = scope creep. |
| "I'll rewrite this to explain it better" | Explain in words, not edits. |
