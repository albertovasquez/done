# Design — the `dn` base system prompt

**Status:** design (approved in brainstorming; no implementation yet). Hand-off
to writing-plans.
**Date:** 2026-06-27
**Author:** Alberto Vasquez (with Claude Opus 4.8)
**Scope:** give DoneDone its own small, authored **base system prompt** — durable
behavioral policy (security posture + agent discipline) plus a runtime
environment block — injected on **both** the coding path and the chat path.
**Builds on:**
`docs/superpowers/specs/2026-06-27-claude-code-system-prompt-gap-analysis.md`
(Part 1, "mine now"). This spec implements that 25%; it does **not** touch the
roadmap items (multi-tool surface, subagents, scheduling, recall-index memory).

---

## 0. TL;DR

`dn` has no authored base behavioral prompt today. The coding path runs on the
upstream one-liner (*"You are a helpful assistant that can interact with a
computer"*, `upstream/.../default.yaml:3`) plus a bash-format contract; the chat
path (`harness/chat_handler.py:81`) has **no** base prompt at all — only an
optional persona block.

This design adds one module, `harness/base_prompt.py`, exposing a static policy
constant and a pure `render_base_prompt(...)` function. The rendered block is
injected at the **single existing chokepoint** on the coding path
(`harness/tracing_agent.py:48`) and as the system message on the chat path
(`harness/chat_handler.py:81`). It is `dn`'s identity: **always present**, **not**
file-backed, **not** user-overridable, **not** content-gated (unlike
persona/memory/skills).

---

## 1. Decisions locked in brainstorming

| # | Decision | Rationale |
|---|---|---|
| D1 | **Code constant**, not a bundled file | The base prompt is `dn`'s identity, not user content. No I/O, no empty-file no-op trap, always present. Diverges from the file-based content-layer pattern *on purpose*. |
| D2 | **All three pieces**: security + discipline + environment | A complete, coherent single block (Claude-Code-shaped), not a fragment. |
| D3 | **Env reconciliation decided on evidence** (below) | Upstream `<system_information>` is just a raw `uname` line; overlap with `dn`'s env block is essentially only the OS string. No real two-sources conflict. |
| D4 | Applies to **both** coding and chat paths | The chat path has no base prompt today; this is the larger behavioral win. |

---

## 2. Architecture & components

One new module: `harness/base_prompt.py`.

```python
# harness/base_prompt.py  (shape, not final text)

BASE_POLICY = """\
…security posture…

…agent-discipline prose…
"""   # static; never changes per run

def render_base_prompt(*, model_id: str, cwd: str, cutoff: str,
                       system_line: str) -> str:
    """Return the full base block: static policy + a rendered # Environment
    section filled with runtime values. Pure function — values in, string out,
    no I/O. The single source of the base-prompt text for BOTH dispatch paths."""
```

- **`BASE_POLICY`** — the durable text (§4.1, §4.2).
- **`render_base_prompt(...)`** — appends a `# Environment` section (§4.3) built
  from the passed runtime values. Pure: trivially unit-testable, no filesystem,
  no globals.

**Boundary check (isolation/clarity):** one job (produce the base block), one
interface (the render function), one dependency (the runtime values the caller
passes). Callers never read its internals; prose can change without touching any
seam. It mirrors how `persona.py`/`memory.py`/`skills.py` are *consumed* (caller
injects), while deliberately **not** mirroring how they're *sourced* (they read
files; this is a constant — D1).

---

## 3. Data flow / injection (both paths)

The same `render_base_prompt(...)` call feeds both paths — single source of truth
for the text.

### 3.1 Coding path — `harness/tracing_agent.py:48` (`_render_template`)

Today `_render_template` appends, in order, onto the rendered system template:
`persona_block` → `memory_block` → `skill_block` (lines 50–55), guarded by the
identity match `template is self.config.system_template` (line 49) so it only
augments the **system** message, never the instance message.

Change: prepend the rendered base block at the **front** of that chain.

```
rendered system message =
    upstream system_template
  + BASE BLOCK              ← new (render_base_prompt output)
  + persona_block
  + memory_block
  + skill_block
```

`TracingAgent.__init__` already takes `persona_block`/`skill_block`/`memory_block`
(lines 35–41). Add a `base_block: str` parameter the same way; the agent stays
decoupled from `base_prompt.py` (it receives a string, not the module). The
caller that constructs `TracingAgent` (in the runner / ACP session path) computes
`render_base_prompt(...)` once and passes it in.

### 3.2 Chat path — `harness/chat_handler.py:81`

Today the messages list is:
`([{system: persona_block}] if persona_block else []) + history + [user]`
(lines 81–83) — i.e. **no base prompt**, system message present only if a persona
exists.

Change: the base block becomes the system message, with persona appended after
it:

```
messages =
    [{role: system, content: base_block + persona_block}]
  + history
  + [{role: user, content: prompt}]
```

The base block is always non-empty (D1), so the chat path now **always** has a
system message — a deliberate behavior change (see §6).

`ChatHandler.__init__` (line 51) currently takes `persona_block`; add a
`base_block` parameter passed from the same construction site that builds the
handler, using the same `render_base_prompt(...)` output as the coding path.

---

## 4. Content of the prompt

### 4.1 Security posture (lift near-verbatim)

Adopt the Claude Code agentic-CLI security block essentially as-is — it matches
`AGENTS.md`/`CLAUDE.md` intent and supports the CTF/pentest use cases `dn` is
meant to serve:

