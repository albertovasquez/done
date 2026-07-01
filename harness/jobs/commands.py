"""Command-first verbs for the agent dashboard input. Pure parse + a thin apply
over harness.jobs.ops. NO agent session — these mutate scheduled jobs directly."""
from __future__ import annotations

import time

from harness.jobs import ops

# 'run' deliberately excluded in P1: ops.run needs a live executor; faking it via
# next_run_at would be a dishonest "run" (schedules, doesn't run). Deferred.
_VERBS = {"disable", "enable", "remove"}


def parse_command(line: str) -> tuple[str, str] | None:
    parts = line.strip().split(maxsplit=1)
    if len(parts) != 2:
        return None
    verb, target = parts[0].lower(), parts[1].strip()
    if verb not in _VERBS or not target:
        return None
    return verb, target


def apply_command(agent_id: str, line: str, now: float | None = None) -> str:
    now = time.time() if now is None else now
    parsed = parse_command(line)
    if parsed is None:
        return "unrecognized command — try: disable/enable/remove <job name>"
    verb, target = parsed
    jobs = ops.list_jobs(agent_id=agent_id)
    match = next((j for j in jobs if j.name.lower() == target.lower()), None)
    if match is None:
        return f"no job named {target!r} for this agent"
    # The mutation can fail if the job vanishes between the list above and the
    # write (daemon fired + deleted it, another action removed it). Return an
    # error string instead of raising into the Textual event handler.
    try:
        if verb == "disable":
            ops.update(match.id, now=now, enabled=False); return f"disabled {match.name}"
        if verb == "enable":
            ops.update(match.id, now=now, enabled=True); return f"enabled {match.name}"
        if verb == "remove":
            ops.remove(match.id); return f"removed {match.name}"
    except Exception as e:
        return f"could not {verb} {match.name}: {e}"
    return "unrecognized command"
