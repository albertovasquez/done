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

import inspect
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from harness import persona_select
from harness.instance_templates import OBSERVE_FIRST_INSTANCE
from harness.jobs.model import AgentTurn, Reminder

logger = logging.getLogger(__name__)


def stamp_headless_gate(env, *roots: Path) -> None:
    """Stamp the file-tool permission gate onto a headless env, confined to `roots`.
    Shared by the cron executor (roots = [workspace]) and the dev-CLI runner
    (roots = [cwd, workspace_dir]) — #168.

    Headless paths have no elicitation channel, so the policy is deny-by-default:
    decide_permission(..., has_elicitation=False) → out-of-root file ops are DENIED.
    The dispatch chokepoint (tracing_agent._dispatch_tool) and the write/edit tools
    read `_check_permission` + `_allowed_roots` exactly as the ACP path does — same
    machinery, no parallel policy.

    SCOPE: this gates only the FILE tools (read/write/edit), which is #168's scope
    ("file tools run ungated"). Bash is NOT routed through this gate — it runs via
    LocalEnvironment.execute(), which has no permission hook — so bash on the cron
    path is unchanged by this stamp (ungated, as before). Confining/sandboxing bash
    for headless jobs is the separate grant-enforcement concern (#141)."""
    from harness.permcheck import decide_permission
    env._allowed_roots = [Path(r) for r in roots]
    env._check_permission = lambda req: decide_permission(
        req, yolo=False, has_elicitation=False) == "allow"


class OrphanPersona(Exception):
    """job.agent_id no longer resolves to a persona dir — caller auto-disables the job."""


def _default_notify(*, text: str, agent_id: str) -> None:
    logger.info("cron reminder [%s]: %s", agent_id, text)


