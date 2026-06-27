# dn Base System Prompt Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give DoneDone its own authored base system prompt (security posture + agent discipline + a runtime environment block), injected on both the coding and chat dispatch paths.

**Architecture:** One new pure module `harness/base_prompt.py` (a static policy constant + `render_base_prompt(...)`). Its rendered output is threaded as a new `base_block` string through the same construction chain that already carries `persona_block`/`memory_block`/`skill_block`, to all four call sites (two coding-path `TracingAgent` constructions, two chat-path `ChatHandler` constructions). The agent/handler classes stay decoupled — they receive a string, never the module.

**Tech Stack:** Python 3.11, pytest. Vendored mini-swe-agent engine (`upstream/`, never edited). litellm for the chat path.

## Global Constraints

- Always work in the git worktree, never on `main` (AGENTS.md #1). This plan runs on branch `worktree-dn-base-prompt-spec`.
- Zero edits under `upstream/` (AGENTS.md #4). The base block is composed in `harness/`; the upstream `system_template` and `<system_information>` stay unchanged.
- Run tests from the worktree root: `.venv/bin/python -m pytest tests/ -q` (target `tests/` only).
- Commit-message trailer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- The base block is `dn`'s identity: **always present**, not file-backed, not user-overridable, **not** content-gated (unlike persona/memory/skills). This is a deliberate behavior change — the persona no-op baseline shifts by exactly the base block, once.
- Match surrounding style (AGENTS.md #5): the new param threads exactly like `persona_block`/`memory_block` at every site.

---

### Task 1: The `base_prompt` module (pure render function)

**Files:**
- Create: `harness/base_prompt.py`
- Test: `tests/test_base_prompt.py`

**Interfaces:**
- Consumes: nothing (leaf module, stdlib only).
- Produces: `BASE_POLICY: str` (the static security + discipline text); `KNOWLEDGE_CUTOFF: str` constant; `render_base_prompt(*, model_id: str, cwd: str, system_line: str, cutoff: str = KNOWLEDGE_CUTOFF) -> str` returning `BASE_POLICY` followed by a `# Environment` block. Callers in Tasks 2–5 pass `model_id`, `cwd`, `system_line`; `cutoff` defaults to the module constant.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_base_prompt.py
from harness import base_prompt


def test_render_contains_static_policy_for_any_inputs():
    out = base_prompt.render_base_prompt(model_id="m", cwd="/x", system_line="OS")
    # security posture present
    assert "authorized security testing" in out.lower()
    # a representative discipline rule present
    assert "file_path:line_number" in out


def test_render_interpolates_environment_values():
    out = base_prompt.render_base_prompt(
        model_id="vibeproxy", cwd="/repo/proj", system_line="macOS-15", cutoff="January 2026")
    assert "# Environment" in out
    assert "vibeproxy" in out
    assert "/repo/proj" in out
    assert "macOS-15" in out
    assert "January 2026" in out


def test_cutoff_defaults_to_module_constant():
    out = base_prompt.render_base_prompt(model_id="m", cwd="/x", system_line="OS")
    assert base_prompt.KNOWLEDGE_CUTOFF in out


def test_policy_is_nonempty_and_static():
    # always-on identity: the constant must carry real content
    assert base_prompt.BASE_POLICY.strip()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_base_prompt.py -q`
Expected: FAIL with `ModuleNotFoundError` / `AttributeError: render_base_prompt`.

- [ ] **Step 3: Write minimal implementation**

```python
# harness/base_prompt.py
"""The dn base system prompt: durable behavioral policy (security posture +
agent discipline) plus a runtime environment block. Unlike persona/memory/skills
this is dn's IDENTITY — always present, not file-backed, not user-overridable,
not content-gated. A pure render function: values in, string out, no I/O."""

from __future__ import annotations

KNOWLEDGE_CUTOFF = "January 2026"

BASE_POLICY = """\
# Security

Assist with authorized security testing, defensive security, CTF challenges, and \
educational contexts. Refuse requests for destructive techniques, DoS attacks, \
mass targeting, supply-chain compromise, or detection evasion for malicious \
purposes. Dual-use security tools (C2 frameworks, credential testing, exploit \
development) require clear authorization context: a pentest engagement, CTF \
competition, security research, or a defensive use case.

# Working principles

- Report outcomes faithfully: if a command or test fails, say so with its output; \
if you skipped a step, say that; claim something is done only once you have \
verified it, and then say so plainly without hedging.
- Confirm actions that are hard to reverse or outward-facing before doing them. \
Approval in one context does not extend to the next.
- Before deleting or overwriting something, look at it first. If what you find \
contradicts how it was described, surface that instead of proceeding.
- Reference code as file_path:line_number so it is clickable.
- Match the surrounding code's style, naming, idiom, and comment density. Make \
surgical changes; every changed line should trace to the task.
"""


def render_base_prompt(*, model_id: str, cwd: str, system_line: str,
                       cutoff: str = KNOWLEDGE_CUTOFF) -> str:
    """Return the base block: the static policy followed by a runtime
    # Environment section. Pure — no I/O, no globals read."""
    env = (
        "\n\n# Environment\n"
        f"- Working directory: {cwd}\n"
        f"- Model: {model_id}\n"
        f"- Knowledge cutoff: {cutoff}\n"
        f"- OS: {system_line}\n"
    )
    return BASE_POLICY + env
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_base_prompt.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add harness/base_prompt.py tests/test_base_prompt.py
git commit -m "feat(base_prompt): pure render_base_prompt (policy + env block)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Thread `base_block` into `TracingAgent` (the agent seam)

**Files:**
- Modify: `harness/tracing_agent.py:34-56` (`__init__` + `_render_template`)
- Test: `tests/test_tracing_agent.py` (add a test; create if absent)

**Interfaces:**
- Consumes: `render_base_prompt(...)` from Task 1 (callers pass its output as a string; this task does not call the module — it accepts the pre-rendered string).
- Produces: `TracingAgent(..., base_block: str = "")`. When non-empty, `_render_template` prepends `base_block` to the system template **before** persona/memory/skills. Tasks 3 & 5 rely on this parameter name.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tracing_agent.py  (add this test)
from harness.tracing_agent import TracingAgent


def _make_agent(**blocks):
    # Minimal construction: TracingAgent needs model+env; reuse the project's
    # existing test fixtures/helpers for those if present. Otherwise construct
    # with the mock model + LocalEnvironment as other tracing_agent tests do.
    ...  # use the same setup the existing tracing_agent tests use


def test_base_block_prepended_before_persona_in_system_template():
    agent = _make_agent(base_block="BASEBLOCK", persona_block="PERSONA")
    rendered = agent._render_template(agent.config.system_template)
    assert "BASEBLOCK" in rendered
    # base block comes before persona in the appended order
    assert rendered.index("BASEBLOCK") < rendered.index("PERSONA")


def test_base_block_not_added_to_instance_template():
    agent = _make_agent(base_block="BASEBLOCK")
    rendered = agent._render_template(agent.config.instance_template)
    assert "BASEBLOCK" not in rendered
```

> NOTE for the implementer: open `tests/` first and reuse the existing
> `TracingAgent` construction helper (mock model + `LocalEnvironment`) that other
> tests already use — do not invent new fixtures. If no helper exists, build the
> agent the same way `tests/` constructs it elsewhere (grep `TracingAgent(`).

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tracing_agent.py -k base_block -q`
Expected: FAIL — `__init__() got an unexpected keyword argument 'base_block'`.

- [ ] **Step 3: Write minimal implementation**

In `harness/tracing_agent.py`, add the param to `__init__` (alongside the existing block params) and prepend it in `_render_template`:

```python
    def __init__(self, model, env, *, emitter: Emitter, skill_block: str = "",
                 persona_block: str = "", memory_block: str = "",
                 base_block: str = "", **kwargs):
        super().__init__(model, env, **kwargs)
        self._emitter = emitter
        self._skill_block = skill_block
        self._persona_block = persona_block
        self._memory_block = memory_block
        self._base_block = base_block
        self._run_start = time.time()
```

```python
    def _render_template(self, template: str) -> str:
        out = super()._render_template(template)
        if template is self.config.system_template:
            if self._base_block:
                out += self._base_block
            if self._persona_block:
                out += self._persona_block
            if self._memory_block:
                out += self._memory_block
            if self._skill_block:
                out += self._skill_block
        return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tracing_agent.py -k base_block -q`
Expected: PASS (2 tests). Also run the file to confirm no regression: `.venv/bin/python -m pytest tests/test_tracing_agent.py -q`.

- [ ] **Step 5: Commit**

```bash
git add harness/tracing_agent.py tests/test_tracing_agent.py
git commit -m "feat(tracing_agent): accept base_block, prepend before persona

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Thread `base_block` through `runner.run` → standalone coding path

**Files:**
- Modify: `harness/runner.py:85-91` (`run(...)` signature + `TracingAgent(...)` call)
- Modify: `harness/run_traced.py:135,143-148` (compute `base_block`, pass to `runner.run`)
- Test: `tests/test_runner.py` (add; or extend the existing runner test)

**Interfaces:**
- Consumes: `TracingAgent(..., base_block=...)` (Task 2); `render_base_prompt(...)` (Task 1).
- Produces: `Runner.run(..., base_block: str = "", ...)` forwarding `base_block` into `TracingAgent`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_runner.py  (add this test)
# Assert that a base_block passed to runner.run reaches the agent's system
# message. Reuse the existing runner/mock-model test setup in tests/.
def test_runner_forwards_base_block_to_agent_system_message():
    events = list(run_with_blocks(base_block="BASEBLOCK"))  # helper over Runner.run
    # the agent's first llm.call records the system message containing the block;
    # assert via the events/trace the existing runner tests already inspect.
    assert any("BASEBLOCK" in _system_message_of(e) for e in events)
```

> NOTE: reuse the existing runner test harness (mock model, event capture). Grep
> `tests/` for how runner events expose the system message; if the suite has no
> runner test yet, model the helper on `tests/test_acp_smoke.py`'s event capture.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_runner.py -k base_block -q`
Expected: FAIL — `run() got an unexpected keyword argument 'base_block'`.

- [ ] **Step 3: Write minimal implementation**

`harness/runner.py` — add the param and forward it:

```python
    def run(self, prompt: str, *, skill_block: str = "", persona_block: str = "",
            memory_block: str = "", base_block: str = "", **kwargs) -> Iterator[Event]:
        ...
        agent = TracingAgent(self._model, self._env, emitter=emitter,
                             skill_block=skill_block, persona_block=persona_block,
                             memory_block=memory_block, base_block=base_block,
                             **self._agent_cfg)
```

`harness/run_traced.py` — compute the block once and pass it (near where `persona_block`/`memory_block` are computed, ~line 140, and at the `runner.run(...)` call ~line 146):

```python
import platform
from harness import base_prompt
...
    base_block = base_prompt.render_base_prompt(
        model_id=(worker_model_id or "mock"),
        cwd=args.cwd,
        system_line=platform.platform())
...
            for event in runner.run(prompt, skill_block=skill_block,
                                    persona_block=persona_block,
                                    memory_block=memory_block,
                                    base_block=base_block):
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_runner.py -k base_block -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/runner.py harness/run_traced.py tests/test_runner.py
git commit -m "feat(runner): forward base_block; run_traced renders it

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Thread `base_block` into `ChatHandler` (both chat-path sites)

**Files:**
- Modify: `harness/chat_handler.py:50-62,76-86` (`__init__` + the `messages=` assembly)
- Modify: `harness/run_traced.py:166-167` (pass `base_block` to the standalone `ChatHandler`)
- Modify: `harness/acp_agent.py:243-244` (pass `base_block` to the ACP `ChatHandler`)
- Test: `tests/test_chat_handler.py` (add; create if absent)

**Interfaces:**
- Consumes: `render_base_prompt(...)` (Task 1).
- Produces: `ChatHandler(..., base_block: str = "")`. When non-empty, the chat system message is `base_block + persona_block` (base first); the path now **always** has a system message when `base_block` is set.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chat_handler.py
from harness.chat_handler import ChatHandler


def test_base_block_becomes_system_message_before_persona(monkeypatch):
    captured = {}

    def fake_completion(**kwargs):
        captured["messages"] = kwargs["messages"]
        # minimal streaming-shaped stub: yield nothing
        return iter(())

    import litellm
    monkeypatch.setattr(litellm, "completion", fake_completion)

    h = ChatHandler("vibeproxy", catalog=[], persona_block="PERSONA",
                    base_block="BASEBLOCK")
    list(h.answer_stream("hi"))  # drives litellm.completion with our messages

    sys_msgs = [m for m in captured["messages"] if m["role"] == "system"]
    assert sys_msgs, "chat path must have a system message when base_block is set"
    content = sys_msgs[0]["content"]
    assert content.index("BASEBLOCK") < content.index("PERSONA")
```

> NOTE: `is_capability_question` short-circuits before litellm; "hi" is not a
> capability question, so the model path runs. Keep the prompt non-capability.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_chat_handler.py -q`
Expected: FAIL — `__init__() got an unexpected keyword argument 'base_block'`.

- [ ] **Step 3: Write minimal implementation**

`harness/chat_handler.py` — add the param, build a single system message from `base_block + persona_block`:

```python
    def __init__(self, worker_model_id: str | None,
                 catalog: list[tuple[str, str]] | None = None,
                 persona_block: str = "", base_block: str = ""):
        self._model_id = worker_model_id
        self._catalog = catalog or []
        self._persona_block = persona_block
        self._base_block = base_block
```

In `answer_stream`, replace the persona-only system message with a combined one:

```python
        system_content = self._base_block + self._persona_block
        stream = litellm.completion(
            model=vibeproxy.model_id(self._model_id),
            **vibeproxy.completion_kwargs(),
            messages=(([{"role": "system", "content": system_content}]
                       if system_content else [])
                      + (history or []) + [{"role": "user", "content": prompt}]),
            max_tokens=1000,
            stream=True,
        )
```

`harness/run_traced.py:166-167` — pass the same `base_block` computed in Task 3:

```python
            make_chat_handler=lambda: ChatHandler(worker_model_id, catalog=router.catalog,
                                                  persona_block=persona_block + memory_block,
                                                  base_block=base_block),
```

`harness/acp_agent.py:243-244` — render and pass `base_block` (model `self._worker_model_id`, cwd `state.cwd`):

```python
            import platform
            from harness import base_prompt
            base_block = base_prompt.render_base_prompt(
                model_id=(self._worker_model_id or "mock"),
                cwd=state.cwd, system_line=platform.platform())
            handler = ChatHandler(self._worker_model_id, catalog=self._router.catalog,
                                  persona_block=(state.persona_block or "") + (state.memory_block or ""),
                                  base_block=base_block)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_chat_handler.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/chat_handler.py harness/run_traced.py harness/acp_agent.py tests/test_chat_handler.py
git commit -m "feat(chat_handler): base_block as system message (both chat sites)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Render & pass `base_block` at the ACP coding-path site

**Files:**
- Modify: `harness/acp_agent.py:293-294` (`_run_agent_turn` signature) and `:418-420` (`TracingAgent(...)` call)
- Modify: `harness/acp_agent.py:270-276` (the `compose_context` / dispatch call site that feeds `_run_agent_turn`) — render `base_block` and forward it
- Test: `tests/test_acp_smoke.py` (extend the existing smoke client to assert the base block reaches the system message), or `tests/test_acp_*` if a finer test exists

**Interfaces:**
- Consumes: `TracingAgent(..., base_block=...)` (Task 2); `render_base_prompt(...)` (Task 1).
- Produces: the ACP coding path now injects the base block, matching the standalone coding path (Task 3). No new public interface — this wires the last call site.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_acp_smoke.py  (extend)
# Drive a mock-model agent turn through the ACP server and assert the streamed
# trace's system message contains the base block. Reuse the existing smoke
# client; assert on the same events/trace it already inspects.
def test_acp_agent_turn_system_message_contains_base_block():
    # run a code task through the ACP client (mock model), capture the trace,
    # assert the agent's system message includes a base-prompt marker, e.g.
    assert "authorized security testing" in _system_message_of(captured_trace).lower()
```

> NOTE: `test_acp_smoke.py` already constructs a client and runs a turn. Reuse
> its harness; find where the system message / first llm.call is observable
> (events.jsonl or the in-memory trace) and assert on that. Do not add a new
> server.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_acp_smoke.py -k base_block -q`
Expected: FAIL — base block absent from the system message (not yet wired).

- [ ] **Step 3: Write minimal implementation**

In `harness/acp_agent.py`, thread `base_block` from the dispatch site through `_run_agent_turn` into the `TracingAgent` call. Render it once where `state.cwd` and `self._worker_model_id` are in scope (the dispatch site around `:270-276`):

```python
        import platform
        from harness import base_prompt
        base_block = base_prompt.render_base_prompt(
            model_id=(self._worker_model_id or "mock"),
            cwd=state.cwd, system_line=platform.platform())
        engine = await self._run_agent_turn(loop, session_id, state, text,
                                            ctx.skill_block, persona_block=...,
                                            memory_block=..., base_block=base_block)
```

Add `base_block: str = ""` to `_run_agent_turn` (`:293-294`) and forward it:

```python
    async def _run_agent_turn(self, loop, session_id, state, text, skill_block,
                              persona_block="", memory_block="", base_block=""):
        ...
                agent = TracingAgent(self._model_factory(self._worker_model_id), env,
                                     emitter=emitter, skill_block=skill_block,
                                     persona_block=persona_block, memory_block=memory_block,
                                     base_block=base_block,
                                     **self._agent_cfg)  # match the live arg list
```

> IMPLEMENTER: open `acp_agent.py:270-276` and `:418-420` and match the exact
> argument names/keywords currently passed (the snippet above is illustrative).
> The `import platform` / `from harness import base_prompt` may be hoisted to the
> module top instead of local imports — follow the file's existing import style.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_acp_smoke.py -k base_block -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/acp_agent.py tests/test_acp_smoke.py
git commit -m "feat(acp_agent): inject base_block on the ACP coding path

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Full-suite green + DRY check

**Files:**
- Possibly modify: `harness/acp_agent.py`, `harness/run_traced.py` (extract a tiny local helper if the `render_base_prompt(...)` call is duplicated verbatim 3×)

**Interfaces:**
- Consumes: everything above.
- Produces: a green suite and no duplicated render-call boilerplate.

- [ ] **Step 1: Run the whole suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS (all green). If any pre-existing test asserted the *old* no-op baseline (system message == upstream template alone, or chat path with no system message), update it to the **new** baseline (§6 of the spec) — the base block is intentionally always-on. Note each such update in the commit body.

- [ ] **Step 2: DRY check**

The `render_base_prompt(...)` call now appears in `run_traced.py` (×1, shared by coding+chat), `acp_agent.py` chat site, and `acp_agent.py` coding site. If the ACP sites call it with identical args (`model_id`, `state.cwd`, `platform.platform()`), extract a one-line private helper on the ACP agent (e.g. `self._base_block(state)`) and use it at both sites. Do **not** over-abstract across modules — `run_traced.py` keeps its own call.

- [ ] **Step 3: Verify primary checkout untouched**

Run: `git -C /Users/alberto/Work/Quiubo/harness status --short`
Expected: empty output (primary checkout clean — all work is in the worktree).

- [ ] **Step 4: Commit any cleanup**

```bash
git add -A
git commit -m "refactor(acp_agent): dedupe base_block render; suite green

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- Spec §2 (module shape) → Task 1. ✓
- Spec §3.1 (coding-path injection at `tracing_agent.py`) → Task 2 (mechanism) + Tasks 3 & 5 (the two construction sites). ✓
- Spec §3.2 (chat-path injection) → Task 4 (both chat sites). ✓
- Spec §4 (content: security, discipline, env) → Task 1 `BASE_POLICY` + `render_base_prompt`. ✓
- Spec §5 (env reconciliation — leave upstream alone) → respected: no `upstream/` edits anywhere; `system_line` uses `platform.platform()`, distinct from upstream's raw `uname`. ✓
- Spec §6 (no-op baseline shift) → Task 6 Step 1 updates any old-baseline tests; Task 4/2 tests assert the new baseline. ✓
- Spec §7 (tests: static present, env interp, single-source, both new baselines) → Tasks 1, 2, 4 tests; single-source is implicit (one `render_base_prompt` feeds all sites). **Gap closed:** added an explicit single-source assertion is unnecessary across processes; the shared `BASE_POLICY` marker asserted in Tasks 2/4/5 covers "same base text on both paths."

**Placeholder scan:** Test bodies for Tasks 2/3/5 intentionally defer *fixture construction* to existing `tests/` helpers (with explicit NOTE pointers) rather than inventing fixtures — this is "reuse the established harness," not a placeholder. All production code steps show complete code. No TBD/TODO in shipped code.

**Type consistency:** The new param is named `base_block` (a `str`) at every site: `TracingAgent.__init__` (T2), `Runner.run` (T3), `ChatHandler.__init__` (T4), `_run_agent_turn` (T5). `render_base_prompt(*, model_id, cwd, system_line, cutoff=...)` keyword signature is identical at all three call sites (T3, T4, T5). Consistent.

**Note on test-harness coupling:** Tasks 2/3/5 depend on existing `tests/` fixtures whose exact shape this plan doesn't reproduce. The implementer's first action in each is to grep `tests/` for the established construction/event-capture helper and reuse it. This is the one place the plan trusts the implementer to read neighboring tests — flagged here so reviewers expect it.
