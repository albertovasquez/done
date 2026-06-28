# Research brief: re-architecting the router for expandable flows + lazy skill discovery

**Date:** 2026-06-28
**Status:** Research only — no design locked, no implementation. Decision input for a future spec.
**Question being answered:** *Can our router be re-architected to be more robust and expandable — so new flow families (SEO, marketing, copywriting, …) can be added without rewriting the router each time, and skills are discovered/injected on demand instead of all being sent as context?*

The short answer: **yes, and the change is smaller than it looks**, because the seams we'd need already exist in the codebase. The current router is the *opposite* of the proven pattern (Anthropic's progressive disclosure), so the work is mostly inverting one default — eager injection → lazy discovery — plus adding a small invocation-model vocabulary to skill frontmatter. The `disable-model-invocation` flag is the anchor primitive, but it's one cell of a 2-axis matrix we should adopt as a set.

---

## 1. Where we are today (grounded in the code)

The harness runs a **classify-then-dispatch** pipeline. A cheap model (`gpt-5.4-mini`) classifies a prompt; a hard branch on `task_type` decides what happens.

**The flat catalog.** `skills.load_catalog()` (`skills.py:43`) scans each skill dir and returns a flat `list[tuple[name, description]]`. `_parse_skill_md()` (`skills.py:30`) parses the *full* YAML frontmatter but the catalog **reads only `name` + `description` and discards the rest** (`skills.py:55`). There is today no way to express *anything* about a skill beyond its one-line description — no invocation rules, no grouping, no relationships.

**Eager body injection.** When the router selects skills (`cls.skills`), `skills.compose()` (`skills.py:71`) reads each chosen skill's **full body from disk and concatenates it** under a `# Available Skills` header (`skills.py:92`). Those full bodies go straight into the agent's system context. This is eager and total: a selected skill's entire body is always injected.

**The agent is blind to the menu.** `render_base_prompt()` (`base_prompt.py:47`) emits policy + environment + persona-files only. The agent is **never told which skills exist** — it only ever sees the bodies the router pre-picked. There is no entrance, no index, and no tool to pull a skill on demand. The tool registry (`tools/registry.py:14`) is `[Bash, Read, Write, Edit]` — no `load_skill`.

**The dispatch branch** (`run_traced.py:98–115`, mirrored in `acp_agent.py`):
- `chat_question` → ChatHandler (answer only, no skills)
- `ambiguous` → clarify, don't run
- everything else (`code_explain`, `code_fix`, `code_feature`, `code_refactor`, `ops_task`) → load skills eagerly → run the agent

### Why this fails the goals you named

| Goal | Why today's design blocks it |
|---|---|
| **Expandable flows** | `task_type` is a fixed 7-value enum baked into `router.py`. A new family (SEO/marketing/copywriting) means editing the enum *and* the dispatch branch every time. Flows are not a first-class concept. |
| **Skills not all sent as context** | `compose()` injects full bodies of every selected skill. Scaling to dozens of marketing/SEO skills means dumping dozens of bodies. The flat catalog *descriptions* already all go to the router; bodies go to the agent. Nothing is dormant. |
| **Discoverability ("ask-matt-docs entrance")** | The agent can't discover skills — it has no menu and no pull mechanism. Discovery happens once, in the cheap router, against name+description only. There's no docs-aware entrance. |
| **"dn jumps to work when I only asked a question"** | A symptom, not the root: a question routes to `code_explain` → the agent path → full tools, prompt that assumes work. There's no "answer-vs-act" gate. A richer invocation/flow model gives us the natural place to add one. |

---

## 2. The proven pattern to converge on (external grounding)

Anthropic's own Skills model (code.claude.com/docs/en/skills) is exactly progressive disclosure — the thing you're asking for, already validated at scale:

- **Names always in context. Descriptions in context (truncated when many). Bodies load only on use.** "A skill's body loads only when it's used, so long reference material costs almost nothing until you need it." This is the "don't send all the bodies" property verbatim.
- **Invocation is a 2-axis matrix, not one flag:**
  - `disable-model-invocation: true` → only the *user* can invoke (via `/name`). For side-effecting / timing-sensitive work (`/deploy`, `/commit`). The model still *sees it exists*.
  - `user-invocable: false` → only the *model* can invoke. Default = both can.
- **`allowed-tools`** → pre-approve specific tools while a skill is active (the "later" field you flagged).
- **`context: fork` + `agent:`** → run the skill in an *isolated subagent* (e.g. `Explore`), so its work never pollutes the main context, and results are summarized back. This is Anthropic's strongest context-economy lever and maps cleanly onto our existing subagent/persona machinery.
- **Progressive disclosure within a skill:** `SKILL.md` stays small and points to `reference.md`/`examples.md` loaded only when needed.