> Assist with authorized security testing, defensive security, CTF challenges,
> and educational contexts. Refuse destructive techniques, DoS, mass targeting,
> supply-chain compromise, or detection evasion for malicious purposes. Dual-use
> tools require clear authorization context (pentest engagement, CTF, security
> research, defensive use).

### 4.2 Agent discipline (portable prose)

- **Report outcomes faithfully** — failures stated with their output, skipped
  steps named, "done" claimed only when verified. (Composes with the bundled
  `verification-before-completion` skill, `README.md:97`.)
- **Confirm hard-to-reverse / outward-facing actions** before doing them;
  approval in one context doesn't carry to the next.
- **Before deleting/overwriting, look at the target**; if it contradicts how it
  was described, surface that instead of proceeding.
- **Reference code as `file_path:line_number`.**
- **Match the surrounding code's style/idiom/comment density.** (Reinforces
  `AGENTS.md` #5.)

Tool-coupled lines from the source prompt are **omitted** (e.g. "prefer dedicated
file tools over shell", "parallel tool calls") — `dn` has only the `bash` tool
today; those belong to the roadmap's multi-tool item, not this block.

### 4.3 Environment (rendered at runtime)

`render_base_prompt(...)` emits a labeled `# Environment` block:

```
# Environment
- Working directory: {cwd}
- Model: {model_id}
- Knowledge cutoff: {cutoff}
- OS: {system_line}
```

Values come from the caller (the construction site already knows the model id and
cwd; cutoff is a `dn` constant; `system_line` is a readable OS string). Concretely:
`model_id` from the resolved worker model (`harness/model_resolve.py`); `cwd` from
the agent's working directory (`--cwd`); `cutoff` a module-level constant in
`base_prompt.py` (bump it when the model lineup changes); `system_line` from
`platform.platform()` (a readable one-liner, distinct from the raw upstream
`uname` fields). The plan resolves the exact call sites; if any value isn't
cleanly available at a site, that's a finding for the plan, not a silent default.

---

## 5. The environment reconciliation (resolved with evidence)

**The flagged open question, now decided.** The upstream `instance_template`
injects `<system_information>` as a single line:
`{{system}} {{release}} {{version}} {{machine}}` (`default.yaml:42-44`) — raw
`uname`-style detail (e.g. `Darwin 25.5.0 … arm64`). It carries **no** model id,
**no** knowledge cutoff, **no** labeled cwd.

**Decision (approved):** the `dn` base prompt owns a clean, labeled
`# Environment` block (§4.3); the upstream `<system_information>` line is **left
untouched** on the coding path. They don't conflict — upstream is low-level OS
detail, ours is the labeled, model-aware block. The only redundancy is the OS
string appearing twice on the coding path, which is cosmetic and **not** worth a
change near `upstream/` (keeps us clear of AGENTS.md #4). The chat path receives
environment for the first time, from the same render call.

Result: one canonical, `dn`-authored environment block; upstream stays as-is; no
drift because the fields are essentially disjoint.

---

## 6. The one behavior change to call out

Persona/memory/skills preserve a **byte-identical no-op** when unused (an unedited
default persona changes nothing — `persona.py:24`, `memory.py:90`). The base
prompt is **always on** by design (D1), so the baseline shifts by exactly the
base block, **once**, for every run:

- Coding path: system message becomes `upstream template + base block` (was
  `upstream template` alone when no persona/skills).
- Chat path: gains a system message where it had none.

This is intended and is the whole point of the change. Tests assert the *new*
baseline (§7), not the old no-op.

---

## 7. Testing

Pure-function tests on `render_base_prompt()` (no mocks — it takes plain values):

1. **Static sections always present** — output contains the security posture and
   each discipline rule, for any inputs.
2. **Env interpolation** — given `model_id`/`cwd`/`cutoff`/`system_line`, the
   `# Environment` block contains exactly those values.
3. **Single source of truth** — both paths, given identical inputs, embed
   identical base text (assert the chat-path system message and the coding-path
   appended segment contain the same `render_base_prompt(...)` string).
4. **New baseline (coding path)** — with persona/memory/skills empty, the rendered
   system message equals `upstream template + base block` (the shifted baseline,
   §6), proving the base block is the *only* added segment.
5. **New baseline (chat path)** — with no persona, the messages list begins with a
   single system message equal to the base block (was: no system message).

Run from the worktree root: `.venv/bin/python -m pytest tests/ -q`.

---

## 8. Out of scope (explicitly)

- The roadmap 75%: multi-tool surface, subagents/Workflow, deferred tools,
  recall-index memory, scheduling, compaction, browser. Tracked in the gap
  analysis; not here.
- User-overridability of the base prompt (D1 — it's identity, not content). If a
  future need arises, that's a separate design.
- Any edit under `upstream/` (AGENTS.md #4). The base block is composed in
  `harness/`; the upstream template and `<system_information>` are unchanged.

---

## 9. Provenance

File:line references verified against the worktree at authoring time
(2026-06-27): `default.yaml:3` (upstream base one-liner), `:42-44`
(`<system_information>` fields), `tracing_agent.py:48-55` (injection chokepoint +
append order + identity guard), `:35-41` (existing block params),
`chat_handler.py:51,81-83` (chat-path messages, persona-only system message),
`persona.py:24` / `memory.py:90` (content-gated no-op). Re-verify before
implementing — docs can lag (`AGENTS.md` #6).
```