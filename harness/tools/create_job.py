"""CreateJobTool: the agent's way to actually CREATE a scheduled cron job after
the create-job skill's four gates are answered.

Without this tool the model has no mechanism to call the privileged door, so it
loops re-asking the gates. The tool assembles the gate spec and calls the single
door `handle_create_job` (harness/jobs/create.py). `agent_id` comes from the
ENVIRONMENT (env._active_persona, stamped at env construction) — NEVER from the
model — so a job is always bound to the persona the user is chatting as.
"""
from __future__ import annotations

import time
import uuid

from harness.jobs.create import handle_create_job


def _normalize_cost(cost) -> dict:
    """Map the skill's user-facing cost keys (timeout_secs / min_cadence_secs) to
    CostGate's field names (timeout_s / min_cadence_s). Tolerant of both spellings
    so the model can use either. max_consecutive_failures is unchanged."""
    if not isinstance(cost, dict):
        return cost
    return {
        "timeout_s": cost.get("timeout_s", cost.get("timeout_secs")),
        "min_cadence_s": cost.get("min_cadence_s", cost.get("min_cadence_secs")),
        "max_consecutive_failures": cost.get("max_consecutive_failures"),
    }


def _normalize_grant(grant) -> dict:
    """Map the skill's user-facing grant (paths/shell/network/tools) to the Grant
    dataclass fields (tools/paths/write/exec/network/enforced). The model speaks
    `shell` (= exec); `write` is inferred (declaring paths implies intent to write
    — conservative default, and grant is recorded-not-enforced in v1 anyway).
    Tolerant of the raw Grant spelling too (passes write/exec through if present)."""
    if not isinstance(grant, dict):
        return grant
    paths = grant.get("paths", [])
    return {
        "tools": grant.get("tools", []),
        "paths": paths,
        "write": grant.get("write", bool(paths)),
        "exec": grant.get("exec", grant.get("shell", False)),
        "network": grant.get("network", False),
        "enforced": grant.get("enforced", False),
    }


def _normalize_schedule(schedule) -> dict:
    """Turn the model's friendly `schedule` into the {kind: ...} dict the door
    expects. Accepts a 5-field cron string, an interval (int or numeric string =
    seconds), or an ISO-8601 timestamp. Already-a-dict passes through."""
    if isinstance(schedule, dict):
        return schedule
    if isinstance(schedule, (int, float)):
        return {"kind": "every", "seconds": int(schedule)}
    s = str(schedule).strip()
    if s.isdigit():
        return {"kind": "every", "seconds": int(s)}
    if len(s.split()) == 5:                       # 5 whitespace-separated fields → cron
        return {"kind": "cron", "expr": s}
    return {"kind": "at", "when_iso": s}          # otherwise treat as a one-shot timestamp

CREATE_JOB_TOOL = {
    "type": "function",
    "function": {
        "name": "create_job",
        "description": (
            "Create a scheduled cron job AFTER the create-job skill's four gates "
            "are answered (timeout, min-cadence, max-failures, permissions). "
            "Do NOT pass agent_id — it is resolved from the active persona."),
        "parameters": {
            "type": "object",
            "properties": {
                "schedule": {"type": "string", "description":
                             "5-field cron (e.g. '0 9 * * *'), an interval in seconds, "
                             "or an ISO-8601 timestamp for a one-shot."},
                "description": {"type": "string",
                                "description": "What this job does."},
                "cost": {
                    "type": "object",
                    "properties": {
                        "timeout_secs": {"type": "integer"},
                        "min_cadence_secs": {"type": "integer"},
                        "max_consecutive_failures": {"type": "integer"},
                    },
                    "required": ["timeout_secs", "min_cadence_secs", "max_consecutive_failures"],
                },
                "grant": {
                    "type": "object",
                    "properties": {
                        "paths": {"type": "array", "items": {"type": "string"}},
                        "shell": {"type": "boolean"},
                        "network": {"type": "boolean"},
                        "tools": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["paths", "shell", "network"],
                },
                "payload": {
                    "type": "object",
                    "description": "Optional. Defaults to a reminder using `description`.",
                    "properties": {
                        "kind": {"type": "string", "enum": ["reminder", "agent_turn"]},
                        "text": {"type": "string"},
                        "message": {"type": "string"},
                    },
                },
            },
            "required": ["schedule", "description", "cost", "grant"],
        },
    },
}


class CreateJobTool:
    name = "create_job"
    schema = CREATE_JOB_TOOL

    def display_label(self, args: dict) -> str:
        return f"create_job {args.get('description', '')[:40]}"

    def execute(self, args: dict, env) -> dict:
        # agent_id from the environment, never the model.
        agent_id = getattr(env, "_active_persona", None) or "default"

        description = args.get("description", "")
        # handle_create_job REQUIRES id + payload; the model supplies neither.
        payload = args.get("payload") or {"kind": "reminder", "text": description}
        spec = {
            "id": uuid.uuid4().hex[:12],
            "name": (description[:40] or "job"),
            "agent_id": agent_id,
            "description": description,
            "schedule": _normalize_schedule(args.get("schedule")),
            "cost": _normalize_cost(args.get("cost")) if args.get("cost") else args.get("cost"),
            "grant": _normalize_grant(args.get("grant")) if args.get("grant") else args.get("grant"),
            "payload": payload,
        }
        try:
            result = handle_create_job(spec, now=time.time())
        except Exception as e:                       # fail-closed gate errors land here
            return {"output": f"Could not create job: {e}", "returncode": 1,
                    "exception_info": None}
        return {"output": f"Created job {result['id']} ({result['name']}) for "
                          f"persona '{agent_id}'.",
                "returncode": 0, "exception_info": None}
