# Design: deterministic "what tools do you have?" answer in the chat path

**Date:** 2026-06-28
**Status:** approved, ready for implementation
**Scope:** one source file (`harness/chat_handler.py`) + its tests. Additive, no regressions.

## Problem

A capability question such as *"what tools do you have access to?"* is classified
as a `chat_question` (observed conf ~0.98) and routed to
`ChatHandler.answer_stream` (`harness/chat_handler.py`). That path:

- passes **no** `tools=` parameter to `litellm.completion` (unlike the agent
  path's `streaming_model.py:_query`, which passes `_tool_schemas()`), and
- includes **no** text enumerating the tools in its system prompt
  (`render_base_prompt` mentions Read/Write/Edit in passing but never lists the
  available tools).

So the model answers from generic LLM self-knowledge ("text chat", "image
generation") instead of the real tool surface (bash, read, write, edit).

The existing deterministic answerer, `is_capability_question`, only matches
`\bskills?\b` or "what can you do / what are your capabilities" — it does not
catch "tools". So the tools question falls through to the tool-blind model.

## Approach

Extend the **existing** deterministic capability-answer path. No new routing, no
change to the agent path, no model dependency. Three pieces, all in
`harness/chat_handler.py`:

### 1. Detection — tight, possessive-only trigger

Add a second regex `_TOOLS_Q` that matches only **self-directed** tool/command
questions, and OR it into `is_capability_question`:

- matches: "what tools do you have", "your tools", "tools you have access to",
  "what commands do you have", "tools you can use"
- does **not** match: "write a tool to parse logs", "what tools should I use in
  Rust"

Rationale: a false negative just falls through to the model (status quo, no
regression); a false positive would hijack a legitimate "build me a tool"
request. So the trigger biases hard to precision. It is a layer in front of the
unchanged skills trigger — same shape as the existing `_SKILL_WORD`/`_ABILITY_Q`
design.

### 2. Routing — branch to the right formatter

`is_capability_question` stays the single entry point in `answer_stream`. Inside,
distinguish the two kinds so each yields the right answer:

- skills/abilities question → today's `_format_catalog(catalog)` (**byte-identical**).
- tools question → new `_format_tools(...)`: lists the real tools, the loaded
  skill catalog, and a one-line note about the `plan` checklist command.

A clean way to keep one public entry point: have `answer_stream` ask a small
classifier (e.g. `_is_tools_question(prompt)`) to pick the formatter, while
`is_capability_question` remains the OR of both for the "should I answer
deterministically at all?" decision. Implementation detail left to the plan;
the contract is: skills-only phrasings produce today's exact output.

### 3. Data source — the live registry

Tools come from `harness.tools.registry.build_registry()` reading
`tool.name` + `tool.schema["function"]["description"]`. Verified live:

```
bash  || Execute a bash command
read  || Read a text file and return its full contents.
write || Create or overwrite a text file with the given content.
edit  || Replace the unique occurrence of old_string with new_string in a file. ...
```

`build_registry()` does no I/O at import (`registry.py:1`), returns a fresh list
per call, and is already the SSOT for the agent path — so the chat answer and the
agent's real capabilities cannot drift. Works in mock mode (no model needed).

The `plan` note is a single literal line describing the intercepted checklist
sentinel (it is documented in `base_prompt.py` but is not a registry tool).

## Example output (tools question)

```
I have **4 tools** available:
- **bash** — Execute a bash command
- **read** — Read a text file and return its full contents.
- **write** — Create or overwrite a text file with the given content.
- **edit** — Replace the unique occurrence of old_string with new_string in a file.

I also have **N skills**:
- **<name>** — <description>
...

Plus a `plan` command that drives an on-screen checklist for multi-step work.
```

(When the catalog is empty, the skills section degrades to the existing
"no skills loaded" wording; mock mode still produces the full tools list.)

## Testing

In `tests/` (alongside the existing chat-handler tests):

**Positive**
- "what tools do you have access to?" → `is_capability_question` true; answer
  lists all 4 registry tools by name.
- "your tools" / "what commands do you have" → trigger true.

**Regression guard (the key one)**
- "write a tool to parse logs" → `is_capability_question` **false** (routes to
  the model, unchanged).
- An existing skills-question test still yields the **byte-identical** catalog
  answer (no tools section bleed-in).

## No-regression guarantees

- Skills/abilities phrasings: unchanged output.
- Any prompt that didn't match before and isn't a possessive tool-question:
  still falls through to the model — identical behavior.
- Agent (non-chat) path untouched; native `tools=` still flows there.
- `build_registry()` import is I/O-free, so no startup-cost regression on the
  chat path (the existing lazy-`litellm` rule is preserved — registry import is
  cheap and pure).

## Out of scope (YAGNI)

- Injecting `tools=` into the chat completion.
- Adding a `# Tools` section to `render_base_prompt`.

Both are larger and unnecessary once the deterministic answer is correct. They
remain available as follow-ups if we later want the *model* (not just the
deterministic path) to know its tools during free-form chat.
