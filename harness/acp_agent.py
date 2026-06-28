"""HarnessAgent: the ACP agent. Per session/prompt turn: classify (Router) →
emit _meta → dispatch chat/ambiguous/agent. The agent loop runs on a worker
thread (via run_in_executor) with an AcpEnvironment whose callbacks marshal
session/update notifications back to the event loop. Router/ChatHandler also run
in the executor so the async loop stays responsive to session/cancel."""

from __future__ import annotations

import asyncio
import logging
import platform
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
from harness.router import Router, Classification
from harness.chat_handler import ChatHandler
from harness.transcript import flatten_agent_messages
from minisweagent.exceptions import UserInterruption

logger = logging.getLogger("harness.acp_agent")


# Answer-only instance template for code_explain turns. The engine's default
# instance_template (mini.yaml) is injected as the USER turn EVERY step and reads
# "Please solve this issue: {{task}} … Edit the source code to resolve it" — an
# every-turn work-order framing that overrides the clarify-before-acting skill
# (appended far down the SYSTEM prompt) and makes the agent edit files when the
# user only asked it to look. For an explain classification we swap that framing
# for one whose job is to answer, not act. {{task}} is preserved so the agent
# still sees the request; Jinja renders it the same way the default does.
ANSWER_ONLY_INSTANCE = (
    "The user asked: {{task}}\n\n"
    "This is a QUESTION, not a work order. Investigate as needed — read files, "
    "run read-only commands — then ANSWER in words. Do NOT edit, create, or "
    "delete files to answer it. If a good answer would require changing code, "
    "say so and ask whether to proceed; do not start the change yourself. "
    "When you have answered, finish by issuing exactly: "
    "`echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`."
)


