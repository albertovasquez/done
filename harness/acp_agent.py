"""HarnessAgent: the ACP agent. Per session/prompt turn: classify (Router) →
emit _meta → dispatch chat/ambiguous/agent. The agent loop runs on a worker
thread (via run_in_executor) with an AcpEnvironment whose callbacks marshal
session/update notifications back to the event loop. Router/ChatHandler also run
in the executor so the async loop stays responsive to session/cancel."""

from __future__ import annotations

import asyncio
import logging
import platform
import threading
import time
from pathlib import Path

import acp
from acp.schema import (
    AllowedOutcome,
    PermissionOption,
    ToolCallUpdate,
)

from harness import base_prompt
from harness import config
from harness import memory as memory_mod
from harness import persona
from harness import skills
from harness.acp_emit import (tool_call_start, tool_call_done, message_chunk,
                              with_meta, plan_update, trace_event)
from harness.acp_env import AcpEnvironment
from harness.acp_session import SessionStore
from harness.output_filters.dispatch import filter_output
from harness.router import Router, Classification
from harness.chat_handler import ChatHandler, is_tools_question
from harness.permcheck import PermissionRequest, decide_permission
from harness.transcript import flatten_agent_messages
from harness.instance_templates import done_agent_cfg
from minisweagent.exceptions import InterruptAgentFlow, UserInterruption

from harness.interruptible import run_interruptible

logger = logging.getLogger("harness.acp_agent")


# decide_permission now lives in harness/permcheck.py (the import-light leaf) so the
# headless cron-executor + dev-CLI paths can reuse it without a cycle (#168). Imported
# above; re-exported here implicitly for any caller that did `from acp_agent import
# decide_permission`.
# The per-task instance templates + the Done-native system template now live in
# harness/instance_templates.py (the import-light leaf); done_agent_cfg is imported
# implicitly so any caller that did `from acp_agent import ...` keeps working.


# The single privileged door now lives in harness/jobs/create.py (pure jobs logic,
# no ACP deps) so BOTH the "harness/create_job" ext-method (below) and the agent
# `create_job` tool can reach it. Re-exported here for the ext-method + existing
# test imports (`from harness.acp_agent import handle_create_job`).
from harness.jobs.create import handle_create_job  # noqa: E402,F401


def _resolve_output_filter():
    """Return the output-filter callable, or None when the operator has disabled
    filtering via ``[harness] output_filter = "false"`` in done.conf.

    Default-on: absent key, ``"true"``, or any other value → filtering active.
    Only the exact string ``"false"`` disables it.
    """
    if config.harness_setting("output_filter") == "false":
        return None
    return filter_output


