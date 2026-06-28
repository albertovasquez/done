"""Persona-faithful headless executor for scheduled jobs.

run_headless_turn(job) is the single guarantee that a scheduled cron job for
persona A runs with EXACTLY A's model/workspace/memory, identical to A typing
the prompt live.

PERSONA-FIDELITY RULES (non-negotiable):
1. model MUST come from resolve_session_model(job.agent_id, ...). NEVER vibeproxy.default_model().
2. Persona/memory blocks come via resolve_persona / resolve_memory from the workspace.
3. Workspace MUST come from resolve_workspace(job.agent_id). On UnknownPersona → OrphanPersona.
4. Fresh model + fresh LocalEnvironment + fresh runner per call. Never mutate os.environ.

Payload dispatch:
- AgentTurn → full persona-faithful LLM turn (rules 1-4 apply).
- Reminder  → notification ONLY; no inference, no run_turn. Workspace is still
              resolved (so orphaned-persona Reminders raise OrphanPersona), then
              deps.notify(text=..., agent_id=...) is called.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from harness import persona_select
from harness.jobs.model import AgentTurn, Reminder

logger = logging.getLogger(__name__)


class OrphanPersona(Exception):
    """job.agent_id no longer resolves to a persona dir — caller auto-disables the job."""


def _default_notify(*, text: str, agent_id: str) -> None:
    logger.info("cron reminder [%s]: %s", agent_id, text)


@dataclass
class Deps:
    """Injectable bundle of factory functions for unit-testing without a real engine."""
    resolve_workspace: Callable[[str], Path]
    resolve_model: Callable[..., str | None]
    compose: Callable[[Path], tuple]       # ws -> (persona_block, memory_block, ws)
    run_turn: Callable[..., None]
    notify: Callable[..., None] = field(default=_default_notify)


def _default_deps() -> Deps:
    """Wire the real harness functions.

    All live-source symbols verified against run_traced.py + persona_sessions.py
    (see inline comments).
    """
    from datetime import date

    from harness import persona as _persona   # resolve_persona: persona.py:102
    from harness import memory as _memory     # resolve_memory: memory.py
    from harness import persona_sessions as _ps   # resolve_session_model: persona_sessions.py:20
    from harness import vibeproxy as _vp          # vibeproxy.model_id, vibeproxy.DEFAULT_MODEL
    from harness.models_mock import build_mock_model  # harness/models_mock.py:58
    from harness.runner import MiniSweAgentRunner     # harness/runner.py:72

    import yaml
    import os

    # run_traced.py:43 — _load_agent_config()
    _REPO_ROOT = Path(__file__).resolve().parent.parent.parent
    _mini_yaml = _REPO_ROOT / "upstream" / "src" / "minisweagent" / "config" / "mini.yaml"

    def _load_agent_cfg() -> dict:
        cfg = yaml.safe_load(_mini_yaml.read_text())
        return cfg["agent"]

    def compose(ws: Path) -> tuple[str, str, Path]:
        pb = _persona.resolve_persona(ws).block
        mb = _memory.resolve_memory(ws, today=date.today()).block
        return pb, mb, ws

    def run_turn(*, model_id: str | None, workspace: Path, persona_block: str,
                 memory_block: str, message: str) -> None:
        # Fresh model per call; NEVER vibeproxy.default_model() (persona-fidelity rule #1).
        if model_id is None:
            model = build_mock_model()
        else:
            # run_traced.py:48-63 — _build_vibeproxy_model pattern.
            # vibeproxy.model_id() converts a bare model name to the qualified name
            # the litellm backend expects (e.g. "openai/gpt-5.4").
            from harness.streaming_model import StreamingLitellmModel
            from harness.tools.registry import build_registry
            from harness import paths as _paths
            model = StreamingLitellmModel(
                model_name=_vp.model_id(model_id),  # vibeproxy.py:36
                model_kwargs=_vp.model_kwargs(),     # vibeproxy.py:47
                cost_tracking="ignore_errors",
                registry=build_registry(
                    skill_roots=_paths.skills_dirs(project_cwd=str(workspace)),
                    memory_root=workspace,
                ),
            )

        # run_traced.py:158 — LocalEnvironment import.
        from minisweagent.environments.local import LocalEnvironment  # noqa: E402
        env = LocalEnvironment(cwd=str(workspace))

        agent_cfg = _load_agent_cfg()
        runner = MiniSweAgentRunner(model, env, agent_cfg=agent_cfg)
        for _ in runner.run(message, skill_block="", persona_block=persona_block,
                            memory_block=memory_block, base_block=""):
            pass

    return Deps(
        resolve_workspace=persona_select.resolve_workspace,  # persona_select.py:50
        resolve_model=lambda pid, **kw: _ps.resolve_session_model(
            pid,
            # These three env reads match how persona_sessions callers pass values.
            # The TUI reads them from the live process env; we do the same here
            # so the CLI-launched daemon gets an identical result.
            shell_set_model="VIBEPROXY_MODEL" in os.environ,
            shell_env=os.environ.get("VIBEPROXY_MODEL"),
            dotenv=None,   # TODO(verify against live source): TUI passes dotenv from load_dotenv;
                           # daemon entrypoint should load .env before calling run_headless_turn.
            backend="vibeproxy",
        ),
        compose=compose,
        run_turn=run_turn,
        # notify uses the module default (_default_notify); no override needed for production.
    )


def run_headless_turn(job, *, deps: Deps | None = None) -> None:
    """Execute one scheduled turn for `job` with full persona fidelity.

    Raises OrphanPersona if job.agent_id no longer maps to a persona directory.

    Payload dispatch:
    - AgentTurn: resolves model + blocks, then calls deps.run_turn (full LLM turn).
    - Reminder:  resolves workspace only (for orphan check), then calls deps.notify.
                 No inference is performed.
    """
    deps = deps or _default_deps()

    # Rule #3: workspace from agent_id; OrphanPersona on failure.
    # Always resolve workspace — even for Reminders — so an orphaned-persona
    # Reminder is detected and raises OrphanPersona consistently.
    try:
        ws = deps.resolve_workspace(job.agent_id)
    except persona_select.UnknownPersona as e:
        raise OrphanPersona(job.agent_id) from e

    if isinstance(job.payload, Reminder):
        # Reminder = notification only. No model resolution, no compose, no run_turn.
        deps.notify(text=job.payload.text, agent_id=job.agent_id)
        return

    # From here: AgentTurn path only.
    assert isinstance(job.payload, AgentTurn), f"unknown payload type: {type(job.payload)}"

    # Rule #1: model from resolve_session_model, NOT vibeproxy.default_model().
    model_id = deps.resolve_model(job.agent_id)

    # Rule #2: blocks via compose (resolve_persona + resolve_memory).
    persona_block, memory_block, ws = deps.compose(ws)

    # Rule #4: fresh model + env + runner inside run_turn; os.environ never mutated.
    deps.run_turn(
        model_id=model_id,
        workspace=ws,
        persona_block=persona_block,
        memory_block=memory_block,
        message=job.payload.message,
    )
