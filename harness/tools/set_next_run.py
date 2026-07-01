"""SetNextRunTool: a dynamic-loop turn's way to steer its own cadence.

The turn calls set_next_run(delay_seconds=N) to schedule its next run N seconds
out. It stamps the intent onto the env (env._next_run_override) — it NEVER writes
the job store. ops.run reads the override off run_headless_turn's return value
after the turn ends and computes the new next_run_at (the sole store writer).

Omitting the call entirely pauses the loop: no override → next_run_at None (see
harness/jobs/model.py Dynamic branch)."""
from __future__ import annotations

SET_NEXT_RUN_TOOL = {
    "type": "function",
    "function": {
        "name": "set_next_run",
        "description": (
            "Schedule THIS self-paced loop's next run, `delay_seconds` from now. "
            "Call it once before you finish the turn to keep the loop going. "
            "Do NOT call it if the loop's work is done — omitting it pauses the "
            "loop. The delay is floored at the job's min-cadence."),
        "parameters": {
            "type": "object",
            "properties": {
                "delay_seconds": {
                    "type": "integer",
                    "description": "Seconds from now until the next run. Must be > 0.",
                },
            },
            "required": ["delay_seconds"],
        },
    },
}


class SetNextRunTool:
    name = "set_next_run"
    schema = SET_NEXT_RUN_TOOL

    def display_label(self, args: dict) -> str:
        return f"set_next_run {args.get('delay_seconds', '?')}s"

    def execute(self, args: dict, env) -> dict:
        raw = args.get("delay_seconds")
        # Accept ints and int-valued floats; reject bools, strings, None, <= 0.
        ok = isinstance(raw, int) and not isinstance(raw, bool)
        if not ok and isinstance(raw, float) and raw.is_integer():
            raw, ok = int(raw), True
        if not ok or raw <= 0:
            return {"output": f"delay_seconds must be a positive integer, got {raw!r}.",
                    "returncode": 1, "exception_info": None}
        env._next_run_override = int(raw)
        return {"output": f"Next run in {raw}s.", "returncode": 0, "exception_info": None}
