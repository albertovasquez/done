"""TracingAgent: subclass of DefaultAgent that emits live events at the three
seams without editing upstream. See docs/superpowers/specs/2026-06-24-... §2.

Why reimplement instead of pure-wrap:
  - query():  parent does limit checks BEFORE the model call; a pre-super()
              llm.call would fire falsely on LimitsExceeded/TimeExceeded.
  - execute_actions(): parent's body is a list-comp over env.execute, and the
              submit command raises Submitted BEFORE returning, so a post-wrap
              never emits action.done for the final action.
  - run():    parent re-raises on uncaught exceptions, so run.finished must be
              emitted in a finally. ADDITIONALLY (v2.4.2 divergence): parent seeds
              self.messages = [system, instance] with no hook between the reset and
              the step loop, so to carry a prior transcript across turns we
              reimplement the loop here and change ONLY the seed line (prior injected
              between the fresh system and fresh instance). The exception branches
              are reproduced verbatim; FormatError/InterruptAgentFlow are subclasses
              such that `except FormatError` MUST precede `except InterruptAgentFlow`
              (Submitted/LimitsExceeded/TimeExceeded are all InterruptAgentFlow and
              are caught there, appending a role:"exit" message that ends the loop).
The duplicated lines are pinned to upstream v2.4.2 — verify against upstream's default.py before upgrading.
"""

from __future__ import annotations

import time

from minisweagent.agents.default import DefaultAgent
from minisweagent.exceptions import (FormatError, InterruptAgentFlow,
                                      LimitsExceeded, Submitted, TimeExceeded)

from harness.events import Emitter


