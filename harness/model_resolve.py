"""The persona worker-model precedence ladder — a single pure function.

Highest rung wins:
  1. shell_env     — a model id the user exported in their SHELL (per-launch)
  2. persisted     — done.conf [agents.<persona>].model
  3. dotenv        — a model id from a project .env file
  4. engine_default

Pure: every input is a parameter, no os.environ / file reads here, so the ladder
is exhaustively testable. Empty strings count as absent (a blank env var must not
beat a real persisted model)."""

from __future__ import annotations


def resolve_model(
    *,
    shell_env: str | None,
    dotenv: str | None,
    persisted: str | None,
    engine_default: str,
) -> str:
    for candidate in (shell_env, persisted, dotenv):
        if candidate:                       # non-None and non-empty
            return candidate
    return engine_default
