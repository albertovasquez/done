# Multi-tool surface — design (issue #60, Slice 1)

**Status:** design / spec (no implementation). Hand-off to writing-plans.
**Date:** 2026-06-27
**Author:** Alberto Vasquez (with Claude Opus 4.8)
**Tracks:** GitHub issue #60 — roadmap item 2.1 of
`docs/superpowers/specs/2026-06-27-claude-code-system-prompt-gap-analysis.md`.
The base prompt (Part 1) already shipped (PR #55); this is the highest-leverage
roadmap item and the first one to build.

**Scope of this slice (decided in brainstorming):**
- Build the tool-registration/dispatch **seam** in `harness/` AND ship its first
  real consumers: `Read`, `Write`, `Edit`.
- `bash` stays as one tool among many, keeping its privileged finish path.
- **Deferred to follow-ups:** parallel tool calls in one response; a Write
  "must-read-before-overwrite" hard gate.

**Hard constraint:** zero edits under `upstream/` (AGENTS.md #4). Every override
lives in code `harness/` already owns. Verified against the worktree at authoring
time — re-verify file:line refs before acting (AGENTS.md #6).

---

## 0. The seam, in one paragraph

`dn`'s agent is **already function-calling** — it is not blocked by "no tool
surface," it is blocked by "every capability is expressed as the single `bash`
tool." Three upstream behaviors hardcode `bash`, and **all three are overridable
from `harness/` without touching `upstream/`**:

1. **Tool list** — `tools=[BASH_TOOL]` in `LitellmModel._query`
   (`upstream/.../litellm_model.py:69`) and in `StreamingLitellmModel._query`
   (`harness/streaming_model.py:43`, which `dn` owns).
2. **Parse** — `parse_toolcall_actions` rejects any name ≠ `"bash"` and forces a
   `command` arg (`upstream/.../actions_toolcall.py:60-63`), called via
   `LitellmModel._parse_actions` (`litellm_model.py:127`) — an overridable method.
3. **Dispatch** — `execute_actions` runs each action as a shell command via
   `env.execute(action)` (`upstream/.../default.py:154`), reimplemented in
   `harness/tracing_agent.py:147-165` — already ours.

So the seam is genuinely a `harness/` extension. `StreamingLitellmModel` is our
file; `_parse_actions`/`format_observation_messages` are overridable; and
`execute_actions` is already overridden in `TracingAgent`.

---

## 1. Architecture & boundaries

A new `harness/tools/` package owns the tool abstraction. One `Tool` per file,
each bundling its schema + execution. A registry assembles them fresh per
construction.

```
harness/tools/
  base.py     Tool: name:str
                    schema:dict                       # JSON sent to the model
                    display_label(args:dict)->str     # for the "action" trace/TUI event
                    execute(args:dict, env)->dict      # -> {output, returncode, exception_info}
  bash.py     BashTool   schema = upstream BASH_TOOL; NOT dispatched via execute() (see §4)
  read.py     ReadTool
  write.py    WriteTool
  edit.py     EditTool
  registry.py build_registry() -> list[Tool]          # FRESH instance per call
```

**Boundaries.** A new tool is one new file + one line in `build_registry()`; the
seam never changes. Each tool is testable in isolation: `tool.execute({...}, env)`
→ assert the output dict. `bash` becomes one tool among many but keeps its
privileged finish path (§4).

**Registry is never a module-global.** `build_registry()` returns a fresh list
per call, passed into the model and the agent. Two model instances exist at once
(worker vs. chat) and per-persona models compound this; a shared mutable registry
would leak state across them.

---

## 2. Model seam — schema list + parse (`harness/streaming_model.py`)

`registry: list[Tool]` becomes a constructor argument on `StreamingLitellmModel`.
Two overrides.

### 2a. Tool list — both code paths send the full registry

The streaming branch sends `tools=[t.schema for t in self.registry]` instead of
`[BASH_TOOL]`.

**The blocking branch must be overridden too.** Today the empty-stream fallback
(`streaming_model.py:59`) and the mock/CLI/non-streaming path both call
`super()._query`, which re-sends `[BASH_TOOL]` (`litellm_model.py:69`). If left
alone, those paths silently drop every file tool. So the subclass overrides the
blocking branch as well, sending the same `tools=[t.schema for t in self.registry]`.
**No path re-hardcodes bash.**

### 2b. Parse — override `_parse_actions`

Iterate `response.choices[0].message.tool_calls`. Per call:

- **Keep `finish_reason` in `template_kwargs`** (as `litellm_model.py:133` does)
  so the format-error template can still distinguish a real format mistake from a
  `max_tokens` truncation.
- **Unknown tool name → `FormatError`**, rendered via the existing
  `format_error_template`, **naming the offending tool** so the model can
  self-correct. The response-persistence contract in `query()`
  (`litellm_model.py:88-97`) is untouched and still runs.
- **Bad JSON args → `FormatError`** (a format mistake; persist the response).
  This is NOT a `returncode=1` tool result. Distinction is load-bearing:
  *bad args = FormatError; tool runtime failure = returncode=1.* Never conflate.
- **Action shape:** `{"tool_name": name, "args": <parsed dict>, "tool_call_id": id}`.
- **Bash compatibility:** for the `bash` tool, ALSO set
  `action["command"] = args["command"]`. The environment reads `action["command"]`
  (`local.py:48`); dispatch reads `action["tool_name"]`. Both keys present for
  bash keeps it byte-compatible with the upstream environment while the rest of
  the loop generalizes.

---

## 3. Result shape — tools return the upstream output dict

**Decision:** every `tool.execute` returns `{output: str, returncode: int,
exception_info: str | None}` — the same shape `env.execute` produces.

Rationale, mapped to the format contract:

- `format_observation_messages` (`actions_toolcall.py:78-112`) zips `actions` ↔
  `outputs` **positionally** and copies `action["tool_call_id"]` onto each
  `role:"tool"` message (`:104-106`). It renders each output via
  `observation_template`, which reads `output.returncode`, `output.output`,
  `output.exception_info` (`litellm_model.py:41-43`).
- The formatter is therefore **agnostic to which tool produced the output**. It
  only needs: (1) one output dict per action, (2) `tool_call_id` on the action,
  (3) the output exposing `output`/`returncode`/`exception_info`.
- Reusing this shape means **no second rendering path** and **no new format
  surface to test**; `tool_call_id` pairing holds automatically; the
  `FormatError`-persistence contract is untouched.
- `output` is contractually a **`str`**, so the `output_bytes` computation
  (`tracing_agent.py:162`) never blows up on a non-string.
- File-tool errors render identically to a failed shell command: e.g. `ReadTool`
  on a missing file → `{output: "<error message>", returncode: 1,
  exception_info: None}`. The model already knows how to react to a nonzero
  returncode.

---

## 4. Dispatch & the submit path (`harness/tracing_agent.py:147-165`)

Generalize the existing `execute_actions` loop. Pseudocode:

```python
by_name = {t.name: t for t in self.registry}
outputs = []
for action in message.get("extra", {}).get("actions", []):
    name = action["tool_name"]
    tool = by_name.get(name)
    if tool is None:                       # parse already guards; belt-and-suspenders
        raise FormatError(...)             # NOT a bare KeyError → would kill the run
    self._emitter.emit("action", command=tool.display_label(action.get("args", {})))
    if name == "bash":
        try:
            output = self.env.execute(action)          # bash STAYS on the env path
        except Submitted:
            self._emitter.emit("action.done", returncode=0, output_bytes=0)
            raise                                       # turn ends; Submitted NOT swallowed
    else:
        output = tool.execute(action["args"], self.env)
    outputs.append(output)
    self._emitter.emit("action.done",
                       returncode=output["returncode"],
                       output_bytes=len(output["output"].encode("utf-8")))
return self.add_messages(
    *self.model.format_observation_messages(message, outputs, self.get_template_vars())
)
```

**Why bash stays special — the finish mechanism.** A turn ends when `env.execute`
raises `Submitted`: the environment inspects bash output and raises when the first
line is `COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` with returncode 0
(`local.py:48-50`). Finishing is therefore a **bash-only, environment-level**
concern. Consequences pinned by this design:

- **Bash MUST route through `self.env.execute` inside the existing
  `try/except Submitted`.** Routing bash through a `tool.execute` wrapper would
  swallow `Submitted` and turns would never end.
- **File tools can never finish a turn** — only bash can. The submit path is
  **untouched**.
- **Unknown tool name is guarded** at dispatch (not just at parse), so a stray
  name never becomes an uncaught exception that triggers `handle_uncaught_exception`
  and re-raises, killing the run.
- **`display_label(args)` drives the `action` emit**, not an assumed `command`
  string — so a `ReadTool` shows e.g. `read path/to/file`, not a blank line in the
  TUI/trace. The `action`/`action.done` event shapes are otherwise unchanged, so
  the TUI and `--debug` trace need no changes.

---

## 5. Tool semantics (pinned — no ambiguity ships)

- **EditTool** — exact-string replace in a file.
  - **0 matches → `returncode=1`** (error: search string not found).
  - **>1 match → `returncode=1`** (error: ambiguous; caller must add unique
    context). Mirrors Claude Code's `Edit`. No silent replace-all in this slice.
  - Exactly 1 match → replace, `returncode=0`.
- **WriteTool** — raw create/overwrite of a file, `returncode=0` on success.
  - The "look before you overwrite" rule stays **prompt-level guidance** (the base
    prompt already states it). A hard read-before-write gate needs read-tracking
    state `dn` does not have yet — **explicitly deferred** to a follow-up, not
    built here.
- **ReadTool** — whole-file read, returns contents as `output`.
  - **`offset`/`limit` are cut for this slice (YAGNI).** Bash `sed -n` still
    covers ranges. Add params only when `Edit` demonstrably needs line context.

---

## 6. Where it plugs in (construction sites)

`build_registry()` is called and the resulting list threaded into the model and
agent at the same construction sites that already thread `base_block` /
`persona_block` (per the base-prompt plan): the standalone coding path
(`run_traced.py`), and the ACP coding path (`acp_agent.py`). The chat path
(`chat_handler.py`) is a free-text completion with **no tool loop** and is **out
of scope** for this slice — tools only matter on the coding/agent path.

Exact call sites and signatures are for the implementation plan to pin; the
seam's public surface is: `StreamingLitellmModel(..., registry=build_registry())`
and `TracingAgent(..., registry=<same list>)`.

---

## 7. Base-prompt reconciliation

§1.2 of the gap analysis intentionally **dropped** two tool-coupled lines from the
base prompt because they were not yet true:

- "prefer dedicated file/search tools over shell"
- "independent tool calls in parallel"

This slice makes the **first** true. Re-add the file-tools-over-shell line to
`harness/base_prompt.py` as part of this work. **Do NOT** re-add the parallel-calls
line — parallel tool calls are a deferred follow-up.

---

## 8. Test plan

- **Per-tool units** (`tests/`): `tool.execute({...}, env)` →
  - Read: hit (contents) and miss (returncode 1).
  - Write: create and overwrite (returncode 0).
  - Edit: single-match replace; 0-match → returncode 1; multi-match → returncode 1.
- **Parse:** registry schemas are sent on both the streaming and blocking paths;
  unknown tool name → `FormatError` naming the tool; malformed JSON args →
  `FormatError`; a bash action carries **both** `command` and `tool_name`.
- **Dispatch:** bash routes to `env.execute` and a `Submitted` propagates (turn
  ends); a file tool routes to `tool.execute`; a message mixing bash + file
  actions pairs every output to the right `tool_call_id`.
- **Base prompt:** the file-tools-over-shell line is present;
  the parallel-calls line is still absent.
- **Suite green:** `.venv/bin/python -m pytest tests/ -q` (target `tests/` only);
  primary checkout stays clean (all work in the worktree, AGENTS.md #1).

---

## 9. Deferred (tracked, not built here)

1. **Parallel tool calls in one response** — the upstream loop and the dispatch
   override assume one call shape per step; true parallelism is a follow-up
   (re-adds the second base-prompt line when it lands).
2. **Write read-before-overwrite hard gate** — needs read-tracking state.
3. **`ToolSearch` / deferred tools (#62)** — only meaningful once the tool count
   bloats context; out of scope until then.

---

## 10. Provenance

All file:line claims verified against the worktree at authoring time (2026-06-27):
`actions_toolcall.py:11` (`BASH_TOOL`), `:60-63` (bash-only name + `command`
check), `:78-112` (`format_observation_messages`), `:104-106` (`tool_call_id`
pairing); `litellm_model.py:69` (`tools=[BASH_TOOL]`), `:88-97`
(response-persistence on FormatError), `:127-134` (`_parse_actions` +
`finish_reason`), `:41-43` (observation_template fields); `streaming_model.py:43`
(streaming `tools=[BASH_TOOL]`), `:59` (empty-stream fallback to `super()._query`);
`default.py:154` (`env.execute(action)` dispatch); `tracing_agent.py:147-165`
(the `execute_actions` override), `:162` (`output_bytes`); `local.py:48-50`
(`COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` → `Submitted`). Re-verify before acting
— docs can lag a phase (AGENTS.md #6).
