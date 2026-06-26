"""HarnessTui: a single-session Textual ACP client with an opencode-style chat
UI. Two visual states on one App (low-risk: connection + message routing stay on
the App, only the view swaps):

  LANDING       centered wordmark + compose box (first prompt)
  CONVERSATION  scrolling transcript + bottom compose, after the first send

It launches the harness agent as a subprocess (spawn_agent_process), runs the ACP
connection on Textual's own asyncio loop, and renders the session/update stream —
agent messages, tool calls, and the harness _meta chips — plus a per-turn meta
line (mode · model · elapsed). Permission requests surface as a modal whose button
resolves the Future the TuiClient awaits.

RichLog.write() only appends (no line handle), so tool status is append-only."""

from __future__ import annotations

import os
import time
from typing import Any

import acp
from acp.schema import ClientCapabilities, ElicitationCapabilities
from textual.app import App, ComposeResult
from textual.containers import Container, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, RichLog, Static

from harness.tui.client import TuiClient
from harness.tui.commands import build_registry
from harness.tui.messages import SessionUpdate, PermissionRequest
from harness.tui.render import render_update, harness_chips, status_style
from harness.tui.theme import HARNESS_THEME, COLORS, STATUS_COLOR
from harness.tui.widgets.select_modal import SelectModal, SelectOption
from harness.tui.widgets.slash_menu import SlashMenu
from harness.tui.logo import logo_markup

_GLYPH = {"completed": "✓", "failed": "✗"}
_MODE = "Build"                       # the single agent "mode" we expose for now


def _c(name: str, text: str) -> str:
    """Wrap text in a hex color for RichLog markup (Rich, not Textual CSS)."""
    return f"[{COLORS.get(name, COLORS['foreground'])}]{text}[/]"


def _provider_label(model: str) -> str:
    return "Vibeproxy" if model == "vibeproxy" else "Mock"


