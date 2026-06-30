"""Internal lifecycle hook registry.

A tiny, single-process pub/sub seam. Built-in consumers self-register at import
time; the TUI fires events at lifecycle moments (session_start / session_end).

This is INTERNAL only — there is no user-configurable shell-hook layer yet (see
the follow-on issue). The event names + the `dispatch(**payload)` dict are the
forward-compat contract a future shell layer will serialize to a subprocess.

Hard rules:
- `dispatch` NEVER raises.
- Each handler is isolated: a raising handler is logged (when a tracer is
  passed) and skipped; the remaining handlers still run.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# event name -> list of (handler, label)
_handlers: dict[str, list[tuple]] = {}


def register(event: str, handler, *, label: str | None = None) -> None:
    """Register *handler* to run on *event*. Handlers run in registration order."""
    _handlers.setdefault(event, []).append((handler, label or getattr(handler, "__name__", "?")))


def on(event: str, *, label: str | None = None):
    """Decorator form of register; returns the handler unchanged."""
    def deco(handler):
        register(event, handler, label=label)
        return handler
    return deco


def dispatch(event: str, *, tracer=None, **payload) -> None:
    """Fire *event*: call every handler with **payload. Never raises.

    A handler that raises is logged (via tracer.emit('dn','hook.error',…) when a
    tracer is passed, and always to the module logger) and skipped."""
    for handler, label in list(_handlers.get(event, ())):
        try:
            extra = {"tracer": tracer} if tracer is not None else {}
            handler(**extra, **payload)
        except Exception as e:                      # isolate: one bad hook never breaks others
            logger.exception("hook %r for event %r raised", label, event)
            if tracer is not None:
                try:
                    tracer.emit("dn", "hook.error", event=event, label=label, error=str(e))
                except Exception:                   # tracer failure must not break dispatch either
                    logger.exception("tracer.emit failed while logging hook error")


def clear(event: str | None = None) -> None:
    """Remove handlers for *event*, or all handlers when event is None. Test-only."""
    if event is None:
        _handlers.clear()
    else:
        _handlers.pop(event, None)
