"""HarnessAgent: the ACP agent. Per session/prompt turn: classify (Router) →
emit _meta → dispatch chat/ambiguous/agent. The agent loop runs on a worker
thread (via run_in_executor) with an AcpEnvironment whose callbacks marshal
session/update notifications back to the event loop. Router/ChatHandler also run
in the executor so the async loop stays responsive to session/cancel."""

from __future__ import annotations

import asyncio
from pathlib import Path

import acp
from acp.schema import (
    AllowedOutcome,
    PermissionOption,
    ToolCallUpdate,
)

from harness import skills
from harness.acp_emit import tool_call_start, tool_call_done, message_chunk, with_meta
from harness.acp_env import AcpEnvironment
from harness.acp_session import SessionStore
from harness.router import Router, Classification
from harness.chat_handler import ChatHandler
from harness.transcript import flatten_agent_messages


class HarnessAgent(acp.Agent):
    def __init__(self, *, model_factory, agent_cfg, skills_dir: list[Path], router: Router,
                 worker_model_id):
        self._model_factory = model_factory
        self._agent_cfg = agent_cfg
        self._skills_dir = skills_dir
        self._router = router
        self._worker_model_id = worker_model_id
        self._store = SessionStore()
        self._conn = None

    def on_connect(self, conn) -> None:
        self._conn = conn

    async def ext_method(self, method: str, params: dict) -> dict:
        """Harness-specific extension methods. `harness/set_model` hot-swaps the
        worker model for SUBSEQUENT turns without restarting the agent — both the
        chat path (ChatHandler) and the agent path (model factory) read
        self._worker_model_id fresh on each prompt."""
        if method == "harness/set_model":
            model = (params or {}).get("model")
            if model:
                self._worker_model_id = model
            return {"ok": True, "model": self._worker_model_id}
        return {}

    async def initialize(self, protocol_version, client_capabilities=None,
                         client_info=None, **kw):
        self._client_caps = client_capabilities
        return acp.InitializeResponse(
            protocol_version=acp.PROTOCOL_VERSION,
            agent_capabilities=acp.schema.AgentCapabilities(load_session=True),
        )

    async def new_session(self, cwd, additional_directories=None, mcp_servers=None, **kw):
        return acp.NewSessionResponse(session_id=self._store.new(cwd=cwd))

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
        text = "".join(getattr(b, "text", "") for b in prompt)
        transcript = state.transcript           # read once; every branch writes back per §6

        # 1) classify in the executor (sync litellm call must not block the loop)
        try:
            cls: Classification = await loop.run_in_executor(
                None, lambda: self._router.classify(text, history=transcript))
        except Exception as e:  # router/VibeProxy unreachable
            await self._conn.session_update(session_id,
                message_chunk(f"router unavailable: {e}"))
            return acp.PromptResponse(stop_reason="refusal")

        meta = {"task_type": cls.task_type, "skills": cls.skills,
                "confidence": cls.confidence}
        await self._conn.session_update(session_id,
            with_meta(message_chunk(""), {"task_classified": meta}))

        if cls.needs_clarification or cls.task_type == "ambiguous":
            q = cls.clarifying_question or "Could you clarify the task?"
            await self._conn.session_update(session_id, message_chunk(q))
            self._store.record(session_id, {"prompt": text, "stop_reason": "end_turn",
                                            "kind": "clarify"})
            # write only the user turn — the clarifying question is router
            # boilerplate, not model output, so it must not pollute later context.
            self._store.extend(session_id, [
                {"role": "user", "content": text, "origin": "clarify"}])
            return acp.PromptResponse(stop_reason="end_turn")

        if cls.task_type == "chat_question":
            handler = ChatHandler(self._worker_model_id)
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
            return acp.PromptResponse(stop_reason="end_turn")

        # agent path
        # offload skills.compose: it does filesystem I/O; keep the event loop free
        load = await loop.run_in_executor(None, skills.compose, self._skills_dir, cls.skills)
        await self._conn.session_update(session_id,
            with_meta(message_chunk(""),
                      {"skill_load": {"injected": load.injected, "skipped": load.skipped}}))
        engine = await self._run_agent_turn(loop, session_id, state, text, load.block, transcript)
        stop_reason = engine["stop_reason"]
        assistant = engine["assistant"] or engine["exit_status"] or stop_reason   # never empty
        self._store.record(session_id, {"prompt": text, "stop_reason": stop_reason,
                                        "kind": "agent"})
        self._store.extend(session_id, [
            {"role": "user", "content": text, "origin": "agent"},
            {"role": "assistant", "content": assistant, "origin": "agent"}])
        return acp.PromptResponse(stop_reason=stop_reason)

    async def _run_agent_turn(self, loop, session_id, state, text, skill_block, prior) -> dict:
        tc_counter = {"n": 0}

        def on_command(phase: str, command: str, out: dict | None) -> None:
            # runs on the worker thread → marshal to the loop and block until sent
            if phase == "start":
                tc_counter["n"] += 1
                state._last_tc_id = f"tc{tc_counter['n']}"          # transient, on the state obj
                upd = tool_call_start(state._last_tc_id, command)
            elif phase in ("done", "rejected"):
                result = out if out is not None else {"output": "permission denied",
                                                      "returncode": -1, "exception_info": ""}
                upd = tool_call_done(getattr(state, "_last_tc_id", "tc0"), result)
            else:
                return
            fut = asyncio.run_coroutine_threadsafe(
                self._conn.session_update(session_id, upd), loop)
            fut.result()

        def request_permission(command: str) -> bool:
            # Auto-allow (standalone path) unless the client advertised it can
            # handle permission prompts. ACP routes permission via elicitation;
            # gate on that rather than a bare None-check so a client that sends
            # capabilities without elicitation support isn't asked to answer a
            # prompt it can't service.
            if self._client_caps is None or getattr(self._client_caps, "elicitation", None) is None:
                return True
            tc_id = getattr(state, "_last_tc_id", "tc0")
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
                             client_terminal=client_terminal)

        def run_engine() -> dict:
            from harness.tracing_agent import TracingAgent
            from harness.events import Emitter
            emitter = Emitter("/dev/null", clock=lambda: 0.0, console=False)  # ACP carries the stream
            cfg = dict(self._agent_cfg)
            agent = None  # bound before construction so the except can reference it
            try:
                # pass the CURRENT worker model so /models hot-swaps the agent path
                # too; the factory ignores the arg in mock mode.
                agent = TracingAgent(self._model_factory(self._worker_model_id), env,
                                     emitter=emitter, skill_block=skill_block, **cfg)
                result = agent.run(text, prior=prior)
                return {"stop_reason": "end_turn",
                        "exit_status": result.get("exit_status", "end_turn"),
                        "assistant": flatten_agent_messages(agent.messages)}
            except Exception:  # engine/construction failure → refusal; capture any prose
                return {"stop_reason": "refusal", "exit_status": "refusal",
                        "assistant": flatten_agent_messages(getattr(agent, "messages", []))}

        if state.cancel_flag.is_set():
            return {"stop_reason": "cancelled", "exit_status": "cancelled", "assistant": ""}
        engine = await loop.run_in_executor(None, run_engine)
        if state.cancel_flag.is_set():
            return {"stop_reason": "cancelled", "exit_status": "cancelled", "assistant": ""}
        return engine


def build_harness_agent(*, model_factory, agent_cfg, skills_dir: list[Path],
                        router: Router, worker_model_id=None) -> HarnessAgent:
    """Factory: wire the agent from resolved dependencies."""
    return HarnessAgent(
        model_factory=model_factory,
        agent_cfg=agent_cfg,
        skills_dir=skills_dir,
        router=router,
        worker_model_id=worker_model_id,
    )
