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
    # Fail closed on a malformed cost gate. A missing key normalizes to None
    # (create_job._normalize_cost), which would otherwise persist and later blow
    # up `max(override, min_cadence_s)` in next_run_at OUTSIDE ops.run's
    # try/except — an undisableable per-tick crash-loop. Reject at the door.
    if cost.min_cadence_s is None or cost.timeout_s is None \
            or cost.max_consecutive_failures is None:
        raise ValueError("cost gate fields must be set (fail closed)")
    # Cadence-floor footgun guard. Enforced for Every (a fixed interval is cheap
    # to check) and for Dynamic (a self-paced loop with min_cadence_s=0 would
    # re-fire every daemon tick — require a positive floor). At is one-shot
    # (skip). Cron's implied interval isn't floor-checked yet (no cheap read).
    if isinstance(schedule, m.Every) and schedule.seconds < cost.min_cadence_s:
        raise ValueError("cadence below min_cadence_s floor")
    if isinstance(schedule, m.Dynamic) and cost.min_cadence_s <= 0:
        raise ValueError("a self-paced loop requires a positive min_cadence_s floor")
    # A Dynamic schedule is meaningless with a Reminder payload: a Reminder never
    # runs an LLM turn, so it can never call set_next_run → the loop pauses after
    # one fire. Reject the combination rather than ship a one-shot "loop".
    payload = m.payload_from_dict(spec["payload"])
    if isinstance(schedule, m.Dynamic) and isinstance(payload, m.Reminder):
        raise ValueError("a Dynamic loop needs an agent_turn payload, not a reminder")

    job = m.Job(
        id=spec["id"],
        name=spec.get("name", spec["id"]),
        agent_id=spec["agent_id"],
        description=spec.get("description", ""),
        enabled=spec.get("enabled", True),
        delete_after_run=spec.get("delete_after_run"),
        session_target=spec.get("session_target", "isolated"),
        schedule=schedule,
        payload=payload,
        grant=m.Grant(**spec["grant"]),
        cost=cost,
        state=m.JobState(),
    )
    result = ops.add(job, now=now)
    return m.job_to_dict(result)
