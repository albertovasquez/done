"""TracingAgent: subclass of DefaultAgent that emits live events at the three
seams without editing upstream. See docs/superpowers/specs/2026-06-24-... §2.

Why reimplement instead of pure-wrap:
  - query():  parent does limit checks BEFORE the model call; a pre-super()
              llm.call would fire falsely on LimitsExceeded/TimeExceeded.
  - execute_actions(): parent's body is a list-comp over env.execute, and the
              submit command raises Submitted BEFORE returning, so a post-wrap
              never emits action.done for the final action.
  - run():    parent re-raises on uncaught exceptions, so run.finished must be
              emitted in a finally.
The duplicated lines are pinned to upstream v2.4.2 (see UPSTREAM_VERSION).
"""

from __future__ import annotations

import time
import traceback

from minisweagent.agents.default import DefaultAgent
from minisweagent.exceptions import LimitsExceeded, Submitted, TimeExceeded

from trace.events import Emitter


class TracingAgent(DefaultAgent):
    def __init__(self, model, env, *, emitter: Emitter, **kwargs):
        super().__init__(model, env, **kwargs)
        self._emitter = emitter
        self._run_start = time.time()  # tracer-local clock; parent's _start_time is set in __init__

    def _t(self) -> float:
        return time.time() - self._run_start

    # --- seam 1: loop lifecycle ---
    def run(self, task: str = "", **kwargs) -> dict:
        self._run_start = time.time()
        self._emitter.set_clock(self._t)  # emitter timestamps relative to this run
        self._emitter.emit("run.started", task=task,
                           model_name=getattr(self.model.config, "model_name", "unknown"),
                           cwd=getattr(self.env.config, "cwd", ""))
        exc_type = exc_str = None
        result: dict = {}
        try:
            result = super().run(task, **kwargs)
            return result
        except Exception as e:  # noqa: BLE001 — record then re-raise
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

    # --- seam 3: shell exec ---
    def execute_actions(self, message: dict) -> list[dict]:
        outputs = []
        for action in message.get("extra", {}).get("actions", []):
            command = action.get("command", "")
            self._emitter.emit("action", command=command)
            try:
                output = self.env.execute(action)
            except Submitted:
                # The submit command finished successfully; env raised before
                # returning. Emit the done event, then re-raise so the loop ends.
                self._emitter.emit("action.done", returncode=0, output_bytes=0)
                raise
            outputs.append(output)
            self._emitter.emit("action.done",
                               returncode=output.get("returncode", -1),
                               output_bytes=len(str(output.get("output", "")).encode("utf-8")))
        return self.add_messages(
            *self.model.format_observation_messages(message, outputs, self.get_template_vars())
        )
