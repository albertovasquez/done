"""run_interruptible: run a blocking call on a daemon worker thread so a
cooperative cancel_flag can abort the wait without waiting for the blocking call
(a stalled LLM request) to return.

Modeled on NousResearch/hermes-agent's ``interruptible_api_call``
(agent/chat_completion_helpers.py:155) — but ADAPTED. Hermes owns its HTTP client
and force-closes the socket on interrupt; we go through litellm and do not own the
socket, so we ABANDON the daemon worker instead. The worker's socket is torn down
when it errors / is garbage-collected; being a daemon it can never block process
exit and holds no lock the next turn needs.

See docs/superpowers/specs/2026-07-01-esc-cancel-cleanup-design.md.
"""

from __future__ import annotations

import threading
from typing import Any, Callable

from minisweagent.exceptions import UserInterruption

# The cancelled-exit message shape used everywhere in the cancel path
# (tracing_agent loop check, acp_agent.emit_delta). Raising UserInterruption with
# this dict lands in the engine loop's existing `except InterruptAgentFlow`
# handler → the turn ends with a `cancelled` exit, same as every other path.
_CANCELLED_MSG = {
    "role": "exit",
    "content": "Cancelled by user.",
    "extra": {"exit_status": "cancelled", "submission": ""},
}


def run_interruptible(
    fn: Callable[[], Any],
    cancel_flag: threading.Event | None,
    *,
    poll_s: float = 0.1,
) -> Any:
    """Run ``fn()`` and return its result, aborting if ``cancel_flag`` sets first.

    * ``cancel_flag is None`` → call ``fn()`` inline on the current thread
      (byte-identical to a direct call; the CLI / cron / mock / reviewer paths
      that have no session take this branch).
    * Otherwise run ``fn()`` on a daemon worker thread and poll ``cancel_flag``
      every ``poll_s`` seconds. If the flag sets before ``fn()`` finishes, raise
      :class:`UserInterruption` and abandon the worker. If ``fn()`` finishes
      first, return its value (or re-raise the exception it raised).
    """
    if cancel_flag is None:
        return fn()

    box: dict[str, Any] = {}
    done = threading.Event()

    def _worker() -> None:
        try:
            box["result"] = fn()
        except BaseException as e:  # noqa: BLE001 — carry it back to the caller
            box["error"] = e
        finally:
            done.set()

    worker = threading.Thread(target=_worker, daemon=True)
    worker.start()

    while not done.wait(timeout=poll_s):
        if cancel_flag.is_set():
            # Abandon the daemon worker; raise the same cancelled-exit shape the
            # rest of the cancel path uses.
            raise UserInterruption(dict(_CANCELLED_MSG))

    if "error" in box:
        raise box["error"]
    return box.get("result")
