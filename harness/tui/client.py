"""TuiClient implements the acp.Client Protocol. Each callback marshals to the
Textual app via post_message; request_permission posts a modal request and awaits
an asyncio.Future the modal button resolves. Runs entirely on Textual's loop —
no threads. fs/terminal/ext methods are benign stubs: v1 advertises NEITHER fs
NOR terminal capability, so the agent never calls them (proven by
tests/test_tui_capabilities.py)."""

from __future__ import annotations

import asyncio
from typing import Any

from acp.schema import (
    AllowedOutcome,
    DeniedOutcome,
    RequestPermissionResponse,
)

from harness.tui.messages import SessionUpdate, PermissionRequest


class TuiClient:                      # implements the acp.Client Protocol
    def __init__(self, app) -> None:
        self._app = app

    async def session_update(self, session_id: str, update: Any, **kw: Any) -> None:
        self._app.post_message(SessionUpdate(update))

    async def request_permission(self, options: Any, session_id: str,
                                 tool_call: Any, **kw: Any) -> Any:
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._app.post_message(PermissionRequest(options, tool_call, fut))
        option_id = await fut
        if option_id is not None:
            # Look up the chosen option by option_id and inspect its kind.
            chosen = next((o for o in (options or []) if getattr(o, "option_id", None) == option_id), None)
            if chosen is not None and str(getattr(chosen, "kind", "")).startswith("allow"):
                # AllowedOutcome REQUIRES outcome="selected" — omitting raises ValidationError.
                return RequestPermissionResponse(
                    outcome=AllowedOutcome(outcome="selected", option_id=option_id))
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))

    # --- benign defaults: unused in v1 (no fs/terminal capability advertised) ---
    async def read_text_file(self, *a: Any, **k: Any) -> Any: return None
    async def write_text_file(self, *a: Any, **k: Any) -> Any: return None
    async def create_terminal(self, *a: Any, **k: Any) -> Any: return None
    async def terminal_output(self, *a: Any, **k: Any) -> Any: return None
    async def wait_for_terminal_exit(self, *a: Any, **k: Any) -> Any: return None
    async def release_terminal(self, *a: Any, **k: Any) -> Any: return None
    async def kill_terminal(self, *a: Any, **k: Any) -> Any: return None
    async def ext_method(self, method: str, params: dict) -> dict: return {}
    async def ext_notification(self, method: str, params: dict) -> None: return None

    def on_connect(self, conn: Any) -> None:
        pass
