"""Slash-command registry — the single source of truth for the slash menu and
any future command palette. Add a command = add one Command entry.

Each handler is an async callable taking the running HarnessTui app. Handlers
live on the app (or call app methods) so they can reach the connection, the
screen stack, etc. The registry only names + describes + dispatches."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable


@dataclass(frozen=True)
class Command:
    name: str                                  # without the leading slash; shown in the menu
    description: str
    # arg is the text typed after the command name (e.g. "pin" in "/yolo pin"),
    # "" when none. Defaults to "" so the no-arg call convention still works.
    handler: Callable[["HarnessTui", str], Awaitable[None]]  # noqa: F821
    aliases: tuple[str, ...] = ()              # extra typed names that resolve here, hidden from the menu


# Handlers are thin: they delegate to app methods so the app owns the wiring.
async def _models(app, arg: str = "") -> None:
    await app.action_select_model()


async def _reload(app, arg: str = "") -> None:
    await app.action_reload()


async def _persona(app, arg: str = "") -> None:
    # /persona just opens the agents rail; selecting a row switches in-process
    # (see app.on_persona_selected → harness/set_persona) and `n` creates one.
    app.action_toggle_rail()


async def _clear(app, arg: str = "") -> None:
    await app.action_clear()


async def _exit(app, arg: str = "") -> None:
    app.exit()


async def _help(app, arg: str = "") -> None:
    app.show_help()


async def _yolo(app, arg: str = "") -> None:
    sub = arg.strip().lower()
    if sub == "":
        app.action_toggle_yolo()
    elif sub == "pin":
        await app.action_yolo_pin()
    elif sub == "unpin":
        await app.action_yolo_unpin()
    else:
        app._notify_line("usage: /yolo [pin|unpin]")


async def _loop(app, arg: str = "") -> None:
    # A "loop" is a self-paced (Dynamic) scheduled job the agent creates via the
    # create_loop tool after the four gates. The bare keystroke can't supply the
    # message/cost/grant, so /loop routes through the normal gated chat flow:
    #   /loop <text> → submit a create-loop request (agent calls create_loop)
    #   /loop        → prefill a template in the composer for the user to complete
    task = arg.strip()
    if task:
        await app._seed_prompt(
            f"Create a self-paced loop that does the following, "
            f"pacing itself between runs: {task}")
    else:
        app._prefill_composer("Create a self-paced loop that: ")


async def _goal(app, arg: str = "") -> None:
    # A /goal is a directive the agent must satisfy before it can end its turn:
    #   /goal <text> → arm the goal (harness/set_goal) + start work
    #   /goal clear  → disarm (harness/clear_goal)
    #   /goal        → show the active goal (tracked app-side, no round-trip)
    sub = arg.strip()
    if sub == "":
        active = getattr(app, "_active_goal_text", None)
        app._notify_line(f"active goal: {active}" if active
                         else "no goal set. /goal <what to accomplish>")
        return
    if sub.lower() == "clear":
        await app._conn.ext_method("harness/clear_goal", {})
        app._active_goal_text = None
        app._notify_line("goal cleared.")
        return
    await app._conn.ext_method("harness/set_goal", {"text": sub})
    app._active_goal_text = sub
    app._notify_line(f"goal armed: {sub}")
    await app._seed_prompt(sub)


async def _compress_aware(app, arg: str = "") -> None:
    sub = arg.strip().lower()
    if sub == "":
        app.action_toggle_compress_aware()
    elif sub == "pin":
        app.action_compress_aware_pin()
    elif sub == "unpin":
        app.action_compress_aware_unpin()
    else:
        app._notify_line("usage: /compress-aware [pin|unpin]")


def build_registry() -> list[Command]:
    """The commands available in the slash menu, in display order."""
    return [
        Command("models", "Select the active model", _models),
        Command("yolo", "Toggle auto-allow (pin/unpin to persist)", _yolo),
        Command("compress-aware", "Toggle context compression (pin/unpin to persist)", _compress_aware),
        Command("loop", "Create a self-paced recurring task (/loop <what to do>)", _loop),
        Command("goal", "Set a goal the agent must reach before it can stop (/goal <what>)", _goal),
        Command("reload", "Reload everything (restart the app)", _reload),
        Command("persona", "Open the agents rail (your personas + which is active)", _persona),
        Command("clear", "Fresh conversation (restart the agent)", _clear),
        Command("help", "Show available commands", _help),
        Command("exit", "Exit the app", _exit, aliases=("quit",)),
    ]


def resolve_command(commands: list[Command], name: str) -> "Command | None":
    """Find the command a typed name refers to: its canonical name, or an exact
    alias. Aliases are exact-match only and never surface in the menu/filter."""
    return next((c for c in commands
                 if c.name == name or name in c.aliases), None)


def filter_commands(commands: list[Command], query: str) -> list[Command]:
    """Filter by the text typed after '/'. Empty query → all. Case-insensitive
    substring match on the command name (prefix matches rank first)."""
    q = query.lower().strip()
    if not q:
        return list(commands)
    prefix = [c for c in commands if c.name.startswith(q)]
    contains = [c for c in commands if q in c.name and not c.name.startswith(q)]
    return prefix + contains
