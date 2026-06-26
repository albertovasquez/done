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
    handler: Callable[["HarnessTui"], Awaitable[None]]  # noqa: F821
    aliases: tuple[str, ...] = ()              # extra typed names that resolve here, hidden from the menu


# Handlers are thin: they delegate to app methods so the app owns the wiring.
async def _models(app) -> None:
    await app.action_select_model()


async def _reload(app) -> None:
    await app.action_reload()


async def _clear(app) -> None:
    await app.action_clear()


async def _exit(app) -> None:
    app.exit()


async def _help(app) -> None:
    app.show_help()


def build_registry() -> list[Command]:
    """The commands available in the slash menu, in display order."""
    return [
        Command("models", "Select the active model", _models),
        Command("reload", "Reload everything (restart the app)", _reload),
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
