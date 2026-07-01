---
name: create-persona
description: Use when the user wants to create or add a new persona / agent / character (e.g. "create a persona named Robbie", "make a new agent", "add an agent called X") — NOT a scheduled job or reminder.
---

# Create Persona (Agent)

## Overview

A persona is an agent the user can switch to from the agents drawer. Your job is
to turn the user's plain-language request — "create a persona named Robbie" — into
a real persona by **calling the `create_persona` tool**. This is a real ACTION,
not a chat answer and not a cron job.

**Core principle:** Do it, don't describe it. If the user's intent to create a
persona is clear, call the tool immediately. Do NOT reply "I can create Robbie
next" or draft files in prose — that produces nothing. The ONLY thing that
actually creates a persona is the `create_persona` tool call.

## What you need

Just the **display name**. The id is derived automatically (slugified) from the
name. You almost never need to ask anything:

- "create a persona named Robbie" → name = "Robbie".
- "make me a new agent called Data Wrangler" → name = "Data Wrangler".
- "add an agent" (no name) → this is the ONLY case to ask: *"Sure — what should
  I name it?"* Then call the tool.

Do not interrogate the user about the persona's personality, model, or files
before creating it — the persona starts blank and those are edited afterward.

## Implementation: Call the `create_persona` tool

Once you know the name, call the **`create_persona` tool**:

```json
{
    "name": "Robbie"
}
```

- Pass the display name exactly as the user gave it (keep their capitalization).
  The tool slugifies it to an id for you — do NOT pre-slugify.
- The tool is **create-only**: it does NOT switch the active seat. That is by
  design (switching mid-turn leaks seat/model state). After it succeeds, tell the
  user to switch from the agents drawer.
- The tool returns `Created persona '<name>' (id: <id>). It starts blank …` on
  success, or `A persona '<id>' already exists.` / `Could not …` on failure —
  in which case relay the reason (e.g. offer a different name for a duplicate).
  **Once it returns "Created persona", the turn is complete: report the id, point
  the user to the agents drawer, and stop. Do NOT call the tool again and do NOT
  draft the persona's files yourself.**

## Not this skill

- **Scheduled job / reminder / recurring task** → that's `create-job`, a
  different tool. "Remind me every Monday" is a job, not a persona.
- **Switching to / activating an existing persona** → this tool does not switch;
  the user does that from the agents drawer.

## Key Points

- **Call the tool, don't narrate.** Drafting files in prose creates nothing.
- **Name is all you need.** Derive the id yourself? No — the tool does it.
- **Create-only.** Report success, point to the agents drawer, stop.
