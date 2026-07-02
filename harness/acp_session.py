"""Per-session state for the ACP agent: cwd, a cooperative cancel flag, and the
turn history (recorded from Layer 1 so Layer 4 session/load can replay it)."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from harness.persona import PersonaLoad
    from harness.memory import MemoryLoad
    from pathlib import Path


@dataclass
class SessionState:
    cwd: str
    cancel_flag: threading.Event = field(default_factory=threading.Event)
    history: list[dict] = field(default_factory=list)
    transcript: list[dict] = field(default_factory=list)  # [{role, content, origin}], plain text
    persona_block: str | None = None  # None = not-yet-composed; "" = composed-empty
    persona_load: "PersonaLoad | None" = None
    persona_load_emitted: bool = False
    persona_emitted: bool = False        # C2a: identity chip sent once per session
    workspace_dir: "Path | None" = None  # the persona workspace this session uses (Phase B isolation core)
    worker_model: "str | None" = None    # the resolved worker model for this session's persona seat
    goal: "object | None" = None         # GoalContext | None — the armed /goal (duck-typed to dodge import cycle)
    memory_block: str | None = None      # None = not-yet-composed; "" = composed-empty
    memory_load: "MemoryLoad | None" = None
    memory_load_emitted: bool = False
    prompt_hashes: dict | None = None   # last turn's block hashes (cache.boundary, #139)
    compact_view: "object | None" = None  # episodic compacted history (history_view.CompactView, #105)


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, SessionState] = {}

    def new(self, cwd: str, workspace_dir=None) -> str:
        session_id = uuid4().hex
        self._sessions[session_id] = SessionState(cwd=cwd, workspace_dir=workspace_dir)
        return session_id

    def get(self, session_id: str) -> SessionState:
        return self._sessions[session_id]            # KeyError on unknown — caller maps to JSON-RPC error

    def record(self, session_id: str, turn: dict) -> None:
        self._sessions[session_id].history.append(turn)

    def extend(self, session_id: str, msgs: list[dict]) -> None:
        transcript = self._sessions[session_id].transcript
        for m in msgs:
            assert m["role"] in ("user", "assistant")
            assert m["origin"] in ("chat", "agent", "clarify")
            transcript.append({"role": m["role"], "content": m["content"],
                               "origin": m["origin"]})  # fresh copy, not alias