def _model_label(model: str, worker_model_id: str | None) -> str:
    # Real worker model id when known (vibeproxy); a friendly label for mock.
    if worker_model_id:
        return worker_model_id
    return "mock model" if model == "mock" else model


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
    CSS_PATH = "app.tcss"  # relative to this module's dir (harness/tui/)
    BINDINGS = [("escape", "cancel", "Cancel turn")]

    def __init__(self, agent_cmd: list[str], cwd: str, model: str,
                 worker_model_id: str | None = None, version: str = "0.5.0") -> None:
        super().__init__()
        self.agent_cmd = agent_cmd
        self.cwd = cwd
        self.model = model
        self._worker_model_id = worker_model_id
        self._version = version
        self._client = TuiClient(self)
        self._conn = None
        self._cm = None                       # the spawn_agent_process context manager
        self._session_id = None
        self._pending_perm = None             # the in-flight permission Future, if any
        self._started = False                 # have we left the landing state?
        self._turn_start = 0.0                # monotonic at send, for elapsed meta
        self._tokens = 0                      # last-known token count from usage updates
        self._commands = build_registry()     # slash-command registry
        self._slash = None                    # the SlashMenu widget while open, else None
        # Register + activate the theme BEFORE the stylesheet parses (CSS_PATH is
        # parsed at construction/mount; theme custom variables must exist by then).
        self.register_theme(HARNESS_THEME)
        self.theme = "harness"

    def get_theme_variable_defaults(self) -> dict[str, str]:
        # Make the theme's custom tokens ($muted, $code, …) resolvable in app.tcss
        # at parse time, independent of which theme is active. Idiomatic Textual.
        return dict(HARNESS_THEME.variables)

    # ---- compose: start in LANDING, build CONVERSATION lazily on first send ----

    def compose(self) -> ComposeResult:
        model_label = _model_label(self.model, self._worker_model_id)
        provider = _provider_label(self.model)
        with Container(id="landing"):
            with Vertical(id="landing-col"):
                yield Static(logo_markup(), id="wordmark", markup=True)
                with Vertical(id="landing-compose", classes="compose"):
                    yield Input(placeholder='Ask anything... "What is the tech stack of this project?"',
                                id="landing-input")
                    yield Static(self._compose_meta_markup(model_label, provider),
                                 classes="compose-meta", markup=True)
                yield Static("[b]tab[/b] agents   [b]ctrl+p[/b] commands", id="hint", markup=True)
        yield self._status_bar()

    def _compose_meta_markup(self, model_label: str, provider: str) -> str:
        # mock mode: just "Build · mock model" (no redundant provider).
        # vibeproxy: "Build · <model> Vibeproxy".
        if self.model == "mock":
            return f"[$accent][b]{_MODE}[/b][/] · [$muted]{model_label}[/]"
        return (f"[$accent][b]{_MODE}[/b][/] · {model_label} "
                f"[$muted]{provider}[/]")

    def _status_bar(self) -> ComposeResult:
        bar = Container(id="statusbar")
        return bar

    # ---- lifecycle ----

    async def on_mount(self) -> None:
        # theme is registered + activated in __init__ (before CSS parse)
        # populate the status bar (left: path:branch, right: version)
        await self._mount_status_contents()
        self.query_one("#landing-input", Input).focus()
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
        except Exception as e:                # startup failure is fatal but must not crash the UI
            self._fatal(f"could not start agent: {e}")

    async def _mount_status_contents(self) -> None:
        bar = self.query_one("#statusbar", Container)
        await bar.mount(Static(self._status_left(), id="statusbar-left", markup=True))
        await bar.mount(Static(self._status_right(), id="statusbar-right", markup=True))

    def _status_left(self) -> str:
        return f"[$muted]{self.cwd}[/]"

    def _status_right(self) -> str:
        right = f"ctrl+p commands"
        if self._tokens:
            right = f"{self._fmt_tokens(self._tokens)}  {right}"
        if not self._started:
            right = self._version
        return f"[$muted]{right}[/]"

    @staticmethod
    def _fmt_tokens(n: int) -> str:
        return f"{n/1000:.1f}K" if n >= 1000 else str(n)

    def _refresh_status(self) -> None:
        try:
            self.query_one("#statusbar-right", Static).update(self._status_right())
        except Exception:
            pass

    def _fatal(self, message: str) -> None:
        # show an error and disable input wherever we currently are
        try:
            self._active_input().disabled = True
        except Exception:
            pass
        if self._started:
            self._transcript.write(_c("error", message))
        else:
            # the wordmark Static is rendered with Textual markup → $error resolves
            self.query_one("#wordmark", Static).update(f"[$error]{message}[/]")

    # ---- input handling (works in both states; id differs) ----

    def _active_input(self) -> Input:
        return self.query_one("#conversation-input" if self._started else "#landing-input", Input)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        # slash command: run the highlighted command (or the typed one) instead of
        # sending a prompt.
        if text.startswith("/"):
            await self._run_slash(text)
            return
        if not text or self._conn is None:
            return
        if not self._started:
            await self._enter_conversation()
        self._add_user_message(text)
        inp = self._active_input()
        inp.value = ""
        inp.disabled = True
        self._turn_start = time.monotonic()
        self.run_worker(self._send_prompt(text), thread=False)

    # ---- slash menu ----

    async def on_input_changed(self, event: Input.Changed) -> None:
        # show/hide/filter the slash menu as '/' text changes in the active input
        value = event.value
        if value.startswith("/"):
            await self._open_or_update_slash(value[1:])
        elif self._slash is not None:
            await self._close_slash()

    async def _open_or_update_slash(self, query: str) -> None:
        if self._slash is None:
            self._slash = SlashMenu(self._commands)
            # mount directly above the active compose box
            anchor = "#composer" if self._started else "#landing-compose"
            await self.mount(self._slash, before=anchor)
        self._slash.update_query(query)

    async def _close_slash(self) -> None:
        if self._slash is not None:
            await self._slash.remove()
            self._slash = None

    async def on_key(self, event) -> None:
        # while the slash menu is open, ↑/↓ move the selection; esc closes it
        if self._slash is None:
            return
        if event.key == "down":
            self._slash.move(1); event.stop()
        elif event.key == "up":
            self._slash.move(-1); event.stop()
        elif event.key == "escape":
            self._active_input().value = ""
            await self._close_slash(); event.stop()

    async def _run_slash(self, text: str) -> None:
        # prefer the highlighted menu command; else parse the typed name
        cmd = self._slash.highlighted_command() if self._slash is not None else None
        if cmd is None:
            name = text[1:].split()[0] if len(text) > 1 else ""
            cmd = next((c for c in self._commands if c.name == name), None)
        self._active_input().value = ""
        await self._close_slash()
        if cmd is None:
            self._notify_line(f"unknown command: {text}")
            return
        await cmd.handler(self)

    def _notify_line(self, message: str) -> None:
        """Show a one-off informational line (in transcript if started, else as a
        transient title under the wordmark)."""
        if self._started:
            self._transcript.write(_c("muted", message))
        else:
            self.query_one("#wordmark", Static).update(
                logo_markup() + "\n\n" + f"[$muted]{message}[/]")

    # ---- commands: /models, /help, /exit, /quit ----

    async def action_select_model(self) -> None:
        if self.model != "vibeproxy":
            self._notify_line("model selection requires launching with --model vibeproxy")
            return
        try:
            models = await self._fetch_models()
        except Exception as e:
            self._notify_line(f"could not fetch models: {e}")
            return
        if not models:
            self._notify_line("no models returned by the provider")
            return
        options = [SelectOption(id=m, label=self._pretty_model(m)) for m in models]
        current = self._worker_model_id

        def _picked(choice) -> None:
            if choice:
                self.run_worker(self._apply_model(choice), thread=False)

        self.push_screen(
            SelectModal(title="Select model", options=options, current=current,
                        footer="[$muted]↑↓ move · enter select · esc cancel[/]"),
            _picked,
        )

    async def _fetch_models(self) -> list[str]:
        import json, urllib.request
        base = os.getenv("VIBEPROXY_BASE_URL", "http://localhost:8317/v1")
        url = base.rstrip("/") + "/models"

        def _get() -> list[str]:
            with urllib.request.urlopen(url, timeout=5) as r:
                data = json.load(r)
            return sorted(m.get("id") for m in data.get("data", []) if m.get("id"))

        import asyncio as _asyncio
        return await _asyncio.get_running_loop().run_in_executor(None, _get)

    @staticmethod
    def _pretty_model(model_id: str) -> str:
        return model_id

    async def _apply_model(self, model_id: str) -> None:
        # hot-swap on the agent for subsequent turns (no restart)
        try:
            await self._conn.ext_method("harness/set_model", {"model": model_id})
        except Exception as e:
            self._notify_line(f"could not switch model: {e}")
            return
        self._worker_model_id = model_id
        self._refresh_meta_line()
        self._notify_line(f"model → {self._pretty_model(model_id)}")

    def _refresh_meta_line(self) -> None:
        # update the compose meta (landing) if still present
        label = _model_label(self.model, self._worker_model_id)
        provider = _provider_label(self.model)
        try:
            self.query_one(".compose-meta", Static).update(
                self._compose_meta_markup(label, provider))
        except Exception:
            pass

    def show_help(self) -> None:
        lines = ["[b]commands[/b]"]
        for c in self._commands:
            lines.append(f"  [$accent]/{c.name}[/]  [$muted]{c.description}[/]")
        msg = "\n".join(lines)
        if self._started:
            for ln in lines:
                self._transcript.write(ln)
        else:
            self.query_one("#wordmark", Static).update(logo_markup() + "\n\n" + msg)

    async def _enter_conversation(self) -> None:
        """Tear down the landing view, build the transcript + bottom composer."""
        self._started = True
        await self.query_one("#landing", Container).remove()
        await self.mount(RichLog(id="transcript", highlight=False, markup=True, wrap=True),
                         before="#statusbar")
        composer = Vertical(id="composer", classes="compose")
        await self.mount(composer, before="#statusbar")
        await composer.mount(Input(placeholder="Reply…", id="conversation-input"))
        self._refresh_status()
        self.query_one("#conversation-input", Input).focus()

    @property
    def _transcript(self) -> RichLog:
        return self.query_one("#transcript", RichLog)

    def _add_user_message(self, text: str) -> None:
        # RichLog can't host child widgets, so render the user message as a styled
        # line: an accent bar glyph + bold text (the bordered-box look, inline).
        self._transcript.write("")  # spacer
        self._transcript.write(f"{_c('accent', '▌')} [b]{self._escape(text)}[/b]")

    @staticmethod
    def _escape(s: str) -> str:
        return s.replace("[", "\\[")

    async def _send_prompt(self, text: str) -> None:
        log = self._transcript
        try:
            resp = await self._conn.prompt(
                prompt=[acp.text_block(text)], session_id=self._session_id)
            elapsed = time.monotonic() - self._turn_start
            self._write_meta(elapsed)
            if getattr(resp, "stop_reason", "end_turn") != "end_turn":
                log.write(_c("muted", f"— turn ended: {resp.stop_reason} —"))
        except Exception as e:
            log.write(_c("error", f"agent disconnected — restart to continue ({e})"))
        finally:
            self._active_input().disabled = False
            self._active_input().focus()

    def _write_meta(self, elapsed: float) -> None:
        model_label = _model_label(self.model, self._worker_model_id)
        self._transcript.write(
            f"{_c('accent', '▣ ' + _MODE)} {_c('muted', f'· {model_label} · {elapsed:.1f}s')}")
        self._refresh_status()

    # ---- streaming session updates → themed transcript ----

    def on_session_update(self, msg: SessionUpdate) -> None:
        if not self._started:
            return  # updates before first send (shouldn't happen) are ignored
        log = self._transcript
        # token usage, if the agent surfaced any under _meta
        self._maybe_update_tokens(getattr(msg.update, "field_meta", None))
        for chip in harness_chips(getattr(msg.update, "field_meta", None)):
            log.write(_c("muted", f"\\[{chip}]"))
        item = render_update(msg.update)
        if item is None:
            return
        if item.kind == "message":
            if item.text:
                log.write(_c("foreground", self._escape(item.text)))
        elif item.kind == "thought":
            if item.text:
                log.write(f"[{COLORS['muted']} italic]{self._escape(item.text)}[/]")
        elif item.kind == "user":
            if item.text:
                log.write(f"{_c('accent', '▌')} [b]{self._escape(item.text)}[/b]")
        elif item.kind == "tool":
            color = self._status_hex(item.status)
            log.write(f"[{color}]{self._escape(item.title)}[/]")
        elif item.kind == "tool_update":
            color = self._status_hex(item.status)
            glyph = _GLYPH.get(item.status, "")
            line = f"  [{color}]→ {item.status} {glyph}[/]"
            if item.body:
                line += f"  {self._escape(item.body.splitlines()[0][:120])}"
            log.write(line)

    @staticmethod
    def _status_hex(status: str) -> str:
        # render.status_style returns a color NAME (e.g. "green"); map to our hex.
        name = status_style(status)
        # status_style already returns a Rich-valid color name; STATUS_COLOR maps
        # the canonical statuses to theme hex. Prefer the theme hex when known.
        return STATUS_COLOR.get(status, name)

    def _maybe_update_tokens(self, field_meta) -> None:
        if not isinstance(field_meta, dict):
            return
        usage = (field_meta.get("harness") or {}).get("usage") if isinstance(
            field_meta.get("harness"), dict) else None
        if isinstance(usage, dict) and isinstance(usage.get("total"), int):
            self._tokens = usage["total"]
            self._refresh_status()

    # ---- permissions / cancel / teardown (unchanged plumbing) ----

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
        if self._pending_perm is not None and not self._pending_perm.done():
            self._pending_perm.set_result(None)
        if self._cm is not None:
            try:
                await self._cm.__aexit__(None, None, None)
            except Exception:
                pass