def _instance_template_for(task_type: str, default: str) -> str:
    """Pick the engine instance_template for this turn. code_explain gets the
    answer-only template (the clarify gate, enforced at the prompt the model
    actually obeys); every other task_type keeps the engine default unchanged."""
    return ANSWER_ONLY_INSTANCE if task_type == "code_explain" else default


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
        return {}

    def _activate_seat(self, pid: str) -> dict:
        """Get-or-create the seat for persona `pid`, make it active, mirror its model
        into the read-site fallback + the session state, and return the switch result.
        Raises persona_select.UnknownPersona / InvalidPersonaId. The ONE activation path
        shared by set_persona and create_persona."""
        from harness import persona_select
        from harness.persona_sessions import resolve_session_model
        resolve_session_model_for = lambda p: resolve_session_model(
            p, shell_set_model=self._shell_set_model,
            shell_env=self._shell_env, dotenv=self._shell_env, backend=self._backend)
        seat = self._persona_sessions.get_or_create(
            pid, cwd=self._cwd, store=self._store,
            resolve_ws=persona_select.resolve_workspace,
            resolve_model=resolve_session_model_for)
        self._active_persona = pid
        self._worker_model_id = seat.model      # mirror active seat for read sites
        self._store.get(seat.session_id).worker_model = seat.model
        return {"ok": True, "id": pid, "session_id": seat.session_id, "model": seat.model}

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
            self._store.get(session_id).cancel_flag.set()
        except KeyError:
            pass

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
            mload = await loop.run_in_executor(
                None, lambda: memory_mod.resolve_memory(state.workspace_dir, today=date.today()))
            state.memory_block = mload.block
            state.memory_load = mload

        # 1) classify in the executor (sync litellm call must not block the loop)
        try:
            cls: Classification = await loop.run_in_executor(
                None, lambda: self._router.classify(text, history=transcript))
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
                # Attach structured options so the TUI renders DecisionPrompt.
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
        # prompt; the agent pulls bodies on demand via load_skill. No persona
        # flows => full catalog, no gating (no-op vs. before).
        from harness import flows as _flows
        from harness import persona_config as _persona_config
        _enabled_flows = _persona_config.read_flows(ws)
        _menu_metas = (_flows.scope_catalog(self._router.catalog, _enabled_flows)
                       if _enabled_flows else self._router.catalog)
        _skills_menu = skills.compose_menu(_menu_metas)
        # Three-tier AGENTS.md (persona > project > global), folded into base_block
        # so BOTH the chat branch and the agent branch below inherit it (both consume
        # base_block). No-op when no AGENTS.md files exist.
        from harness import agents as _agents
        from harness import paths as _paths
        _agents_block = _agents.resolve_agents(
            persona_dir=ws,
            project_cwd=Path(state.cwd) if state.cwd else None,
            global_dir=_paths.config_dir()).block
        # Absolute path so the agent's Edit tool (which requires absolute paths) can
        # act on it; .resolve() also guards a relative XDG_CONFIG_HOME (Codex).
        base_block = base_prompt.render_base_prompt(
            model_id=(model_id or "mock"),
            cwd=state.cwd, system_line=platform.platform(),
            persona_id=(ws.name if ws else None),
            persona_dir=(str(ws.resolve()) if ws else None),
            skills_menu=_skills_menu,
            agents_block=_agents_block)

        if cls.task_type == "chat_question":
            # hand the router's catalog so "what skills do we have?" is answered
            # from data, not the model (see ChatHandler.is_capability_question).
            # Also surface skills DROPPED at load (malformed/name-mismatch) so the
            # user learns why a skill is unselectable rather than it vanishing.
            _roots = self._skills_dir if isinstance(self._skills_dir, list) else [self._skills_dir]
            _skipped = skills.load_catalog_with_skips(_roots).skipped
            handler = ChatHandler(model_id, catalog=self._router.catalog,
                                  persona_block=(state.persona_block or "") + (state.memory_block or ""),
                                  base_block=base_block, skipped=_skipped)
            pieces: list[str] = []

            def pump() -> None:
                # answer_stream is a blocking generator (litellm); run it on the
                # worker thread and marshal each piece back to the loop as its own
                # message_chunk — same idiom as the tool-call path above. Accumulate
                # the pieces so the full answer can be written to the transcript.
                for piece in handler.answer_stream(text, history=transcript):
                    pieces.append(piece)
                    asyncio.run_coroutine_threadsafe(
                        self._conn.session_update(session_id, message_chunk(piece)),
                        loop).result()

            await loop.run_in_executor(None, pump)
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
        ctx = await loop.run_in_executor(
            None, persona.compose_context, state.persona_block or "",
            state.memory_block or "", self._skills_dir, cls.skills)
        await self._conn.session_update(session_id,
            with_meta(message_chunk(""),
                      {"skill_load": {"injected": ctx.skills.injected,
                                      "skipped": ctx.skills.skipped}}))
        engine = await self._run_agent_turn(loop, session_id, state, text, ctx.skill_block,
                                            transcript, ctx.persona_block, ctx.memory_block,
                                            base_block=base_block, model_id=model_id,
                                            task_type=cls.task_type)
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
                              persona_block="", memory_block="", base_block="", model_id=None,
                              task_type="") -> dict:
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

        def emit_step_boundary() -> None:
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
            streamed["buf"] += piece
            asyncio.run_coroutine_threadsafe(
                self._conn.session_update(session_id, message_chunk(piece)), loop).result()

        def on_command(phase: str, command: str, out: dict | None) -> None:
            # runs on the worker thread → marshal to the loop and block until sent
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
            # Full-snapshot replace: the agent re-emits the whole list each time.
            asyncio.run_coroutine_threadsafe(
                self._conn.session_update(session_id, plan_update(entries)), loop).result()

        def request_permission(command: str) -> bool:
            # --yolo: auto-allow everything, no client round-trip, no modal.
            if self._auto_allow():
                return True
            # Auto-allow (standalone path) unless the client advertised it can
            # handle permission prompts. ACP routes permission via elicitation;
            # gate on that rather than a bare None-check so a client that sends
            # capabilities without elicitation support isn't asked to answer a
            # prompt it can't service.
            if self._client_caps is None or getattr(self._client_caps, "elicitation", None) is None:
                return True
            tc_id = tc["id"]
            options = [
                PermissionOption(kind="allow_once", name="Allow once", option_id="allow_once"),
                PermissionOption(kind="reject_once", name="Reject", option_id="reject_once"),
            ]
            # carry the actual command in title so the client can show it
            # ("$ <cmd>") instead of the opaque tool_call_id.
            tool_call = ToolCallUpdate(tool_call_id=tc_id, title=f"$ {command}")
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
                             request_permission=request_permission,
                             cancel_flag=state.cancel_flag,
                             client_terminal=client_terminal,
                             on_plan=on_plan)

        def run_engine() -> dict:
            from harness.tracing_agent import TracingAgent
            from harness.events import Emitter
            # ACP carries the user-facing stream; the engine's own event stream
            # (llm.call/llm.return/action/action.done/run.*) is normally discarded.
            # Under --debug, relay each event to the TUI sole-writer over the same
            # with_meta channel instead of dropping it to /dev/null.
            if self._debug:
                from harness.relay_emitter import RelayEmitter

                def _relay(ev: dict) -> None:
                    upd = with_meta(message_chunk(""),
                                    {"trace": {"type": ev["type"],
                                               "data": {"sid": session_id, **ev["data"]}}})
                    asyncio.run_coroutine_threadsafe(
                        self._conn.session_update(session_id, upd), loop).result()

                emitter = RelayEmitter("/dev/null", clock=lambda: 0.0, relay=_relay)
            else:
                emitter = Emitter("/dev/null", clock=lambda: 0.0, console=False)
            cfg = dict(self._agent_cfg)
            # Clarify-before-acting, enforced where the model actually obeys: an
            # explain turn runs with an answer-only instance_template instead of
            # the engine's every-turn "solve this issue / edit the source" one.
            cfg["instance_template"] = _instance_template_for(
                task_type, cfg.get("instance_template", ""))
            agent = None  # bound before construction so the except can reference it
            try:
                # pass the CURRENT worker model so /models hot-swaps the agent path
                # too; the factory ignores the arg in mock mode.
                model_obj = self._model_factory(model_id if model_id is not None else self._worker_model_id)
                agent = TracingAgent(model_obj, env,
                                     emitter=emitter, skill_block=skill_block,
                                     persona_block=persona_block, memory_block=memory_block,
                                     base_block=base_block,
                                     # share the model's registry; None (mock) => agent default.
                                     registry=getattr(model_obj, "registry", None),
                                     # ESC checkpoint: the loop ends between steps when set.
                                     cancel_flag=state.cancel_flag,
                                     **cfg)
                agent_ref["agent"] = agent
                model = agent.model
                # mock model has no on_delta attr → bind nothing → mock mode unchanged.
                if hasattr(model, "on_delta"):
                    model.on_delta = emit_delta
                try:
                    result = agent.run(text, prior=prior)
                    return {"stop_reason": "end_turn",
                            "exit_status": result.get("exit_status", "end_turn"),
                            "assistant": flatten_agent_messages(agent.messages),
                            "streamed": streamed["buf"]}
                finally:
                    # never marshal a delta to a dead loop after the turn ends.
                    if hasattr(model, "on_delta"):
                        model.on_delta = None
            except Exception:  # engine/construction failure → refusal; capture any prose
                # The refusal otherwise hides WHY the turn died (bad model id,
                # litellm/network error, engine crash). Log the traceback — this
                # runs on the worker thread, so logger.exception is the right sink.
                logger.exception("agent engine failed (model=%r, persona=%r)",
                                 self._worker_model_id, self._persona_key())
                return {"stop_reason": "refusal", "exit_status": "refusal",
                        "assistant": flatten_agent_messages(getattr(agent, "messages", [])),
                        "streamed": streamed["buf"]}

        if state.cancel_flag.is_set():
            return {"stop_reason": "cancelled", "exit_status": "cancelled", "assistant": ""}
        engine = await loop.run_in_executor(None, run_engine)
        if state.cancel_flag.is_set():
            return {"stop_reason": "cancelled", "exit_status": "cancelled", "assistant": ""}
        return engine


def build_harness_agent(*, model_factory, agent_cfg, skills_dir: list[Path],
                        router: Router, worker_model_id=None,
                        workspace_dir: Path | None = None,
                        debug: bool = False) -> HarnessAgent:
    """Factory: wire the agent from resolved dependencies."""
    return HarnessAgent(
        model_factory=model_factory,
        agent_cfg=agent_cfg,
        skills_dir=skills_dir,
        router=router,
        worker_model_id=worker_model_id,
        workspace_dir=workspace_dir,
        debug=debug,
    )
