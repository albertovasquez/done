"""AcpEnvironment: the seam that gives the ACP layer what the lossy Phase-0
events cannot — the FULL command output, a pre-exec permission gate, and a
cancel checkpoint. A subclass of upstream LocalEnvironment (subclass, NOT edit:
honors zero-upstream-edits). The agent runs this on Phase-1's worker thread; the
callbacks marshal to the async ACP loop in acp_agent."""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
from typing import Any, Callable

from minisweagent.environments.local import LocalEnvironment

from harness.acp_emit import parse_plan_command

logger = logging.getLogger("harness.acp_env")

# how often the run loop wakes to check the cancel flag while waiting on output.
_POLL_INTERVAL_S = 0.1


class AcpEnvironment(LocalEnvironment):
    def __init__(self, *,
                 on_command: Callable[[str, str, dict | None], None],
                 check_permission=None,                # Callable[[PermissionRequest], bool] | None
                 cancel_flag: threading.Event | None = None,
                 client_terminal: Callable[[str], dict] | None = None,
                 on_plan: Callable[[list[tuple[str, str]]], None] | None = None,
                 on_progress: Callable[[dict], None] | None = None,
                 output_filter: Callable[[str, str, int], str] | None = None,
                 **kwargs: Any):
        super().__init__(**kwargs)
        self._on_command = on_command
        self._check_permission = check_permission
        self._cancel_flag = cancel_flag
        self._client_terminal = client_terminal
        self._on_plan = on_plan
        self._on_progress = on_progress
        self._output_filter = output_filter

    def emit_progress(self, meta: dict) -> None:
        """Push a mid-turn progress payload onto the ACP stream via field_meta.
        Used by tools (e.g. SubagentTool's Collector) to surface sub-activity
        while execute() is still running. No-op off the ACP path (on_progress
        None) so the same call is safe for cron/CLI workers."""
        if self._on_progress is not None:
            self._on_progress(meta)

    def execute(self, action: dict, cwd: str = "", *, timeout: int | None = None) -> dict[str, Any]:
        if self._cancel_flag is not None and self._cancel_flag.is_set():
            return {"output": "", "returncode": -1, "exception_info": "cancelled"}
        command = action.get("command", "")
        # The `plan ...` sentinel is a structured capability over the bash-only
        # channel: intercept it, surface the plan to the TUI, and return success
        # WITHOUT running it as a shell command or asking permission.
        plan = parse_plan_command(command)
        if plan is not None:
            if self._on_plan is not None:
                self._on_plan(plan)
            return {"output": "(plan updated)", "returncode": 0, "exception_info": ""}
        self._on_command("start", command, None)
        if self._check_permission is not None:
            from harness.permcheck import PermissionRequest
            req = PermissionRequest(kind="bash", command=command, is_exec=True)
            if not self._check_permission(req):
                self._on_command("rejected", command, None)
                return {"output": "", "returncode": -1, "exception_info": "permission denied"}
        # The submit sentinel makes _check_finished raise Submitted. That raise sits
        # BETWEEN start and done, so without the finally below the submit command's
        # tool-call never closes — the TUI "Running shell…" spinner stays open
        # forever (stuck spinner + locked composer) after the turn has finished.
        # Wrap every branch so start/done ALWAYS balance, even as Submitted
        # propagates. out is seeded so the finally is safe if super() raises before
        # returning a value.
        out: dict[str, Any] = {"output": "", "returncode": -1, "exception_info": ""}
        try:
            if self._client_terminal is not None:
                out = self._client_terminal(command)   # client runs it; returns {output, returncode, exception_info}
                self._check_finished(out)              # raises Submitted if submit sentinel present
            elif self._cancel_flag is not None:
                # ESC mid-run: own the subprocess so a cancel set AFTER the command
                # started kills it instead of blocking in communicate(). The no-flag
                # branch keeps upstream's exact behavior (CLI/mock path).
                out = self._run_cancellable(command, cwd, timeout)
                if out.get("exception_info") == "cancelled":
                    return out                         # killed: skip done + Submitted check
                self._check_finished(out)
            else:
                # super().execute() runs AND calls _check_finished internally, so it
                # may raise Submitted before assigning out; the finally still fires.
                out = super().execute(action, cwd, timeout=timeout)   # REAL run; FULL output
        finally:
            self._on_command("done", command, out)
        if self._output_filter is not None and out.get("returncode") is not None:
            raw = out.get("output", "")
            try:
                filtered = self._output_filter(command, raw, out.get("returncode", 0))
            except Exception:
                filtered = None                 # fail-open: leave out unchanged
            if filtered and len(filtered) < len(raw):
                out = {**out, "output": filtered,
                       "_raw_bytes": len(raw), "_filtered_bytes": len(filtered)}
                logger.debug("filter.savings command=%r bytes_in=%d bytes_out=%d",
                             command, len(raw), len(filtered))
        return out

    def _run_cancellable(self, command: str, cwd: str, timeout: int | None) -> dict[str, Any]:
        """Run `command`, polling the cancel flag so a mid-run ESC kills the whole
        process group. Mirrors LocalEnvironment._run's shape/kill, plus the poll."""
        cwd = cwd or self.config.cwd or os.getcwd()
        deadline_timeout = timeout or self.config.timeout
        try:
            proc = subprocess.Popen(
                command, shell=True, text=True, cwd=cwd,
                env=os.environ | self.config.env,
                encoding="utf-8", errors="replace",
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                start_new_session=os.name == "posix",   # new group → killpg reaches children
            )
        except Exception as e:                          # same shape upstream returns on failure
            return {"output": "", "returncode": -1,
                    "exception_info": f"An error occurred while executing the command: {e}",
                    "extra": {"exception_type": type(e).__name__, "exception": str(e)}}

        waited = 0.0
        while True:
            try:
                stdout, _ = proc.communicate(timeout=_POLL_INTERVAL_S)
                return {"output": stdout, "returncode": proc.returncode, "exception_info": ""}
            except subprocess.TimeoutExpired:
                if self._cancel_flag.is_set():
                    self._kill(proc)
                    return {"output": "", "returncode": -1, "exception_info": "cancelled"}
                waited += _POLL_INTERVAL_S
                if 0 < deadline_timeout <= waited:      # honor the real timeout too
                    self._kill(proc)
                    out = proc.communicate()[0] or ""
                    return {"output": out, "returncode": -1,
                            "exception_info": f"An error occurred while executing the command: "
                                              f"Command '{command}' timed out after {deadline_timeout} seconds"}

    @staticmethod
    def _kill(proc: subprocess.Popen) -> None:
        try:
            os.killpg(proc.pid, signal.SIGKILL) if os.name == "posix" else proc.kill()
        except ProcessLookupError:
            pass                                        # already gone
        try:
            proc.wait(timeout=5)                        # reap; never leave a zombie
        except Exception:
            pass
