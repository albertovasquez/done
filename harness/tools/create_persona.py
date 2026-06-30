"""CreatePersonaTool: the agent's way to actually CREATE a new persona (agent)
from a natural-language request ("create a persona named Robbie").

Without this tool the router classifies such a request as chat_question (there is
no persona-create task type) and the agent has no mechanism to fulfil it — persona
creation is otherwise reachable only via the TUI modal. This tool mirrors
CreateJobTool: it turns a UI-only privileged operation into an agent-reachable
one by calling the single existing backend `persona.create_persona`.

CREATE-ONLY by design: it does NOT switch the active seat. The tool runs inside
the current persona's turn, and activating a new seat mid-turn is the seat/model
state-leak hazard the C2c work flagged. The user switches from the agents drawer;
the success message points them there.
"""
from __future__ import annotations

from harness import persona, persona_select

CREATE_PERSONA_TOOL = {
    "type": "function",
    "function": {
        "name": "create_persona",
        "description": (
            "Create a new persona (agent) by name. Use when the user asks to "
            "create or add a persona/agent. Pass the display name as `name`; the "
            "id is derived automatically. This ONLY creates the persona — it does "
            "NOT switch to it. Tell the user to switch from the agents drawer."),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description":
                         "The display name for the new persona, e.g. 'Robbie'."},
            },
            "required": ["name"],
        },
    },
}


class CreatePersonaTool:
    name = "create_persona"
    schema = CREATE_PERSONA_TOOL

    def display_label(self, args: dict) -> str:
        return f"create_persona {str(args.get('name', '')).strip()[:40]}"

    def execute(self, args: dict, env) -> dict:
        name = str(args.get("name", "")).strip()
        if not name:
            return {"output": "Could not create persona: name required.",
                    "returncode": 1, "exception_info": None}
        pid = persona_select.slugify_persona_name(name)
        try:
            # create-only — deliberately NOT followed by a seat switch.
            persona.create_persona(pid, display_name=name)
        except persona.PersonaExists:
            return {"output": f"A persona '{pid}' already exists.",
                    "returncode": 1, "exception_info": None}
        except persona_select.InvalidPersonaId:
            return {"output": f"Could not derive a valid persona id from '{name}'.",
                    "returncode": 1, "exception_info": None}
        except OSError as e:
            return {"output": f"Could not create persona: {e}",
                    "returncode": 1, "exception_info": None}
        return {"output":
                f"Created persona '{name}' (id: {pid}). It starts blank — edit its "
                f"SOUL.md / IDENTITY.md / USER.md to give it a personality. Switch "
                f"to it from the agents drawer when you're ready.",
                "returncode": 0, "exception_info": None}
