"""Per-persona seats for the in-process fleet: each persona id maps to a Seat
(its session_id + its resolved worker model). resolve_session_model applies the
C1 precedence ladder (harness.model_resolve.resolve_model) keyed on the persona's
done.conf entry, so every seat — launch persona included — resolves its model the
SAME way. No second model home: persisted model lives only in done.conf."""

from __future__ import annotations

from dataclasses import dataclass

from harness import config, model_resolve, vibeproxy


@dataclass(frozen=True)
class Seat:
    session_id: str
    model: str | None        # None in mock mode


def resolve_session_model(
    persona_id: str, *, shell_set_model: bool, shell_env: str | None,
    dotenv: str | None, backend: str,
) -> str | None:
    """The worker model for one persona's seat. None for mock. Otherwise the C1
    ladder: a REAL shell VIBEPROXY_MODEL (shell_set_model) forces the value for
    every persona; else this persona's done.conf model; else a .env value; else
    the engine default. shell_env/dotenv are the caller-read env values (kept as
    params so this stays pure-ish and testable)."""
    if backend == "mock":
        return None
    persisted = None
    cfg = config.load_agent(persona_id)
    if cfg is not None:
        persisted = cfg.model
    return model_resolve.resolve_model(
        shell_env=shell_env if shell_set_model else None,
        persisted=persisted,
        dotenv=dotenv,
        engine_default=vibeproxy.DEFAULT_MODEL,
    )


class PersonaSessions:
    """Agent-owned seat map: persona_id -> Seat. get_or_create resumes an existing
    seat (same session AND model) or mints+resolves a new one."""

    def __init__(self) -> None:
        self._seats: dict[str, Seat] = {}

    def seat_of(self, persona_id: str) -> Seat | None:
        return self._seats.get(persona_id)

    def model_of(self, persona_id: str) -> str | None:
        seat = self._seats.get(persona_id)
        return seat.model if seat is not None else None

    def set_model(self, persona_id: str, model: str | None) -> None:
        seat = self._seats.get(persona_id)
        if seat is not None:
            self._seats[persona_id] = Seat(session_id=seat.session_id, model=model)

    def register(self, persona_id: str, seat: Seat) -> None:
        """Record an externally-minted seat (e.g. the launch seat from new_session)."""
        self._seats[persona_id] = seat

    def get_or_create(self, persona_id, *, cwd, store, resolve_ws, resolve_model) -> Seat:
        seat = self._seats.get(persona_id)
        if seat is not None:
            return seat                      # resume: same session + same model
        ws = resolve_ws(persona_id)
        session_id = store.new(cwd=cwd, workspace_dir=ws)
        model = resolve_model(persona_id)
        seat = Seat(session_id=session_id, model=model)
        self._seats[persona_id] = seat
        return seat