Matt Pocock's `ask-matt` is the *human-facing* version of the same idea: a `disable-model-invocation: true` skill whose body is a **hand-authored map of skills and the flows between them** ("main flow", "on-ramps", "standalone"). It's curated routing *knowledge the model reads on demand* — not an automatic classifier. The lesson for us: **richness lives in relationships + prose, not in a flat description list.** Our router is flat precisely because our catalog is flat.

---

## 3. What we already have that makes this cheap

The investigation surfaced seams that mean we are *not* starting from scratch:

1. **Frontmatter is already fully parsed.** `_parse_skill_md` returns the entire YAML dict (`skills.py:40`). Adding fields = reading keys already in hand. No parser change.
2. **`persona.toml` is already the per-persona config surface — and already lists skill roots.** `persona_config.py:16` `read_skills()` reads a `skills` key (extra skill roots) and `read_name()` reads `name`. The established pattern for new per-persona config is *a new best-effort reader function here*. **Flows fit this mold exactly** — `read_flows(workspace_dir)` alongside `read_skills`.
3. **Skills resolve from multiple roots with precedence.** `paths.skills_dirs()` returns `[bundled, user-dir]`, and persona.toml can add more. A "flow" can simply be **a skills subdirectory** (e.g. `skills/marketing/*`) — no new storage concept needed.
4. **The tool registry is trivially extensible.** `build_registry()` (`tools/registry.py:14`) returns a plain list; a `load_skill` tool is ~30 lines and one list entry (`base.py` Protocol: `name`, `schema`, `display_label`, `execute`).
5. **Subagents/personas already exist.** `context: fork`-style isolated execution has a home — we already dispatch work to isolated seats with their own model binding (`done.conf [agents.<id>]`).
6. **The compose chokepoint is single.** All skill-body assembly funnels through `skills.compose()` → `persona.compose_context()` (`persona.py:118`). Lazy injection is a change at *one* function, not scattered.

---

## 4. The architecture (research-level, three layers)

The re-architecture is three independent layers. Each is useful alone; together they deliver the full goal. **Layer A is the load-bearing foundation** — B and C both depend on it.

### Layer A — Skill invocation model (the foundation, anchored on `disable-model-invocation`)

Teach the skills layer to read and carry an invocation vocabulary, and make the catalog *structured* instead of flat.