class HarnessAgent(acp.Agent):
    def __init__(self, *, model_factory, agent_cfg, skills_dir: list[Path], router: Router,
                 worker_model_id, yolo: bool = False, backend: str = "vibeproxy",
                 workspace_dir: Path | None = None, cwd: str | None = None,
                 shell_set_model: bool = False, shell_env: str | None = None,
                 debug: bool = False):
        self._model_factory = model_factory
        self._agent_cfg = agent_cfg
        self._skills_dir = skills_dir
        self._workspace_dir = workspace_dir     # None => no persona (byte-identical)
        self._router = router
        self._worker_model_id = worker_model_id
        self._yolo = yolo                 # --yolo: auto-allow every command, no prompts
        self._backend = backend           # launch backend; paired with model on persist
        self._cwd = cwd
        self._shell_set_model = shell_set_model
        self._shell_env = shell_env
        self._debug = debug               # --debug: relay a JSONL trace over with_meta
        self._store = SessionStore()
        from harness.persona_sessions import PersonaSessions
        self._persona_sessions = PersonaSessions()
        self._active_persona = self._workspace_dir.name if self._workspace_dir else "default"
        self._conn = None

    def _auto_allow(self) -> bool:
        """True when the permission gate should allow without prompting the
        client (yolo mode). Kept tiny + pure so the gate is unit-testable."""
        return self._yolo

    async def _trace(self, session_id, type, **data):
        """Relay one trace event to the TUI sole-writer, only when --debug. Rides
        the existing with_meta channel; a no-op (and zero wire bytes) when debug
        is off, preserving the byte-identical-wire invariant."""
        if not self._debug:
            return
        await self._conn.session_update(
            session_id, with_meta(message_chunk(""), {"trace": trace_event(type, **data)}))

    def _persona_key(self) -> str:
        """The done.conf agent key the active seat persists under: the persona the
        client is currently driving (set by set_persona; "default" at launch).
        NOT a branch — "default" is just the id."""
        return self._active_persona

    def on_connect(self, conn) -> None:
        self._conn = conn

    async def ext_method(self, method: str, params: dict) -> dict:
        """Harness-specific extension methods. `harness/set_model` hot-swaps the
        worker model for SUBSEQUENT turns without restarting the agent — it stamps
        the active session's state.worker_model (read by prompt() on every turn),
        updates the seat in the persona-sessions map, and mirrors the value in
        self._worker_model_id (global fallback). All three are updated atomically
        so the very next prompt on the active session sees the new model."""
        if method == "harness/set_model":
            model = (params or {}).get("model")
            ok = True
            if model:
                self._worker_model_id = model
                self._persona_sessions.set_model(self._active_persona, model)
                seat = self._persona_sessions.seat_of(self._active_persona)
                if seat is not None:
                    try:
                        self._store.get(seat.session_id).worker_model = model
                    except KeyError:
                        pass            # session gone (shouldn't happen) — global+seat still updated
                try:                       # best-effort; report failure, never break the swap
                    config.save_agent(self._persona_key(),
                                      config.AgentConfig(backend=self._backend, model=model))
                except Exception:
                    # ok=False reaches the TUI, but the REASON is otherwise lost —
                    # a silent persist failure means the pin won't stick next launch.
                    logger.exception("failed to persist model pin for persona %r",
                                     self._persona_key())
                    ok = False
            return {"ok": ok, "model": self._worker_model_id}
        if method == "harness/set_goal":
            # Arm the /goal stop-gate on the active session's state (read by the
            # engine gate each turn). Resolve the session via the seat, exactly as
            # set_model does — there is no session_id param.
            from harness.goal_gate import GoalContext
            text = (params or {}).get("text")
            if not text:
                return {"ok": False, "error": "goal text required"}
            # Reviewer model: explicit override wins, else the persona's Layer A
            # reviewer-role config (that's the whole point of role-model config),
            # else the worker model. Codex #8.
            reviewer = (params or {}).get("reviewer_model")
            if not reviewer:
                from harness.role_model import load_role_tables, resolve_role_candidates
                reviewer = resolve_role_candidates(
                    self._active_persona, "reviewer",
                    load_role_tables(), self._worker_model_id or "")[0]
            seat = self._persona_sessions.seat_of(self._active_persona)
            if seat is None:
                return {"ok": False, "error": "no active session to arm the goal on"}
            try:
                self._store.get(seat.session_id).goal = GoalContext(
                    text=text, reviewer_model=reviewer)
            except KeyError:
                return {"ok": False, "error": "no active session"}
            return {"ok": True}
        if method == "harness/clear_goal":
            seat = self._persona_sessions.seat_of(self._active_persona)
            if seat is not None:
                try:
                    self._store.get(seat.session_id).goal = None
                except KeyError:
                    pass
            return {"ok": True}
        if method == "harness/set_yolo":
            # Live auto-allow toggle (+ optional persisted pin). The ACP process
            # owns the permission gate, so it owns both the flip and the write.
            # active/pin MUST be real booleans — this is a security gate, so a
            # non-bool (e.g. the string "false", which is truthy) is ignored, not
            # coerced. Returns the TRUE persisted state so the TUI can reconcile.
            params = params or {}
            if isinstance(params.get("active"), bool):
                self._yolo = params["active"]
            pin = params.get("pin")
            ok = True
            if isinstance(pin, bool):
                try:                       # best-effort: a failed write never breaks the toggle
                    if pin:
                        # Pair the pin with the agent's known backend+model so a
                        # fresh config never gets a persona table with empty
                        # required fields (which would later resolve to `--model ""`).
                        # model may be None (mock); pass it only when it's a real
                        # string — update_agent refuses to create an incomplete
                        # table, so the pin simply no-ops rather than corrupting.
                        fields = {"backend": self._backend, "yolo_pinned": True}
                        if isinstance(self._worker_model_id, str) and self._worker_model_id:
                            fields["model"] = self._worker_model_id
                        config.update_agent(self._persona_key(), **fields)
                    else:
                        config.update_agent(self._persona_key(), yolo_pinned=False)
                except Exception:
                    logger.exception("failed to persist yolo pin (=%s) for persona %r",
                                     pin, self._persona_key())
                    ok = False             # surface the failure; do NOT claim success
            try:
                pinned = config.yolo_pinned(self._persona_key())
            except Exception:
                logger.exception("failed to read back yolo pin for persona %r",
                                 self._persona_key())
                pinned = False
            return {"ok": ok, "active": self._yolo, "pinned": pinned}
        if method == "harness/set_persona":
            pid = (params or {}).get("id")
            if not isinstance(pid, str) or not pid:
                return {"ok": False, "error": "missing id"}
            from harness import persona_select
            try:
                return self._activate_seat(pid)
            except (persona_select.UnknownPersona, persona_select.InvalidPersonaId) as e:
                # The error dict reaches the TUI, but a durable log is the only
                # place a persona-switch failure is diagnosable after the fact.
                logger.warning("set_persona rejected id %r: %s", pid, e)
                return {"ok": False, "error": str(e)}
        if method == "harness/replay_session":
            pid = (params or {}).get("id")
            if not isinstance(pid, str) or not pid:
                return {"ok": False, "error": "missing id"}
            from harness import persona_select
            try:
                return await self._replay_session(pid)
            except (persona_select.UnknownPersona, persona_select.InvalidPersonaId) as e:
                logger.warning("replay_session rejected id %r: %s", pid, e)
                return {"ok": False, "error": str(e)}
        if method == "harness/create_persona":
            pid = (params or {}).get("id")
            if not isinstance(pid, str) or not pid:
                return {"ok": False, "error": "missing id"}
            display_name = (params or {}).get("display_name")
            if not isinstance(display_name, str):
                display_name = None
            from harness import persona, persona_select
            try:
                persona.create_persona(pid, display_name=display_name)  # raises InvalidPersonaId/PersonaExists/OSError
                return self._activate_seat(pid)          # raises UnknownPersona/InvalidPersonaId
            except (persona_select.InvalidPersonaId, persona.PersonaExists,
                    persona_select.UnknownPersona, OSError) as e:
                return {"ok": False, "error": str(e)}
        if method == "harness/create_job":
            # Privileged single-door path: fail-closed gate (agent_id, cost,
            # grant all required).  ops.add is NOT exposed as a normal agent
            # tool — this ext-method is the only way to write a job.
            try:
                return handle_create_job(params or {}, now=time.time())
            except (ValueError, KeyError, TypeError) as e:
                return {"ok": False, "error": str(e)}
        return {}

    def _seat_for(self, pid: str):
        """Resolve (get-or-create) the persona's seat — NO active-state mutation.
        Shared by _activate_seat (which then mirrors the model) and _replay_session
        (which only reads session_id). Raises UnknownPersona / InvalidPersonaId."""
        from harness import persona_select
        from harness.persona_sessions import resolve_session_model
        resolve_session_model_for = lambda p: resolve_session_model(
            p, shell_set_model=self._shell_set_model,
            shell_env=self._shell_env, dotenv=self._shell_env, backend=self._backend)
        return self._persona_sessions.get_or_create(
            pid, cwd=self._cwd, store=self._store,
            resolve_ws=persona_select.resolve_workspace,
            resolve_model=resolve_session_model_for)

    def _activate_seat(self, pid: str) -> dict:
        """Get-or-create the seat for persona `pid`, make it active, mirror its model
        into the read-site fallback + the session state, and return the switch result.
        Raises persona_select.UnknownPersona / InvalidPersonaId. The ONE activation path
        shared by set_persona and create_persona."""
        seat = self._seat_for(pid)
        self._active_persona = pid
        self._worker_model_id = seat.model      # mirror active seat for read sites
        sess = self._store.get(seat.session_id)
        sess.worker_model = seat.model
        count = len(sess.transcript)
        return {"ok": True, "id": pid, "session_id": seat.session_id,
                "model": seat.model, "message_count": count}

    async def _replay_session(self, pid: str) -> dict:
        """Stream the persona's stored transcript back to the client as ACP
        session_update notifications (rendered by the client's normal path), then
        a `resumed` seam. Read-only — uses _seat_for (no re-activation)."""
        from harness.acp_emit import message_chunk, user_message_chunk, with_meta
        sid = self._seat_for(pid).session_id
        transcript = self._store.get(sid).transcript
        for i, m in enumerate(transcript):
            # BOUNDARY between messages (Codex review — message-merge): the client's
            # _stream_message keeps ONE markdown widget open across consecutive
            # message deltas. Without a boundary, back-to-back replayed messages
            # MERGE into a single block. Emit a stream_reset meta before every
            # message after the first, so each renders as its OWN widget.
            if i > 0:
                await self._conn.session_update(
                    sid, with_meta(message_chunk(""), {"stream_reset": True}))
            upd = (user_message_chunk(m["content"]) if m["role"] == "user"
                   else message_chunk(m["content"]))
            await self._conn.session_update(sid, upd)
        if transcript:
            seam = with_meta(message_chunk(""), {"resumed": True})
            await self._conn.session_update(sid, seam)
        return {"ok": True, "count": len(transcript)}

    async def initialize(self, protocol_version, client_capabilities=None,
                         client_info=None, **kw):
        self._client_caps = client_capabilities
        return acp.InitializeResponse(
            protocol_version=acp.PROTOCOL_VERSION,
            agent_capabilities=acp.schema.AgentCapabilities(load_session=True),
        )

    async def new_session(self, cwd, additional_directories=None, mcp_servers=None, **kw):
        from harness.persona_sessions import resolve_session_model, Seat
        session_id = self._store.new(cwd=cwd, workspace_dir=self._workspace_dir)
        model = resolve_session_model(
            self._active_persona,
            shell_set_model=self._shell_set_model,
            shell_env=self._shell_env,
            dotenv=self._shell_env,
            backend=self._backend,
        )
        self._store.get(session_id).worker_model = model
        self._persona_sessions.register(self._active_persona, Seat(session_id=session_id, model=model))
        if model is not None:
            self._worker_model_id = model
        return acp.NewSessionResponse(session_id=session_id)

    async def load_session(self, cwd, session_id, additional_directories=None,
                           mcp_servers=None, **kw):
        try:
            state = self._store.get(session_id)
        except KeyError:
            # can't resume a session we never issued — reject, don't orphan a new one
            # (consistent with prompt()'s unknown-session handling)
            raise acp.RequestError.invalid_params()
        for turn in state.history:
            await self._conn.session_update(
                session_id,
                message_chunk(f"[resumed] {turn.get('kind', 'turn')}: {turn.get('prompt', '')}"),
            )
        return acp.LoadSessionResponse()

    async def cancel(self, session_id, **kw) -> None:
        try:
            state = self._store.get(session_id)
            state.cancel_flag.set()
            state.goal = None      # cancel disarms the goal so it can't hijack the next turn
        except KeyError:
            pass

    def _has_elicitation(self) -> bool:
        """True when the connected client can show a permission modal. False for
        headless/cron/CLI (no elicitation) — the signal the permission gate uses
        to fail closed, reused to decide whether a chat_question may escalate to
        the tool-running agent path."""
        return not (
            self._client_caps is None
            or getattr(self._client_caps, "elicitation", None) is None
        )

    async def prompt(self, prompt, session_id, message_id=None, **kw):
        loop = asyncio.get_running_loop()
        try:
            state = self._store.get(session_id)
        except KeyError:
            # invalid_params is a classmethod that exists in the installed SDK
            raise acp.RequestError.invalid_params()
        state.cancel_flag.clear()
        model_id = state.worker_model if state.worker_model is not None else self._worker_model_id
        text = "".join(getattr(b, "text", "") for b in prompt)
        transcript = state.transcript           # read once; every branch writes back per §6

        def _cancelled() -> acp.PromptResponse:
            # A cancel that lands during the turn preamble ends the turn cleanly —
            # same stop_reason the post-run_engine cancel checks use (L805/808).
            return acp.PromptResponse(stop_reason="cancelled")

        # Persona: compose once per session (cached). None => not-yet-read. Both the
        # chat and agent dispatch paths read state.persona_block, so the COMPOSE
        # must happen before routing; the telemetry EMIT is deferred until after
        # classification (below) so the persona_load event is ordered after
        # task_classified and is skipped on the unpersonalized clarify/ambiguous
        # branches — mirroring how skill_load only fires on the agent path.
        persona_first_load = None
        if state.persona_block is None:
            # resolve from the PER-SESSION workspace (state.workspace_dir), not the
            # per-agent self._workspace_dir — so persona and memory agree on the
            # session's workspace (the Phase-B isolation invariant). new_session
            # records state.workspace_dir = self._workspace_dir at session start.
            if state.cancel_flag.is_set():
                return _cancelled()
            persona_first_load = await loop.run_in_executor(
                None, persona.resolve_persona, state.workspace_dir)
            state.persona_block = persona_first_load.block
            state.persona_load = persona_first_load

        # Memory: compose once per session (cached). None => not-yet-read. Both the
        # chat and agent dispatch paths read state.memory_block, so the COMPOSE must
        # happen before routing; the telemetry EMIT is deferred until after
        # classification so memory_load is ordered after task_classified and is
        # skipped on the clarify/ambiguous branches — mirroring persona_load.
        if state.memory_block is None:
            from datetime import date
            if state.cancel_flag.is_set():
                return _cancelled()
            mload = await loop.run_in_executor(
                None, lambda: memory_mod.resolve_memory(state.workspace_dir, today=date.today()))
            state.memory_block = mload.block
            state.memory_load = mload

        # 1) classify in the executor (sync litellm call must not block the loop).
        # Cancel handling (Finding 3): check BEFORE the try — the except below
        # returns a "router unavailable" refusal, and UserInterruption IS an
        # Exception, so a raise inside the try would be misreported as a router
        # failure. run_interruptible aborts a stalled classify; the except
        # re-raises InterruptAgentFlow so a mid-flight cancel stays a clean cancel.
        if state.cancel_flag.is_set():
            return _cancelled()
        try:
            cls: Classification = await loop.run_in_executor(
                None, lambda: run_interruptible(
                    lambda: self._router.classify(text, history=transcript),
                    state.cancel_flag))
        except InterruptAgentFlow:
            return _cancelled()
        except Exception as e:  # router/VibeProxy unreachable
            # A turn that dies before it even classifies is one of the most
            # confusing failures to debug — log it (always) AND trace it (--debug),
            # not just flash a user message that scrolls away.
            logger.exception("router classify failed for persona %r", self._persona_key())
            await self._trace(session_id, "router.failed", sid=session_id, error=str(e))
            await self._conn.session_update(session_id,
                message_chunk(f"router unavailable: {e}"))
            return acp.PromptResponse(stop_reason="refusal")

        meta = {"task_type": cls.task_type, "skills": cls.skills,
                "confidence": cls.confidence}
        await self._conn.session_update(session_id,
            with_meta(message_chunk(""), {"task_classified": meta}))
        await self._trace(session_id, "task.classified", sid=session_id,
                          task_type=cls.task_type, skills=cls.skills,
                          confidence=cls.confidence)

        # Active-persona identity chip (C2a): the persona the agent ACTUALLY resolved.
        # Unlike persona_load, this is NOT gated on injected/personalized — an identity
        # indicator must show for EVERY session (incl. default) and on every dispatch
        # path (chat/agent/clarify/ambiguous). Once per session.
        if not state.persona_emitted:
            pid = state.workspace_dir.name if state.workspace_dir else "default"
            await self._conn.session_update(session_id,
                with_meta(message_chunk(""), {"persona": {"id": pid}}))
            state.persona_emitted = True

        # Deferred persona_load emit: after task_classified, only once per session
        # for a NON-EMPTY persona AND only on personalized dispatch paths
        # (chat/agent — never clarify/ambiguous). GATED on injected so the empty
        # default emits nothing (the byte-identical no-op guarantee).
        personalized = not (cls.needs_clarification or cls.task_type == "ambiguous")
        if (not state.persona_load_emitted and state.persona_load
                and state.persona_load.injected and personalized):
            await self._conn.session_update(session_id,
                with_meta(message_chunk(""),
                          {"persona_load": {"injected": state.persona_load.injected,
                                            "skipped": state.persona_load.skipped}}))
            state.persona_load_emitted = True

        if (not state.memory_load_emitted and state.memory_load
                and state.memory_load.injected and personalized):
            await self._conn.session_update(session_id,
                with_meta(message_chunk(""),
                          {"memory_load": {"injected": state.memory_load.injected,
                                           "skipped": state.memory_load.skipped}}))
            state.memory_load_emitted = True

        if cls.needs_clarification or cls.task_type == "ambiguous":
            q = cls.clarifying_question or "Could you clarify the task?"
            chunk = message_chunk(q)
            if cls.options:
                # Attach structured options so the TUI renders the DecisionModal.
                # Empty options => plain chunk, byte-identical to prior behavior.
                chunk = with_meta(chunk, {"decision": {
                    "question": q,
                    "options": [{"title": t, "rationale": r} for t, r in cls.options]}})
            await self._conn.session_update(session_id, chunk)
            self._store.record(session_id, {"prompt": text, "stop_reason": "end_turn",
                                            "kind": "clarify"})
            # write only the user turn — the clarifying question is router
            # boilerplate, not model output, so it must not pollute later context.
            self._store.extend(session_id, [
                {"role": "user", "content": text, "origin": "clarify"}])
            await self._trace(session_id, "clarify", sid=session_id, question=q)
            return acp.PromptResponse(stop_reason="end_turn")

        # Render base_block once — used by both chat and agent paths below.
        # Use the PER-SESSION model_id (resolved above from state.worker_model), not
        # the process-global self._worker_model_id, so the base prompt reflects the
        # active session's persona seat — consistent with how the model is bound for
        # this turn (C2c). Falls back to "mock" when there is no model.
        ws = state.workspace_dir
        # Lazy skill discovery: a flow-scoped MENU (names+descriptions) in the
        # prompt; the agent pulls bodies on demand via load_skill. Resolve roots
        # PER TURN from the session cwd so this session's project .agents/.claude
        # skills are included (the router's startup catalog is global-only).
        from harness import flows as _flows
        from harness import persona_config as _persona_config
        from harness import paths as _paths
        _skill_roots = _paths.skills_dirs(project_cwd=state.cwd)
        _catalog_load = skills.load_catalog_with_skips(_skill_roots, project_cwd=state.cwd)
        _enabled_flows = _persona_config.read_flows(ws)
        _menu_metas = (_flows.scope_catalog(_catalog_load.skills, _enabled_flows)
                       if _enabled_flows else _catalog_load.skills)
        _skills_menu = skills.compose_menu(_menu_metas)
        # Three-tier AGENTS.md (persona > project > global), folded into base_block
        # so BOTH the chat branch and the agent branch below inherit it (both consume
        # base_block). No-op when no AGENTS.md files exist.
        from harness import agents as _agents
        _agents_block = _agents.resolve_agents(
            persona_dir=ws,
            project_cwd=Path(state.cwd) if state.cwd else None,
            global_dir=_paths.config_dir()).block
        # Absolute path so the agent's Edit tool (which requires absolute paths) can
        # act on it; .resolve() also guards a relative XDG_CONFIG_HOME (Codex).
        base_block = base_prompt.render_base_prompt(
            persona_id=(ws.name if ws else None),
            persona_dir=(str(ws.resolve()) if ws else None),
            skills_menu=_skills_menu,
            agents_block=_agents_block)
        env_block = base_prompt.render_env_block(
            model_id=(model_id or "mock"),
            cwd=state.cwd, system_line=platform.platform())

        # cache.boundary: name which prompt block changed since the last turn.
        # Declared boundaries (persona/model swap, skills/AGENTS.md edits) are
        # expected; anything else appearing here is a silent cache invalidator.
        from harness import prompt_hash as _prompt_hash
        _hashes = _prompt_hash.block_hashes({
            "base": base_block,
            "persona": state.persona_block or "",
            "memory": state.memory_block or "",
            "env": env_block,
        })
        _changed = _prompt_hash.changed_blocks(state.prompt_hashes, _hashes)
        if _changed:
            await self._trace(session_id, "cache.boundary",
                              sid=session_id, changed=",".join(_changed))
        state.prompt_hashes = _hashes

        # #105: episodic history view — compaction episodes persist on the
        # session so between episodes the effective history is byte-stable
        # (cache-warm). The raw transcript stays append-only truth; the router
        # keeps consuming it directly (already tail-capped to 8 turns).
        from harness import compaction as _compaction
        from harness import history_view as _history_view

        def _summarize_history(middle: list[dict]) -> str:
            if model_id is None:
                raise RuntimeError("mock mode: no summarizer model")  # -> truncated
            import litellm  # lazy: keep the ~1s import off startup
            from harness import vibeproxy
            resp = litellm.completion(
                model=vibeproxy.model_id(model_id),
                **vibeproxy.completion_kwargs(),
                messages=[{"role": "system", "content": _compaction.COMPRESS_SYSTEM},
                          {"role": "user", "content": _compaction.render(middle)}],
                max_tokens=2000,
            )
            return resp.choices[0].message.content or ""

        _fixed_overhead = _compaction.estimate_tokens(
            base_block + (state.persona_block or "") + (state.memory_block or "")
            + env_block + text)
        # reconcile can summarize via a blocking LLM call (_summarize_history) —
        # the only other blocking-LLM call in prompt() besides classify, so it
        # gets the same run_interruptible treatment: ESC aborts a stalled/slow
        # summarize instead of blocking until it returns (the #254 invariant).
        try:
            history, _new_view, _hist_result = await loop.run_in_executor(
                None, lambda: run_interruptible(
                    lambda: _history_view.reconcile(
                        transcript, state.compact_view,
                        summarize=_summarize_history,
                        fixed_overhead_tokens=_fixed_overhead,
                        ctx_window=_compaction.resolve_ctx_window(model_id or ""),
                    ),
                    state.cancel_flag))
        except InterruptAgentFlow:
            return _cancelled()
        if _hist_result.compressed:
            await self._trace(session_id, "cache.boundary", sid=session_id,
                              changed="history", method=_hist_result.method)
            state.compact_view = _new_view

        # Chat turns that want a tool escalate to the tool-running agent path.
        # Gated tightly so this is ZERO-COST on every path that can't escalate —
        # mock (no model_id), headless/cron (no elicitation, so the authorization
        # surface for unattended runs is unchanged), and deterministic tools
        # questions (answered from data in the chat block below). Only when ALL
        # gates pass do we build the registry + probe. wants_tool is a throwaway
        # boolean classifier — on True we reassign task_type so control falls
        # through to the agent path (single record site, gate + engine reused).
        if (cls.task_type == "chat_question" and model_id is not None
                and self._has_elicitation() and not is_tools_question(text)):
            if state.cancel_flag.is_set():
                return _cancelled()
            try:
                # Build the registry lazily — the SAME one the agent path uses
                # (this session's skill roots + persona memory) — only here, never
                # on the common prose path.
                from harness.tools.registry import build_registry as _build_registry
                _chat_tool_schemas = [
                    t.schema for t in _build_registry(
                        skill_roots=_skill_roots,
                        memory_root=(ws.resolve() if ws else None))]
                _probe = ChatHandler(
                    model_id, base_block=base_block,
                    persona_block=(state.persona_block or "") + (state.memory_block or ""),
                    env_block=env_block,
                    tool_schemas=_chat_tool_schemas)
                wants = await loop.run_in_executor(
                    None, lambda: _probe.wants_tool(
                        text, history=history, cancel_flag=state.cancel_flag))
            except Exception:
                # Fail-open: any error deciding escalation degrades to the prose
                # chat path (today's behavior), never crashes the turn.
                logger.exception("chat tool-probe failed; falling back to prose chat")
                wants = False
            if wants:
                cls.task_type = "code_feature"   # route to the agent path below

        if cls.task_type == "chat_question":
            # hand the (project-aware) catalog so "what skills do we have?" is
            # answered from data, not the model. Surface DROPPED (malformed) and
            # SHADOWED (overridden across roots) skills so the user sees why a skill
            # is unselectable / which copy is active. Reuse the per-turn catalog load.
            handler = ChatHandler(model_id, catalog=_catalog_load.skills,
                                  persona_block=(state.persona_block or "") + (state.memory_block or ""),
                                  base_block=base_block,
                                  env_block=env_block,
                                  skipped=_catalog_load.skipped,
                                  shadowed=_catalog_load.shadowed)
            pieces: list[str] = []
            chat_prose = {"buf": ""}
            chat_lock = threading.Lock()

            async def _chat_flush() -> None:
                with chat_lock:
                    text_out, chat_prose["buf"] = chat_prose["buf"], ""
                if text_out:
                    await self._conn.session_update(session_id, message_chunk(text_out))

            def pump() -> None:
                # answer_stream is a blocking generator (litellm); run it on the
                # worker thread. transcript source (pieces) stays per-piece; delivery
                # is buffered and drained by the timer + the final flush below.
                # cancel_flag: ESC aborts a stalled/streaming chat answer (raises
                # UserInterruption from answer_stream, caught below).
                for piece in handler.answer_stream(text, history=history,
                                                   cancel_flag=state.cancel_flag):
                    pieces.append(piece)
                    with chat_lock:
                        chat_prose["buf"] += piece

            async def _chat_flush_loop() -> None:
                while True:
                    try:
                        await asyncio.sleep(0.08)
                        await _chat_flush()
                    except asyncio.CancelledError:
                        return
                    except Exception:
                        # transient session_update failure must not permanently
                        # stop mid-turn delivery; the turn-end flush still runs
                        # so no data is lost — just keep ticking.
                        logger.exception("stream flush loop error (turn-end flush still delivers)")

            chat_flush_task = loop.create_task(_chat_flush_loop())
            try:
                await loop.run_in_executor(None, pump)
            except UserInterruption:
                # ESC during a chat answer: end the turn cleanly (not a crash /
                # disconnect). Persist nothing partial; the TUI clears on turn-end.
                await self._trace(session_id, "chat.cancelled", sid=session_id)
                return acp.PromptResponse(stop_reason="cancelled")
            finally:
                await _chat_flush()          # deliver the tail
                chat_flush_task.cancel()     # no leftover timer into the next turn
            answer = "".join(pieces)
            self._store.record(session_id, {"prompt": text, "stop_reason": "end_turn",
                                            "kind": "chat"})
            self._store.extend(session_id, [
                {"role": "user", "content": text, "origin": "chat"},
                {"role": "assistant", "content": answer, "origin": "chat"}])
            await self._trace(session_id, "chat.done", sid=session_id)
            return acp.PromptResponse(stop_reason="end_turn")

        # agent path
        # offload compose_context: it does filesystem I/O (skills); keep the event loop free
        if state.cancel_flag.is_set():
            return _cancelled()
        ctx = await loop.run_in_executor(
            None, persona.compose_context, state.persona_block or "",
            state.memory_block or "", _skill_roots, cls.skills)
        await self._conn.session_update(session_id,
            with_meta(message_chunk(""),
                      {"skill_load": {"injected": ctx.skills.injected,
                                      "skipped": ctx.skills.skipped}}))
        engine = await self._run_agent_turn(loop, session_id, state, text, ctx.skill_block,
                                            history, ctx.persona_block, ctx.memory_block,
                                            base_block=base_block, env_block=env_block,
                                            model_id=model_id,
                                            task_type=cls.task_type,
                                            skills=ctx.skills.injected)
        stop_reason = engine["stop_reason"]
        if stop_reason == "refusal":
            # streamed-on-screen == stored: never fold prior-turn prose in.
            # flatten_agent_messages(agent.messages) includes the injected prior
            # transcript, so on failure use only THIS turn's streamed buffer.
            assistant = engine.get("streamed", "") or engine["exit_status"] or stop_reason
        else:
            assistant = engine["assistant"] or engine["exit_status"] or stop_reason   # never empty
        self._store.record(session_id, {"prompt": text, "stop_reason": stop_reason,
                                        "kind": "agent"})
        self._store.extend(session_id, [
            {"role": "user", "content": text, "origin": "agent"},
            {"role": "assistant", "content": assistant, "origin": "agent"}])
        await self._trace(session_id, "run.finished", sid=session_id, stop_reason=stop_reason)
        return acp.PromptResponse(stop_reason=stop_reason)

    async def _run_agent_turn(self, loop, session_id, state, text, skill_block, prior,
                              persona_block="", memory_block="", base_block="", env_block="",
                              model_id=None,
                              task_type="", skills=()) -> dict:
        # Tool-call ids are TURN-LOCAL: the counter resets each turn and the
        # "current id" lives here (not on SessionState), so the start/done/permission
        # handshake within this turn pairs correctly and ids restart at tc1 per turn.
        tc = {"n": 0, "id": "tc0"}

        # --- streaming: marshal each prose delta to the TUI, accumulate into a
        # buffer (the failure-case transcript), and signal a per-step boundary so
        # the client can close the previous prose block before the next one. ---
        streamed = {"buf": ""}
        agent_ref = {"agent": None}     # bound to the TracingAgent in run_engine
        last_step = {"n": -1}
        compacted = {"event": None}     # set if context.compacted fired this turn

        # Delivery-only prose buffer (distinct from streamed["buf"], the
        # failure-case transcript). Prose accumulates here on the worker thread and
        # is drained to the wire at ~80ms (matching the TUI's 12Hz render), so we
        # stop blocking the worker thread on a per-token RPC round-trip. Ordering
        # vs. boundaries/tool/plan events is preserved because emit_step_boundary,
        # on_command, and on_plan all flush this buffer first (see
        # _flush_prose_sync calls below), plus a final flush at turn end.
        prose = {"buf": ""}
        prose_lock = threading.Lock()

        async def _flush_prose() -> None:
            # loop-side: atomically take the pending prose and send it as one chunk.
            with prose_lock:
                chunk, prose["buf"] = prose["buf"], ""
            if chunk:
                await self._conn.session_update(session_id, message_chunk(chunk))

        def _flush_prose_sync() -> None:
            # worker-thread-callable: marshal the flush to the loop and block, the
            # same idiom the boundary/tool/plan callbacks already use.
            asyncio.run_coroutine_threadsafe(_flush_prose(), loop).result()

        def emit_step_boundary() -> None:
            # Drain pending prose FIRST so this boundary lands after the prose that
            # preceded it on the wire (ordering invariant). Then emit the boundary.
            _flush_prose_sync()
            # tell the TUI: a NEW prose block begins (close any open one).
            upd = with_meta(message_chunk(""), {"stream_reset": True})
            asyncio.run_coroutine_threadsafe(
                self._conn.session_update(session_id, upd), loop).result()

        def emit_delta(piece: str) -> None:
            # ESC mid-stream: raising here aborts the model's `for chunk in stream`
            # loop (streaming_model.py) on the next prose token. UserInterruption is
            # an InterruptAgentFlow → the engine loop's handler ends the turn with a
            # cancelled exit (same path as the between-steps checkpoint).
            if state.cancel_flag.is_set():
                raise UserInterruption({
                    "role": "exit", "content": "Cancelled by user.",
                    "extra": {"exit_status": "cancelled", "submission": ""}})
            # first delta of a NEW step (new n_calls) → boundary first. n_calls is
            # incremented in TracingAgent.query() BEFORE model.query() fires
            # on_delta, so the first delta of each step sees a fresh n_calls value
            # → exactly one boundary per step (covers FormatError steps that never
            # emit a tool event).
            n = getattr(agent_ref["agent"], "n_calls", 0)
            if n != last_step["n"]:
                last_step["n"] = n
                emit_step_boundary()
            # transcript source — UNCHANGED, synchronous, per-piece.
            streamed["buf"] += piece
            # delivery — buffer, do not block. The ~80ms timer + turn-end flush
            # drain it. No .result() per token.
            with prose_lock:
                prose["buf"] += piece

        def on_command(phase: str, command: str, out: dict | None) -> None:
            # runs on the worker thread → marshal to the loop and block until sent.
            # Flush pending prose FIRST so a tool-call event never overtakes the
            # prose that preceded it.
            _flush_prose_sync()
            if phase == "start":
                tc["n"] += 1
                tc["id"] = f"tc{tc['n']}"
                upd = tool_call_start(tc["id"], command)
            elif phase in ("done", "rejected"):
                result = out if out is not None else {"output": "permission denied",
                                                      "returncode": -1, "exception_info": ""}
                upd = tool_call_done(tc["id"], result)
            else:
                return
            fut = asyncio.run_coroutine_threadsafe(
                self._conn.session_update(session_id, upd), loop)
            fut.result()

        def on_plan(entries: list[tuple[str, str]]) -> None:
            # runs on the worker thread → marshal the ACP plan update to the loop.
            # Flush pending prose FIRST (ordering invariant). Full-snapshot replace:
            # the agent re-emits the whole list each time.
            _flush_prose_sync()
            asyncio.run_coroutine_threadsafe(
                self._conn.session_update(session_id, plan_update(entries)), loop).result()

        def on_progress(meta: dict) -> None:
            # Mid-turn sub-activity (SubagentTool workers) → a message_chunk carrying
            # field_meta["workers"]. FIRE-AND-FORGET: no .result(). Progress has no
            # ordering requirement vs. the tool's completion, and up to N worker
            # threads call this concurrently — blocking on .result() from each would
            # serialize the workers on the single event loop and defeat the very
            # parallelism subagents exist for. run_coroutine_threadsafe is itself
            # thread-safe; the loop serializes the sends.
            # meta is {"workers": {...}}; with_meta nests it under field_meta["harness"],
            # so the TUI reads field_meta["harness"]["workers"] (same namespace as
            # task_classified / skill_load).
            asyncio.run_coroutine_threadsafe(
                self._conn.session_update(session_id,
                                          with_meta(message_chunk(""), meta)), loop)

        def check_permission(req: PermissionRequest) -> bool:
            yolo = self._auto_allow()
            has_elicitation = self._has_elicitation()
            verdict = decide_permission(req, yolo=yolo, has_elicitation=has_elicitation)
            if verdict == "allow":
                return True
            if verdict == "deny":
                return False
            # verdict == "ask": prompt the client
            tc_id = tc["id"]
            options = [
                PermissionOption(kind="allow_once", name="Allow once", option_id="allow_once"),
                PermissionOption(kind="reject_once", name="Reject", option_id="reject_once"),
            ]
            title = f"$ {req.command}" if req.kind == "bash" else f"{'write' if req.is_write else 'read'} {req.path}"
            tool_call = ToolCallUpdate(tool_call_id=tc_id, title=title)
            coro = self._conn.request_permission(
                options=options, session_id=session_id, tool_call=tool_call
            )
            resp = asyncio.run_coroutine_threadsafe(coro, loop).result()
            return isinstance(resp.outcome, AllowedOutcome)

        client_terminal = None
        if getattr(self._client_caps, "terminal", None):
            def client_terminal(command: str) -> dict:
                from acp.schema import TerminalExitStatus
                # create → wait_for_exit → output → release  (all on worker thread)
                create_resp = asyncio.run_coroutine_threadsafe(
                    self._conn.create_terminal(command=command, session_id=session_id,
                                               cwd=state.cwd),
                    loop,
                ).result()
                tid = create_resp.terminal_id
                try:
                    asyncio.run_coroutine_threadsafe(
                        self._conn.wait_for_terminal_exit(session_id=session_id,
                                                          terminal_id=tid),
                        loop,
                    ).result()
                    out_resp = asyncio.run_coroutine_threadsafe(
                        self._conn.terminal_output(session_id=session_id, terminal_id=tid),
                        loop,
                    ).result()
                finally:
                    # always release, even if wait/output raised — no terminal leak
                    asyncio.run_coroutine_threadsafe(
                        self._conn.release_terminal(session_id=session_id, terminal_id=tid),
                        loop,
                    ).result()
                exit_status: TerminalExitStatus | None = getattr(out_resp, "exit_status", None)
                # unknown exit status → -1 (error), NOT 0 — never misreport a failure as success
                returncode = exit_status.exit_code if exit_status is not None else -1
                return {
                    "output": out_resp.output or "",
                    "returncode": returncode,
                    "exception_info": "",
                }

        env = AcpEnvironment(cwd=state.cwd, on_command=on_command,
                             check_permission=check_permission,
                             cancel_flag=state.cancel_flag,
                             client_terminal=client_terminal,
                             on_plan=on_plan,
                             on_progress=on_progress,
                             output_filter=_resolve_output_filter())
        # Bind the active persona onto the env so the create_job tool can resolve
        # agent_id from it (never from the model). Per-session workspace name, or
        # "default" with no persona. Mirrors the env._loaded_skills stamp pattern.
        env._active_persona = state.workspace_dir.name if state.workspace_dir else "default"
        # Allowed write/confine roots: the session cwd plus the persona workspace
        # (which lives OUTSIDE cwd — config_dir()/agents/<id> — so memory writes
        # must not be classified outside-root). Consumed by permcheck + file tools.
        from pathlib import Path as _Path
        env._allowed_roots = [_Path(state.cwd)] + ([_Path(state.workspace_dir)] if state.workspace_dir else [])

        def run_engine() -> dict:
            from harness.tracing_agent import TracingAgent
            from harness.relay_emitter import RelayEmitter
            # ACP carries the user-facing stream; most engine trace events stay
            # internal unless --debug is on. Usage is the exception: it feeds the
            # always-visible context/usage footer.
            def _relay(ev: dict) -> None:
                data = ev.get("data") or {}
                if ev["type"] == "context.compacted":
                    compacted["event"] = data
                meta = {}
                usage = data.get("usage") if ev["type"] == "llm.return" else None
                if isinstance(usage, dict) and isinstance(usage.get("total"), int):
                    meta["usage"] = usage
                if self._debug:
                    meta["trace"] = {"type": ev["type"],
                                     "data": {"sid": session_id, **data}}
                if meta:
                    # Intentionally NOT flushed-before: this chunk's text is always
                    # "" (nothing to paint) and its payload (usage/trace) is
                    # order-independent relative to prose — _maybe_update_tokens
                    # just sets self._tokens regardless of arrival order. So
                    # usage-vs-prose is commutative; flushing here would only add
                    # RPC round-trips with no visible benefit.
                    upd = with_meta(message_chunk(""), meta)
                    asyncio.run_coroutine_threadsafe(
                        self._conn.session_update(session_id, upd), loop).result()

            emitter = RelayEmitter("/dev/null", clock=lambda: 0.0, relay=_relay)
            # Make both templates Done-native: strip upstream's "helpful assistant
            # that can interact with a computer" system prompt AND pick the per-task
            # instance_template (answer-only for explain, observe-first for ops,
            # etc.) so neither the system nor the user turn carries the engine's
            # "solve this issue / edit the source" SWE-bench framing.
            cfg = done_agent_cfg(self._agent_cfg, task_type, skills)
            agent = None  # bound before construction so the except can reference it
            try:
                # pass the CURRENT worker model so /models hot-swaps the agent path
                # too; the factory ignores the arg in mock mode.
                model_obj = self._model_factory(model_id if model_id is not None else self._worker_model_id,
                                                project_cwd=state.cwd,
                                                memory_root=state.workspace_dir)
                agent = TracingAgent(model_obj, env,
                                     emitter=emitter, skill_block=skill_block,
                                     persona_block=persona_block, memory_block=memory_block,
                                     base_block=base_block, env_block=env_block,
                                     # share the model's registry; None (mock) => agent default.
                                     registry=getattr(model_obj, "registry", None),
                                     # ESC checkpoint: the loop ends between steps when set.
                                     cancel_flag=state.cancel_flag,
                                     # /goal stop-gate: None when unarmed → no-op.
                                     goal_ctx=state.goal,
                                     **cfg)
                agent_ref["agent"] = agent
                model = agent.model
                # mock model has no on_delta attr → bind nothing → mock mode unchanged.
                if hasattr(model, "on_delta"):
                    model.on_delta = emit_delta
                # Same guard for cancel_flag: lets _query abort a stalled /
                # pre-first-token litellm call (emit_delta only fires once a token
                # arrives). Mock model lacks the attr → untouched.
                if hasattr(model, "cancel_flag"):
                    model.cancel_flag = state.cancel_flag
                try:
                    result = agent.run(text, prior=prior)
                    return {"stop_reason": "end_turn",
                            "exit_status": result.get("exit_status", "end_turn"),
                            "assistant": flatten_agent_messages(agent.messages),
                            "streamed": streamed["buf"],
                            "compacted": compacted["event"]}
                finally:
                    # Write the goal ctx back to the session: the gate clears it on
                    # met/escape and mutates attempts on continue, all on the agent's
                    # copy — persist that so the next turn sees the true state (not a
                    # stale re-armed goal). Codex #3/#7.
                    state.goal = getattr(agent, "goal_ctx", None)
                    # never marshal a delta to a dead loop after the turn ends.
                    if hasattr(model, "on_delta"):
                        model.on_delta = None
                    # final flush: deliver any prose still buffered (the last <80ms
                    # that never hit a timer tick). Runs on success, failure, AND
                    # cancellation (this finally covers all three). Flush BEFORE the
                    # timer is cancelled (Task 3) so the tail is never skipped.
                    _flush_prose_sync()
                    # unbind cancel_flag so a later abandoned worker can't observe
                    # a flag that now belongs to the next turn.
                    if hasattr(model, "cancel_flag"):
                        model.cancel_flag = None
            except BaseException:  # engine/construction failure → refusal; capture any prose
                # BaseException (not just Exception): tracing_agent re-raises
                # BaseException, so a BaseException-only exc (asyncio.CancelledError,
                # SystemExit, KeyboardInterrupt) would otherwise escape this handler,
                # kill the ACP request task, exit the agent process, and surface in
                # the TUI as 'agent disconnected (Connection closed)'. Catching it
                # here turns an intermittent disconnect into a clean refusal. Mirrors
                # runner.py, which already catches BaseException for the same reason.
                # The refusal otherwise hides WHY the turn died (bad model id,
                # litellm/network error, engine crash). Log the traceback — this
                # runs on the worker thread, so logger.exception is the right sink.
                logger.exception("agent engine failed (model=%r, persona=%r)",
                                 self._worker_model_id, self._persona_key())
                return {"stop_reason": "refusal", "exit_status": "refusal",
                        "assistant": flatten_agent_messages(getattr(agent, "messages", [])),
                        "streamed": streamed["buf"],
                        "compacted": compacted["event"]}

        if state.cancel_flag.is_set():
            return {"stop_reason": "cancelled", "exit_status": "cancelled", "assistant": ""}
        async def _flush_loop() -> None:
            # ~80ms cadence matches the TUI's 12Hz render; finer delivery is
            # invisible (the TUI buffers and paints on its own timer).
            while True:
                try:
                    await asyncio.sleep(0.08)
                    await _flush_prose()
                except asyncio.CancelledError:
                    return
                except Exception:
                    # transient session_update failure must not permanently
                    # stop mid-turn delivery; the turn-end flush still runs
                    # so no data is lost — just keep ticking.
                    logger.exception("stream flush loop error (turn-end flush still delivers)")

        flush_task = loop.create_task(_flush_loop())
        try:
            engine = await loop.run_in_executor(None, run_engine)
        finally:
            # stop the periodic flusher so it can never fire into a later turn.
            # run_engine's own finally already did the FINAL prose flush, so no
            # tail is lost by cancelling here.
            flush_task.cancel()
        if state.cancel_flag.is_set():
            return {"stop_reason": "cancelled", "exit_status": "cancelled", "assistant": ""}
        # Surface context compaction to the TUI via the with_meta channel so the
        # turn footer can show a dim note.  Emitted only when compaction fired.
        if engine.get("compacted"):
            await self._conn.session_update(session_id,
                with_meta(message_chunk(""), {"context_compacted": engine["compacted"]}))
        return engine


def build_harness_agent(*, model_factory, agent_cfg, skills_dir: list[Path],
                        router: Router, worker_model_id=None,
                        backend: str = "vibeproxy",
                        workspace_dir: Path | None = None,
                        debug: bool = False) -> HarnessAgent:
    """Factory: wire the agent from resolved dependencies. `backend` mirrors the
    direct HarnessAgent(...) construction in acp_main: it drives session-model
    resolution (backend="mock" => no real worker model, no network), so the
    factory must forward it rather than silently defaulting to vibeproxy."""
    return HarnessAgent(
        model_factory=model_factory,
        agent_cfg=agent_cfg,
        skills_dir=skills_dir,
        router=router,
        worker_model_id=worker_model_id,
        backend=backend,
        workspace_dir=workspace_dir,
        debug=debug,
    )
