---
name: developer
description: Use when writing, reviewing, or refactoring code — the general coding discipline for Done. Cross-cutting engineering judgment (simplicity, restraint, verifiable goals) plus a map to the sharper skills for the specific job. The home for coding lessons learned.
---

# Developer

The general coding skill for Done. Load it whenever you are about to write,
review, or change code and no sharper skill has already taken over. It carries
the engineering judgment that applies to *every* coding task, and it points you
at the specialized skill when the task has one.

This is a living skill: durable coding lessons accumulate here (see
**Lessons learned** at the bottom). Peer domains — a `ui` skill, a `marketing`
skill — grow the same way for their fields.

## Defer to the sharper skill

This skill is the general case. When the task fits one of these, load that skill
and follow it — do not reimplement its discipline here:

- **A reported bug, failing test, or error to fix** → `systematic-debugging`
  (root cause before any fix).
- **Implementing a feature or a bugfix** → `test-driven-development`
  (write the failing test first, then the minimal code to pass).
- **A non-trivial feature or design decision** → `planning-before-coding`
  (lock architecture, edge cases, and the test surface before implementing).
- **About to claim it works / is done** → `verification-before-completion`
  (run the check, show the output, then claim it — never before).
- **Folding in review feedback** → `receiving-code-review`
  (verify each point on its merits; no reflexive agreement).
- **Unsure where to start** → `ask-done`.

If one of those applies, you are mostly here to route. What follows is what holds
regardless of which one you land in.

## Cross-cutting discipline

**1. Think before coding.** State your assumptions before you act on them. If a
request has more than one reasonable reading, surface them — don't silently pick.
If a simpler approach exists, say so. If something is unclear, stop and name it.

**2. Simplicity first.** Write the minimum code that solves the actual problem.
No speculative abstractions, no configurability that wasn't asked for, no error
handling for cases that can't occur, no framework where a function will do. If a
senior engineer would call it overcomplicated, it is — cut it back. Prefer
deleting code to adding it.

**3. Surgical changes.** Touch only what the task requires. Match the surrounding
style, naming, and idiom even where you'd choose differently. Don't refactor,
reformat, or "improve" adjacent code that isn't broken. Every changed line should
trace to the task. Remove the imports and names *your* change orphaned; leave
pre-existing dead code alone unless asked (mention it, don't delete it).

**4. Verifiable goals.** Turn the task into something you can check, then loop
until it passes. "Add validation" becomes "write tests for the invalid inputs,
then make them pass." "It works" is not a success criterion; a passing command
whose output you've read is. This is what lets you finish independently instead
of guessing.

## Lessons learned

Durable, hard-won coding lessons live here — the kind that would otherwise be
relearned on the next task. Keep each one short: the rule, and one line on why.
Add to this list as lessons accrue; prune ones that stop being true.

- _(seed)_ Prefer the minimum viable change. Most regressions ride in on code
  that wasn't needed for the task.