- Parse from frontmatter (defaults preserve today's behavior exactly):
  - `disable-model-invocation: bool` (default `false`) — router may NOT auto-select; user/flow only.
  - `user-invocable: bool` (default `true`) — exposed as `/name`.
  - `flow: str | list[str]` (optional) — which flow family(ies) this skill belongs to.
  - *(later)* `allowed-tools`, `context: fork`, `agent:`.
- Change `load_catalog()` to return a **structured record** per skill (name, description, invocation flags, flow tags) instead of a bare tuple. The router filters: it only ever *auto-selects* model-invocable skills; dormant ones are visible-by-description but never auto-injected.

**Why first:** every other layer needs to know "is this skill auto-injectable, and what flow is it in." This is the cell `disable-model-invocation` lives in, generalized to the matrix.

### Layer B — Lazy skill discovery (kills the "all bodies in context" problem)

Invert the default from eager-inject to discover-then-pull.

- The agent's system prompt gains a small **menu**: skill *names + descriptions* of the available/in-flow skills (cheap — this is what Anthropic keeps in context). **Bodies are not injected up front.**
- Add a **`load_skill(name)` tool** (registry entry). The agent pulls a skill body into context *only when it decides it needs it*. `compose()` already knows how to read+format one body; the tool reuses it.
- The router's job narrows: instead of "pick skills and inject their bodies," it **selects the active flow** and seeds the menu. Selection of *individual* skills moves to the moment of need (agent-pull) — or stays router-seeded for high-confidence cases. (Section 5 is the open decision on who pulls.)

**Result:** adding 40 marketing skills costs ~40 one-line descriptions in context, not 40 bodies. Exactly the property you asked for.

### Layer C — Pluggable flows + the `ask-done` entrance

Make "flow" a first-class, data-driven concept so new families need no router edits.

- A **flow** = a named group of skills (by `flow:` tag and/or a skills subdirectory) + an optional curated map (an `ask-matt`-style doc describing the path: main flow, on-ramps, standalone). The map is the "ask-matt-docs entrance."
- **Enablement lives in `persona.toml`** via a new `read_flows()` (mirrors `read_skills`). A copywriting persona enables `["copywriting", "seo"]`; the default persona enables the engineering flow. The router only sees in-flow skills → naturally scoped context, naturally expandable.
- **`/ask-done`** = a `disable-model-invocation: true` skill (our `ask-matt`) whose body renders the enabled flows' maps. You call it when unsure; it recommends a flow/skill/persona. It is the human discoverability entrance and reuses the same flow data the router reads — **one source of truth, two consumers** (auto-router + manual `/ask-done`).
- The fixed `task_type` enum stops being the expansion axis. New families are added as *data* (a flow dir + tags + a map + a persona.toml line), not as new enum values and dispatch branches.

### How the layers answer the "answer-vs-act" symptom

With Layer A+B, a question no longer forces eager work: the agent gets a menu, not a pile of bodies, and a flow can declare an **answer-first posture** (or a dedicated `explain` flow whose skills/prompt bias toward responding before editing). The gate becomes a property of the selected flow, not a special-case in `router.py`.

---

## 5. The one genuinely open decision: who pulls a skill at runtime?

This is the fork that changes the most and is worth deciding deliberately (it was the question left open in our discussion). Both are viable on our seams.

**Option 1 — Cheap router selects (closest to today).** The `gpt-5.4-mini` pre-step reads the flow index, picks the flow + (optionally) specific skills, seeds the menu and pre-loads only high-confidence bodies. Routing intelligence stays cheap and out of the worker.
- *Pros:* minimal change to dispatch shape; cheap; deterministic; preserves the classify-then-dispatch model the codebase is built around.
- *Cons:* the cheap model is the bottleneck on discovery quality; still a one-shot guess; doesn't help the agent realize *mid-task* it needs another skill.

**Option 2 — Worker agent pulls (Anthropic's model).** The agent gets the menu + a `load_skill` tool and pulls bodies itself, progressively, including mid-task.
- *Pros:* maximal context economy and flexibility; matches the proven pattern; handles "I discovered I also need X" naturally.
- *Cons:* moves routing into the expensive worker; bigger change to the dispatch model; needs guardrails so the agent doesn't over-pull.

**Likely best: a hybrid.** Router selects the *flow* (cheap, scoping) and seeds the menu; the agent *pulls individual skill bodies* on demand within that flow (lazy, flexible). This keeps the cheap scoping we already have while getting progressive disclosure where it pays. **Recommendation: design toward the hybrid, but validate Option 2's `load_skill` tool first since it's the reusable primitive both options need.**

---

## 6. Recommended direction & rough sequencing

1. **Layer A first (foundation).** Structured catalog + invocation flags (`disable-model-invocation`, `user-invocable`, `flow`), defaults chosen so behavior is byte-identical until a skill opts in. Low risk, unlocks everything.
2. **Layer B next (the payoff).** `load_skill` tool + menu-in-prompt + invert `compose()` to lazy. This is where the "skills not all in context" win lands. Decide §5 here.
3. **Layer C last (expandability + entrance).** `read_flows()` in `persona.toml`, flow = tag/dir + curated map, `/ask-done`. After this, SEO/marketing/copywriting are added as *data*, not router edits.

Each layer is independently shippable and testable, and each preserves the no-op for personas that don't opt in — consistent with how persona/memory/flags were rolled out before.

---

## 7. Risks & watch-fors

- **Backward compatibility:** defaults must make existing skills behave exactly as today (model-invocable, eager-equivalent) until they opt into flags. The 4 current bundled skills carry no new frontmatter — they must keep working untouched.
- **Two sources of truth drift:** if a flow's curated map (the `ask-matt`-style doc) is hand-authored separately from the `flow:` tags, they can diverge. Mitigation: derive as much of the map as possible from frontmatter; keep prose minimal.
- **Agent over-pulling (Option 2):** a `load_skill` tool needs a budget/guard so the agent doesn't load everything "just in case" — defeating the purpose.
- **Cheap-model ceiling (Option 1):** discovery quality is capped by `gpt-5.4-mini`. The `ask-done` map helps by giving it structure to reason over, not just flat descriptions.
- **Don't fork the enum-and-branch pattern further:** the whole point is to *stop* growing `task_type`. Resist adding `flow == "seo"` branches in `run_traced.py`; flows must be data-driven dispatch.
- **Verify config home:** `persona.toml` is non-model config only (model is single-homed in `done.conf [agents.<id>]`). Flows belong in `persona.toml`; do not put them in `done.conf`.

---

## 8. Bottom line

The router *can* be re-architected to be robust and expandable, and the codebase is unusually well-positioned for it: the frontmatter is already parsed, `persona.toml` is already the config home and already lists skill roots, the compose path is a single chokepoint, and the tool registry is trivially extensible. The work is **inverting one default (eager → lazy) and adding a small invocation/flow vocabulary** — converging on Anthropic's proven progressive-disclosure model — rather than rebuilding the pipeline. `disable-model-invocation` is the right anchor; adopt it as part of the 2-axis invocation matrix, layer lazy discovery on top, and make flows data-driven so SEO/marketing/copywriting arrive as content, not code.

**Next step if you want to proceed:** turn this into a full design spec for **Layer A** (the foundation), since it gates the rest and is the lowest-risk, highest-leverage starting point.
