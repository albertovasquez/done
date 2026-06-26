"""HarnessTui: a single-session Textual ACP client. Launches the harness agent
as a subprocess via spawn_agent_process, runs the connection on Textual's own
asyncio loop, and renders the session/update stream (messages, tool calls, and
the harness _meta chips) into a RichLog. Permission requests surface as a modal
whose button resolves the Future the TuiClient awaits.

RichLog.write() only appends (no line handle), so tool status is append-only:
ToolCallStart writes the "$ cmd" line; ToolCallProgress appends a follow-up
status line."""

from __future__ import annotations

from typing import Any

import acp
from acp.schema import ClientCapabilities, ElicitationCapabilities
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, RichLog

from trace.tui.client import TuiClient
from trace.tui.messages import SessionUpdate, PermissionRequest
from trace.tui.render import render_update, harness_chips, status_style

_GLYPH = {"completed": "✓", "failed": "✗"}


class PermissionModal(ModalScreen):
    """Renders ALL acp-provided options generically + a Reject path. Dismisses
    with the chosen option_id (str) or None (reject)."""

    def __init__(self, options, tool_call) -> None:
        super().__init__()
        self._options = options or []
        self._tool_call = tool_call

    def compose(self) -> ComposeResult:
        cmd = getattr(self._tool_call, "tool_call_id", "") or "permission requested"
        with Vertical(id="box"):
            yield Label(f"$ {cmd}", id="cmd")
            for opt in self._options:
                yield Button(getattr(opt, "name", "Allow"),
                             id=f"opt-{getattr(opt, 'option_id', 'allow')}")
            yield Button("Reject", id="opt-__reject__", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        oid = event.button.id[len("opt-"):]
        self.dismiss(None if oid == "__reject__" else oid)


class HarnessTui(App):
    CSS_PATH = "app.tcss"  # relative to this module's dir (trace/tui/)
    BINDINGS = [("escape", "cancel", "Cancel turn")]

    def __init__(self, agent_cmd: list[str], cwd: str, model: str) -> None:
        super().__init__()
        self.agent_cmd = agent_cmd
        self.cwd = cwd
        self.model = model
        self._client = TuiClient(self)
        self._conn = None
        self._cm = None                       # the spawn_agent_process context manager
        self._session_id = None
        self._pending_perm = None             # the in-flight permission Future, if any

    def compose(self) -> ComposeResult:
        yield RichLog(id="transcript", highlight=False, markup=True, wrap=True)
        yield Input(placeholder="Type a prompt…", id="prompt")

    @property
    def _transcript(self) -> RichLog:
        return self.query_one("#transcript", RichLog)

    async def on_mount(self) -> None:
        log = self._transcript
        try:
            self._cm = acp.spawn_agent_process(self._client, self.agent_cmd[0],
                                               *self.agent_cmd[1:])
            self._conn, _proc = await self._cm.__aenter__()
            await self._conn.initialize(
                protocol_version=acp.PROTOCOL_VERSION,
                client_capabilities=ClientCapabilities(elicitation=ElicitationCapabilities()),
            )
            new = await self._conn.new_session(cwd=self.cwd, mcp_servers=[])
            self._session_id = new.session_id
            log.write(f"[dim]harness · {self.cwd} · model={self.model}[/dim]")
        except Exception as e:                # startup failure is fatal but must not crash the UI
            log.write(f"[red]could not start agent: {e}[/red]")
            self.query_one("#prompt", Input).disabled = True

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text or self._conn is None:
            return
        self._transcript.write(f"[bold]you:[/bold] {text}")
        prompt = self.query_one("#prompt", Input)
        prompt.value = ""
        prompt.disabled = True
        self.run_worker(self._send_prompt(text), thread=False)

    async def _send_prompt(self, text: str) -> None:
        log = self._transcript
        try:
            resp = await self._conn.prompt(
                prompt=[acp.text_block(text)], session_id=self._session_id)
            if getattr(resp, "stop_reason", "end_turn") != "end_turn":
                log.write(f"[dim]— turn ended: {resp.stop_reason} —[/dim]")
        except Exception as e:
            log.write(f"[red]agent disconnected — restart to continue ({e})[/red]")
        finally:
            self.query_one("#prompt", Input).disabled = False

    def on_session_update(self, msg: SessionUpdate) -> None:
        log = self._transcript
        for chip in harness_chips(getattr(msg.update, "field_meta", None)):
            log.write(f"[dim]\\[{chip}][/dim]")
        item = render_update(msg.update)
        if item is None:
            return
        if item.kind == "message":
            if item.text:
                log.write(f"[bold]agent:[/bold] {item.text}")
        elif item.kind == "thought":
            if item.text:
                log.write(f"[dim italic]{item.text}[/dim italic]")
        elif item.kind == "user":
            if item.text:
                log.write(f"[bold]you:[/bold] {item.text}")
        elif item.kind == "tool":
            color = status_style(item.status)
            log.write(f"[{color}]{item.title}[/{color}]")
        elif item.kind == "tool_update":
            color = status_style(item.status)
            glyph = _GLYPH.get(item.status, "")
            line = f"  [{color}]→ {item.status} {glyph}[/{color}]"
            if item.body:
                line += f"  {item.body.splitlines()[0][:120]}"
            log.write(line)

    def on_permission_request(self, msg: PermissionRequest) -> None:
        self._pending_perm = msg.future

        def _resolve(chosen) -> None:
            self._pending_perm = None
            if not msg.future.done():
                msg.future.set_result(chosen)

        self.push_screen(PermissionModal(msg.options, msg.tool_call), _resolve)

    async def action_cancel(self) -> None:
        if self._conn is not None and self._session_id is not None:
            await self._conn.cancel(session_id=self._session_id)

    async def on_unmount(self) -> None:
        # resolve any dangling permission Future to reject, then tear down the subprocess
        if self._pending_perm is not None and not self._pending_perm.done():
            self._pending_perm.set_result(None)
        if self._cm is not None:
            try:
                await self._cm.__aexit__(None, None, None)
            except Exception:
                pass
