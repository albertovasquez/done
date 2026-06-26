"""Typed handoff between the acp.Client callbacks and the Textual app."""

from __future__ import annotations

import asyncio
from typing import Any

from textual.message import Message


class SessionUpdate(Message):
    """An ACP session/update notification, marshalled to the app for rendering.
    Carries the originating session_id so the app can drop updates from a stale
    (reloaded-away) session."""
    def __init__(self, update: Any, session_id: str | None = None) -> None:
        super().__init__()
        self.update = update
        self.session_id = session_id


class PermissionRequest(Message):
    """A permission request; the app resolves `future` with an option_id (allow)
    or None (reject)."""
    def __init__(self, options: Any, tool_call: Any, future: "asyncio.Future") -> None:
        super().__init__()
        self.options = options
        self.tool_call = tool_call
        self.future = future