def _accepts_kwarg(fn: Callable, name: str) -> bool:
    """True if `fn` can be called with keyword `name` (has the param or **kwargs).

    Keeps Task 8 additive: a positive job timeout is only handed to run_turn
    callables that can receive `wall_budget`. Test doubles with a fixed signature
    (no wall_budget, no **kwargs) are never broken by the new kwarg."""
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return True  # builtins / un-introspectable: assume it tolerates the kwarg
    for p in sig.parameters.values():
        if p.kind is inspect.Parameter.VAR_KEYWORD:
            return True
        if p.name == name and p.kind in (
            inspect.Parameter.KEYWORD_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            return True
    return False


def _observe_or_default_cfg(cfg: dict, mode: str | None) -> dict:
    """If the job opted into observe mode, return a COPY with the observe-first
    instance_template; otherwise return cfg untouched (default work-order). (#177)"""
    if mode == "observe":
        return {**cfg, "instance_template": OBSERVE_FIRST_INSTANCE}
    return cfg


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

    The cron turn is composed IDENTICALLY to the interactive/run_traced path
    (spec §6: the daemon never short-circuits compose_context). compose() resolves
    persona+memory; run_turn() builds the skill spine via persona.compose_context
    and the base/AGENTS.md block via base_prompt.render_base_prompt, then runs the
    turn with the REAL skill_block + base_block — not "" as before.

    All live-source symbols verified against run_traced.py + persona_sessions.py
    (see inline comments).
    """
    import platform
    from datetime import date

    from harness import agents as _agents       # resolve_agents: agents.py:56
    from harness import base_prompt as _base_prompt  # render_base_prompt: base_prompt.py:47
    from harness import flows as _flows         # scope_catalog: flows.py:11
    from harness import memory as _memory     # resolve_memory: memory.py
    from harness import paths as _paths        # skills_dirs/config_dir: paths.py:50/16
    from harness import persona as _persona   # resolve_persona / compose_context: persona.py:102/111
    from harness import persona_config as _persona_config  # read_flows: persona_config.py:38
    from harness import persona_sessions as _ps   # resolve_session_model: persona_sessions.py:20
    from harness import skills as _skills      # load_catalog_with_skips: skills.py:85

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
                 memory_block: str, message: str, wall_budget: int | None = None,
                 mode: str | None = None) -> None:
        # Compose skills + base block IDENTICALLY to run_traced.py:171-190 so the
        # cron turn is indistinguishable from the persona typing live (spec §6).
        skills_roots = _paths.skills_dirs(project_cwd=str(workspace))
        _catalog_load = _skills.load_catalog_with_skips(skills_roots)
        _enabled_flows = _persona_config.read_flows(workspace)
        _menu_metas = (_flows.scope_catalog(_catalog_load.skills, _enabled_flows)
                       if _enabled_flows else _catalog_load.skills)
        # compose_context bundles persona+memory+skills via the SAME chokepoint the
        # ACP path uses (acp_agent.py:540). skill_names=[] => lazy menu only.
        ctx = _persona.compose_context(persona_block, memory_block, skills_roots,
                                       [], menu_metas=_menu_metas)
        # Three-tier AGENTS.md (persona > project > global), run_traced.py:182.
        _agents_block = _agents.resolve_agents(
            persona_dir=workspace, project_cwd=workspace,
            global_dir=_paths.config_dir()).block
        base_block = _base_prompt.render_base_prompt(
            model_id=(model_id or "mock"),
            cwd=str(workspace),
            system_line=platform.platform(),
            skills_menu=ctx.skills_menu,
            agents_block=_agents_block)

        # Construction via the shared chokepoint (harness/agent_build.py). Cron
        # passes model_name=None for mock, else the qualified model; the builder
        # stamps env._active_persona = agent_id so env-bound tools resolve.
        from harness.agent_build import build_persona_agent
        runner, _registry = build_persona_agent(
            agent_id=workspace.name,
            model_name=(None if model_id is None else model_id),
            skill_roots=skills_roots,
            memory_root=workspace,
            agent_cfg=_observe_or_default_cfg(_load_agent_cfg(), mode),
            cwd=str(workspace),
        )
        # #168: this is a HEADLESS path (no elicitation channel), so file tools must
        # be gated + confined to the job's workspace — risky/out-of-root ops fail
        # CLOSED. Same chokepoint machinery as the ACP path, deny-by-default policy.
        # Applied to the env the builder constructed (runner._env).
        stamp_headless_gate(runner._env, workspace)
        # Cron budget (Task 8): stamp the job's configured timeout onto the env so
        # any subagent worker the turn spawns caps its wall-time at this budget
        # (subagent.py reads env._remaining_secs). Static upper bound, not a live
        # countdown, in v1. The interactive path never passes wall_budget, so it
        # leaves _remaining_secs unset (None) — behavior-preserving.
        if wall_budget:
            runner._env._remaining_secs = wall_budget
        # Pass the REAL skill_block + base_block (run_traced.py:195-198 parity).
        for _ in runner.run(message, skill_block=ctx.skill_block,
                            persona_block=ctx.persona_block,
                            memory_block=ctx.memory_block, base_block=base_block):
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
    # Task 8: hand the job's configured timeout to run_turn so a subagent worker
    # the turn spawns caps its wall-time at this budget. Only a positive timeout
    # counts; only passed to run_turn callables that accept the kwarg (keeps the
    # fixed-signature parity doubles green).
    _wall_budget = job.cost.timeout_s if job.cost.timeout_s and job.cost.timeout_s > 0 else None
    _turn_kwargs = dict(
        model_id=model_id,
        workspace=ws,
        persona_block=persona_block,
        memory_block=memory_block,
        message=job.payload.message,
    )
    if _wall_budget is not None and _accepts_kwarg(deps.run_turn, "wall_budget"):
        _turn_kwargs["wall_budget"] = _wall_budget
    _mode = job.payload.agent_options.get("mode")  # AgentTurn only; e.g. "observe"
    if _mode is not None and _accepts_kwarg(deps.run_turn, "mode"):
        _turn_kwargs["mode"] = _mode
    deps.run_turn(**_turn_kwargs)
