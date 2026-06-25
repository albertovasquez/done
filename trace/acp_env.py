"""AcpEnvironment: the seam that gives the ACP layer what the lossy Phase-0
events cannot — the FULL command output, a pre-exec permission gate, and a
cancel checkpoint. A subclass of upstream LocalEnvironment (subclass, NOT edit:
honors zero-upstream-edits). The agent runs this on Phase-1's worker thread; the
callbacks marshal to the async ACP loop in acp_agent."""

from __future__ import annotations

import threading
from typing import Any, Callable

from minisweagent.environments.local import LocalEnvironment


class AcpEnvironment(LocalEnvironment):
    def __init__(self, *,
                 on_command: Callable[[str, str, dict | None], None],
                 request_permission: Callable[[str], bool] | None = None,
                 cancel_flag: threading.Event | None = None,
                 client_terminal: Callable[[str], dict] | None = None,
                 **kwargs: Any):
        super().__init__(**kwargs)
        self._on_command = on_command
        self._request_permission = request_permission
        self._cancel_flag = cancel_flag
        self._client_terminal = client_terminal

    def execute(self, action: dict, cwd: str = "", *, timeout: int | None = None) -> dict[str, Any]:
        if self._cancel_flag is not None and self._cancel_flag.is_set():
            return {"output": "", "returncode": -1, "exception_info": "cancelled"}
        command = action.get("command", "")
        self._on_command("start", command, None)
        if self._request_permission is not None and not self._request_permission(command):
            self._on_command("rejected", command, None)
            return {"output": "", "returncode": -1, "exception_info": "permission denied"}
        if self._client_terminal is not None:
            out = self._client_terminal(command)   # client runs it; returns {output, returncode, exception_info}
            self._check_finished(out)              # raises Submitted if submit sentinel present
        else:
            out = super().execute(action, cwd, timeout=timeout)   # REAL run; FULL output; may raise Submitted
        self._on_command("done", command, out)
        return out
