"""handle_create_job: the SINGLE privileged door that writes a cron job.

Pure jobs logic (only harness.jobs.ops/model) so BOTH callers can reach it
without pulling in ACP: the `harness/create_job` ext-method (acp_agent re-exports
this) and the agent-facing `create_job` tool (harness/tools/create_job.py).
Validates all gates fail-closed (agent_id, cost, grant must all be present).
"""
from __future__ import annotations


def handle_create_job(spec: dict, *, now: float) -> dict:
    """Validate the gate spec fail-closed, then delegate to ops.add. Returns the
    created job as a dict (job_to_dict). Raises ValueError/KeyError on a bad spec."""
    from harness.jobs import ops, model as m

    if not spec.get("agent_id"):
        raise ValueError("agent_id required")
    if not spec.get("cost"):
        raise ValueError("cost gate required (fail closed)")
    if not spec.get("grant"):
        raise ValueError("grant required (fail closed)")

    schedule = m.schedule_from_dict(spec["schedule"])
    cost = m.CostGate(**spec["cost"])
    # Cadence-floor footgun guard. v1 enforces the floor only for Every schedules
    # (a fixed interval is cheap to check). At is one-shot (skip). Cron's implied
    # interval isn't floor-checked yet (no cheap interval read).
    if isinstance(schedule, m.Every) and schedule.seconds < cost.min_cadence_s:
        raise ValueError("cadence below min_cadence_s floor")

    job = m.Job(
        id=spec["id"],
        name=spec.get("name", spec["id"]),
        agent_id=spec["agent_id"],
        description=spec.get("description", ""),
        enabled=spec.get("enabled", True),
        delete_after_run=spec.get("delete_after_run"),
        session_target=spec.get("session_target", "isolated"),
        schedule=schedule,
        payload=m.payload_from_dict(spec["payload"]),
        grant=m.Grant(**spec["grant"]),
        cost=cost,
        state=m.JobState(),
    )
    result = ops.add(job, now=now)
    return m.job_to_dict(result)
