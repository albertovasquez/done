"""CreateLoopTool: create a self-paced (Dynamic) scheduled loop from chat.

Sibling of CreateJobTool. A loop is a Job whose schedule is Dynamic: each run
the turn calls set_next_run to steer its own cadence, or omits it to pause the
loop. The turn payload is always an AgentTurn (a loop runs the model, not a bare
reminder). agent_id comes from env._active_persona, never the model. Reuses the
same handle_create_job door + create-job gates (cost/grant fail-closed) and the
same cost/grant normalizers as create_job (DRY)."""
from __future__ import annotations

import time
import uuid

from harness.jobs.create import handle_create_job
from harness.tools.create_job import _normalize_cost, _normalize_grant

CREATE_LOOP_TOOL = {
    "type": "function",
    "function": {
        "name": "create_loop",
        "description": (
            "Create a SELF-PACED loop: a scheduled turn that decides its own "
            "cadence via set_next_run each run (omit it to pause the loop). Same "
            "four gates as create_job (timeout, min-cadence, max-failures, "
            "permissions). Do NOT pass agent_id — it is the active persona."),
        "parameters": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description":
                            "The prompt this loop's turn runs each fire."},
                "description": {"type": "string",
                                "description": "What this loop does."},
                "cost": {
                    "type": "object",
                    "properties": {
                        "timeout_secs": {"type": "integer"},
                        "min_cadence_secs": {"type": "integer"},
                        "max_consecutive_failures": {"type": "integer"},
                    },
                    "required": ["timeout_secs", "min_cadence_secs",
                                 "max_consecutive_failures"],
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
            },
            "required": ["message", "cost", "grant"],
        },
    },
}


class CreateLoopTool:
    name = "create_loop"
    schema = CREATE_LOOP_TOOL

    def display_label(self, args: dict) -> str:
        return f"create_loop {args.get('description', args.get('message', ''))[:40]}"

    def execute(self, args: dict, env) -> dict:
        agent_id = getattr(env, "_active_persona", None) or "default"
        description = args.get("description", "") or args.get("message", "")[:40]
        spec = {
            "id": uuid.uuid4().hex[:12],
            "name": (description[:40] or "loop"),
            "agent_id": agent_id,
            "description": description,
            "schedule": {"kind": "dynamic"},
            "cost": _normalize_cost(args.get("cost")) if args.get("cost") else args.get("cost"),
            "grant": _normalize_grant(args.get("grant")) if args.get("grant") else args.get("grant"),
            "payload": {"kind": "agent_turn", "message": args.get("message", "")},
        }
        try:
            result = handle_create_job(spec, now=time.time())
        except Exception as e:                       # fail-closed gate errors
            return {"output": f"Could not create loop: {e}", "returncode": 1,
                    "exception_info": None}
        return {"output": f"Created loop {result['id']} ({result['name']}) for "
                          f"persona '{agent_id}'.",
                "returncode": 0, "exception_info": None}
