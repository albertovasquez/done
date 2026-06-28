# harness/jobs/model.py
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Union
from datetime import datetime

# ---- Schedule union ----
@dataclass(frozen=True)
class At:    when_iso: str
@dataclass(frozen=True)
class Every: seconds: int; anchor: float | None = None
@dataclass(frozen=True)
class Cron:  expr: str; tz: str | None = None; stagger_ms: int | None = None
Schedule = Union[At, Every, Cron]

# ---- Payload union ----
@dataclass(frozen=True)
class Reminder:  text: str
@dataclass(frozen=True)
class AgentTurn: message: str; model: str | None = None; agent_options: dict = field(default_factory=dict)
Payload = Union[Reminder, AgentTurn]

@dataclass(frozen=True)
class Grant:
    tools: "list[str] | str"; paths: "str | list[str]"
    write: bool; exec: bool; network: bool; enforced: bool = False

@dataclass(frozen=True)
class CostGate:
    timeout_s: int; min_cadence_s: int; max_consecutive_failures: int

@dataclass(frozen=True)
class JobState:
    next_run_at: float | None = None
    running_since: float | None = None
    last_run_at: float | None = None
    last_status: str | None = None
    last_error: str | None = None
    last_duration: float | None = None
    consecutive_errors: int = 0
    version: int = 0

@dataclass(frozen=True)
class Job:
    id: str; name: str; agent_id: str
    schedule: Schedule; payload: Payload
    grant: Grant; cost: CostGate; state: JobState
    description: str = ""
    enabled: bool = True
    delete_after_run: bool | None = None
    created_at: float = 0.0
    updated_at: float = 0.0
    session_target: str = "isolated"

@dataclass(frozen=True)
class JobRun:
    job_id: str; started_at: float; duration: float; status: str; error: str | None = None

# ---- tagged-union (de)serialization ----
def schedule_to_dict(s: Schedule) -> dict:
    if isinstance(s, At):    return {"kind": "at", "when_iso": s.when_iso}
    if isinstance(s, Every): return {"kind": "every", "seconds": s.seconds, "anchor": s.anchor}
    return {"kind": "cron", "expr": s.expr, "tz": s.tz, "stagger_ms": s.stagger_ms}

def schedule_from_dict(d: dict) -> Schedule:
    k = d["kind"]
    if k == "at":    return At(when_iso=d["when_iso"])
    if k == "every": return Every(seconds=d["seconds"], anchor=d.get("anchor"))
    if k == "cron":  return Cron(expr=d["expr"], tz=d.get("tz"), stagger_ms=d.get("stagger_ms"))
    raise ValueError(f"unknown schedule kind {k!r}")

def payload_to_dict(p: Payload) -> dict:
    if isinstance(p, Reminder): return {"kind": "reminder", "text": p.text}
    return {"kind": "agent_turn", "message": p.message, "model": p.model, "agent_options": p.agent_options}

def payload_from_dict(d: dict) -> Payload:
    k = d["kind"]
    if k == "reminder":   return Reminder(text=d["text"])
    if k == "agent_turn": return AgentTurn(message=d["message"], model=d.get("model"), agent_options=d.get("agent_options", {}))
    raise ValueError(f"unknown payload kind {k!r}")

def job_to_dict(j: Job) -> dict:
    return {
        "id": j.id, "name": j.name, "agent_id": j.agent_id,
        "description": j.description, "enabled": j.enabled,
        "delete_after_run": j.delete_after_run,
        "created_at": j.created_at, "updated_at": j.updated_at,
        "session_target": j.session_target,
        "schedule": schedule_to_dict(j.schedule),
        "payload": payload_to_dict(j.payload),
        "grant": asdict(j.grant), "cost": asdict(j.cost), "state": asdict(j.state),
    }

def job_from_dict(d: dict) -> Job:
    return Job(
        id=d["id"], name=d["name"], agent_id=d["agent_id"],
        description=d.get("description", ""), enabled=d.get("enabled", True),
        delete_after_run=d.get("delete_after_run"),
        created_at=d.get("created_at", 0.0), updated_at=d.get("updated_at", 0.0),
        session_target=d.get("session_target", "isolated"),
        schedule=schedule_from_dict(d["schedule"]),
        payload=payload_from_dict(d["payload"]),
        grant=Grant(**d["grant"]), cost=CostGate(**d["cost"]), state=JobState(**d["state"]),
    )

def next_run_at(schedule: "Schedule", now: float, state: "JobState") -> float | None:
    if isinstance(schedule, At):
        if state.last_run_at is not None:
            return None
        return datetime.fromisoformat(schedule.when_iso).timestamp()
    if isinstance(schedule, Every):
        if state.last_run_at is None:
            base = schedule.anchor if schedule.anchor is not None else now
            return base + schedule.seconds
        return state.last_run_at + schedule.seconds
    if isinstance(schedule, Cron):
        from croniter import croniter
        from datetime import timezone as _tz
        from zoneinfo import ZoneInfo
        tzinfo = ZoneInfo(schedule.tz) if schedule.tz else None
        base = datetime.fromtimestamp(state.last_run_at or now, tz=tzinfo or _tz.utc)
        return croniter(schedule.expr, base).get_next(float)
    raise ValueError(f"unknown schedule {schedule!r}")