class TracingAgent(DefaultAgent):
    def __init__(self, model, env, *, emitter: Emitter, skill_block: str = "",
                 persona_block: str = "", memory_block: str = "",
                 base_block: str = "", registry=None, **kwargs):
        super().__init__(model, env, **kwargs)
        self._emitter = emitter
        self._skill_block = skill_block
        self._persona_block = persona_block
        self._memory_block = memory_block
        self._base_block = base_block
        from harness.tools.registry import build_registry
        # registry None => default tools (mock model passes None; the AGENT still
        # needs tools to dispatch any tool_name action even when the model ignores them).
        self._registry = registry if registry is not None else build_registry()
        self._tools_by_name = {t.name: t for t in self._registry}
        self._run_start = time.time()  # tracer-local clock; parent's _start_time is set in __init__

    def _render_template(self, template: str) -> str:
        # Inject selected skills AFTER Jinja renders the base, so a skill body
        # containing {{ }}/{% %} is literal text and cannot break StrictUndefined.
        # Identity match: only the system template gets skills, never instance.
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

    def _t(self) -> float:
        return time.time() - self._run_start

    # --- seam 1: loop lifecycle ---
    def run(self, task: str = "", prior: list[dict] | None = None, **kwargs) -> dict:
        self._run_start = time.time()
        self._emitter.set_clock(self._t)  # emitter timestamps relative to this run
        self._emitter.emit("run.started", task=task,
                           model_name=getattr(self.model.config, "model_name", "unknown"),
                           cwd=getattr(self.env.config, "cwd", ""))
        exc_type = exc_str = None
        try:
            # --- reimplemented DefaultAgent.run() body, pinned to upstream v2.4.2 ---
            # ONLY divergence from upstream: `prior` injected between the fresh
            # system message and the fresh instance message.
            self.extra_template_vars |= {"task": task, **kwargs}
            self.messages = []
            self.add_messages(self.model.format_message(
                role="system", content=self._render_template(self.config.system_template)))
            self.add_messages(*(prior or []))
            self.add_messages(self.model.format_message(
                role="user", content=self._render_template(self.config.instance_template)))
            while True:
                try:
                    self.step()
                    self.n_consecutive_format_errors = 0  # reset on any clean step
                except FormatError as e:
                    self.n_consecutive_format_errors += 1
                    if 0 < self.config.max_consecutive_format_errors <= self.n_consecutive_format_errors:
                        self.add_messages(*e.messages, {
                            "role": "exit", "content": "RepeatedFormatError",
                            "extra": {"exit_status": "RepeatedFormatError", "submission": ""}})
                    else:
                        self.add_messages(*e.messages)
                except InterruptAgentFlow as e:
                    self.add_messages(*e.messages)
                except Exception as e:
                    self.handle_uncaught_exception(e)
                    raise
                finally:
                    self.save(self.config.output_path)
                if self.messages[-1].get("role") == "exit":
                    break
            return self.messages[-1].get("extra", {})
            # --- end reimplemented body ---
        except BaseException as e:  # noqa: BLE001 — record then re-raise
            exc_type, exc_str = type(e).__name__, str(e)
            raise
        finally:
            last_extra = self.messages[-1].get("extra", {}) if self.messages else {}
            self._emitter.emit(
                "run.finished",
                ok=exc_type is None,
                exit_status=last_extra.get("exit_status", "") or (exc_type or ""),
                n_calls=self.n_calls,
                total_cost=round(self.cost, 6),
                elapsed_s=round(self._t(), 3),
                exception_type=exc_type,
                exception_str=exc_str,
            )

    # --- seam 2: LLM call ---
    def query(self) -> dict:
        # Reproduce parent limit checks first (default.py:128-139) so llm.call is honest.
        if 0 < self.config.step_limit <= self.n_calls or 0 < self.config.cost_limit <= self.cost:
            raise LimitsExceeded({"role": "exit", "content": "LimitsExceeded",
                                  "extra": {"exit_status": "LimitsExceeded", "submission": ""}})
        if 0 < self.config.wall_time_limit_seconds <= int(time.time() - self._start_time):
            raise TimeExceeded({"role": "exit", "content": "TimeExceeded",
                                "extra": {"exit_status": "TimeExceeded", "submission": ""}})
        self._emitter.emit("llm.call", n=self.n_calls + 1, n_messages=len(self.messages))
        self.n_calls += 1
        message = self.model.query(self.messages)
        self.cost += message.get("extra", {}).get("cost", 0.0)
        self.add_messages(message)
        extra = message.get("extra", {})
        content = message.get("content") or ""
        preview = content[:120] if isinstance(content, str) else str(content)[:120]
        self._emitter.emit("llm.return", n=self.n_calls,
                           cost=round(extra.get("cost", 0.0), 6),
                           n_actions=len(extra.get("actions", [])),
                           content_preview=preview)
        return message

    # --- seam 3: tool dispatch (bash via env.execute; file tools via Tool.execute) ---
    def execute_actions(self, message: dict) -> list[dict]:
        outputs = []
        for action in message.get("extra", {}).get("actions", []):
            name = action.get("tool_name", "bash")   # missing => bash (mock back-compat)
            tool = self._tools_by_name.get(name)
            if tool is None:
                # Parse already rejects unknown names; guard the hand-built/ACP path
                # so a stray name is a FormatError, not an uncaught run-killer.
                raise FormatError({"role": "user", "content": f"Unknown tool '{name}'.",
                                   "extra": {"interrupt_type": "FormatError"}})
            label = action.get("command") if name == "bash" else tool.display_label(action.get("args", {}))
            self._emitter.emit("action", command=label or "")
            if name == "bash":
                try:
                    output = self.env.execute(action)
                except Submitted:
                    # The submit command finished successfully; env raised before
                    # returning. Emit the done event, then re-raise so the loop ends.
                    self._emitter.emit("action.done", returncode=0, output_bytes=0)
                    raise
            else:
                output = tool.execute(action.get("args", {}), self.env)
            outputs.append(output)
            self._emitter.emit("action.done",
                               returncode=output.get("returncode", -1),
                               output_bytes=len(str(output.get("output", "")).encode("utf-8")))
        return self.add_messages(
            *self.model.format_observation_messages(message, outputs, self.get_template_vars())
        )
