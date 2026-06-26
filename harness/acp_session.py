"""Per-session state for the ACP agent: cwd, a cooperative cancel flag, and the
turn history (recorded from Layer 1 so Layer 4 session/load can replay it)."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from uuid import uuid4


@dataclass
class SessionState:
    cwd: str
    cancel_flag: threading.Event = field(default_factory=threading.Event)
    history: list[dict] = field(default_factory=list)


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, SessionState] = {}

    def new(self, cwd: str) -> str:
        session_id = uuid4().hex
        self._sessions[session_id] = SessionState(cwd=cwd)
        return session_id

    def get(self, session_id: str) -> SessionState:
        return self._sessions[session_id]            # KeyError on unknown — caller maps to JSON-RPC error

    def record(self, session_id: str, turn: dict) -> None:
        self._sessions[session_id].history.append(turn)
