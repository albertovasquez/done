---
name: planning-before-coding
description: Use before building any non-trivial feature or change — lock architecture, data flow, edge cases, failure modes, and the test surface BEFORE writing implementation code.
---

# Planning Before Coding

## Overview

Code written before the problem is understood is rework waiting to happen. The
job here is to become the technical lead for a moment: build the spine the work
will hang on, so implementation is filling in a known shape rather than
discovering it line by line.

**Core principle:** Surface the hidden assumptions BEFORE they cost a rewrite.

## What a plan must nail

- **Architecture & boundaries** — what the pieces are, who owns what, where the
  seams sit.
- **Data flow & state** — what moves where, what transitions are legal, what is
  cached vs. derived.
- **Failure modes & edge cases** — what happens on partial failure, empty input,
  concurrency, the unhappy path. Name them out loud.
- **Trust boundaries** — where untrusted data enters; what must be validated.
- **The test surface** — what proves it works, listed before code exists.

## Draw the system

Force the shape into the open. A short diagram — sequence, state, component, or
data-flow — or even a plain bulleted list of steps and transitions does more to
expose hand-waving than prose does. If you cannot draw it, you do not yet
understand it well enough to build it.

## Scale the plan to the work

- **Trivial change** (one-line, rename, obvious fix): no formal plan — just do
  it with the usual test discipline.
- **Non-trivial** (new behavior, multiple files, new data path): plan first. A
  few sentences of architecture + the edge-case list + the test surface is
  enough; it does not need to be long, it needs to be honest.
- **Large/multi-step**: publish the plan and proceed step by step, marking
  progress as you go.

## The rule

```
NO IMPLEMENTATION CODE UNTIL THE SHAPE IS KNOWN.
Unknowns are resolved on paper, not in a half-built codebase.
```

When a question can only be answered by running something (real state, business
logic, a UI you must see), say so and get that answer first — do not guess it
into the design.
