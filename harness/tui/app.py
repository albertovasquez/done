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

The transcript is a VerticalScroll of widgets (not an append-only RichLog): each
streamed agent answer is a live Markdown widget that accumulates deltas and is
.update()-ed per token, so answers render formatted AND stream as they arrive.
Discrete items (user message, chips, tool calls, meta) are themed Static lines.
A LoadingIndicator shows while the model is working (between send and first token)."""

from __future__ import annotations

import os
import time
from typing import Any

import acp
from acp.schema import ClientCapabilities, ElicitationCapabilities
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.widgets import LoadingIndicator, Markdown, Static, TextArea

from harness.tui.client import TuiClient
from harness.tui.commands import build_registry, resolve_command
from harness.tui.messages import SessionUpdate, PermissionRequest
from harness.tui.render import render_update, harness_chips, status_style
from harness.tui.theme import HARNESS_THEME, COLORS, STATUS_COLOR
from harness.tui.widgets.permission_modal import PermissionModal
from harness.tui.widgets.select_modal import SelectModal, SelectOption
from harness.tui.widgets.slash_menu import SlashMenu
from harness.tui.widgets.prompt_area import PromptArea
from harness.tui.header import icon_markup, header_text_markup

_GLYPH = {"completed": "✓", "failed": "✗"}
_MODE = "Build"                       # the single agent "mode" we expose for now


def _c(name: str, text: str) -> str:
    """Wrap text in a hex color for RichLog markup (Rich, not Textual CSS)."""
    return f"[{COLORS.get(name, COLORS['foreground'])}]{text}[/]"


def _provider_label(model: str) -> str:
    return "Vibeproxy" if model == "vibeproxy" else "Mock"


def _model_label(model: str, worker_model_id: str | None) -> str:
    # Real worker model id when known (vibeproxy); a friendly label otherwise.
    if worker_model_id:
        return worker_model_id
    if model == "mock":
        return "mock model"
    # vibeproxy with no model chosen yet — avoid the redundant "vibeproxy Vibeproxy".
    return "default model"


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
        self._gen = 0                         # session generation; bumped each _connect
        self._send_gen = 0                    # generation a prompt worker was launched in
        self._busy = False                    # lifecycle guard (reload/clear/model)
        self._reexec = False                  # /reload requests a full-process re-exec
        self._launch_worker_model_id = worker_model_id  # source of truth for "user switched model?"
        self._pending_perm = None             # the in-flight permission Future, if any
        self._started = False                 # have we left the landing state?
        self._turn_start = 0.0                # monotonic at send, for elapsed meta
        self._streaming_md = None             # the live Markdown widget for the current answer, else None
        self._stream_buf = ""                 # accumulated text for _streaming_md
        self._stream_closed = True            # True => the next message delta starts a fresh widget
        self._boundary_after = False          # True => an in-turn boundary (tool/thought/stream_reset) closed the block; next prose opens a FRESH widget (vs. a late delta of the prior answer, which extends in place)
        self._tokens = 0                      # last-known token count from usage updates
        self._commands = build_registry()     # slash-command registry
        self._slash = None                    # the SlashMenu widget while open, else None
        self._slash_overlay = None            # landing-only overlay box wrapping the menu
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
                with Horizontal(id="landing-header"):
                    yield Static(self._header_markup(), id="header-text", markup=True)
                with Vertical(id="landing-compose", classes="compose"):
                    yield PromptArea(placeholder='Ask anything... "What is the tech stack of this project?"',
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

    def _header_markup(self) -> str:
        """Build the landing header text (name + tagline). The mode·model line is
        shown on the compose-meta line under the input, not repeated here."""
        return header_text_markup("≡", self._version, "Get Shit Done")

    def _status_bar(self) -> ComposeResult:
        bar = Container(id="statusbar")
        return bar

    # ---- lifecycle ----

    async def _new_session(self) -> None:
        new = await self._conn.new_session(cwd=self.cwd, mcp_servers=[])
        self._session_id = new.session_id

    async def _connect(self) -> None:
        """Spawn the agent subprocess, initialize, open a session, re-apply the
        preserved model, and bump the generation. Failure-atomic: if anything
        after __aenter__ raises, tear the half-open context down before re-raising."""
        self._cm = acp.spawn_agent_process(
            self._client, self.agent_cmd[0], *self.agent_cmd[1:],
            env=dict(os.environ), cwd=self.cwd,
        )
        self._conn, _proc = await self._cm.__aenter__()
        try:
            await self._conn.initialize(
                protocol_version=acp.PROTOCOL_VERSION,
                client_capabilities=ClientCapabilities(elicitation=ElicitationCapabilities()),
            )
            await self._new_session()
            await self._reapply_model()
        except Exception:
            await self._teardown()            # never leave a half-open _cm
            raise
        self._gen += 1

    async def _teardown(self) -> None:
        """Close the subprocess context (terminates the child). Clears connection
        state in finally so a raising __aexit__ can't leave a stale _conn."""
        try:
            if self._cm is not None:
                await self._cm.__aexit__(None, None, None)
        finally:
            self._cm = self._conn = self._session_id = None

    async def _reapply_model(self) -> None:
        """After a respawn, re-apply a runtime-selected model. No-op if unchanged
        from launch; swallow method-not-found for agents without the extension."""
        if (self._worker_model_id is not None
                and self._worker_model_id != self._launch_worker_model_id):
            try:
                await self._conn.ext_method("harness/set_model", {"model": self._worker_model_id})
            except Exception:
                pass

    async def on_mount(self) -> None:
        # theme is registered + activated in __init__ (before CSS parse)
        # populate the status bar (left: path:branch, right: version)
        await self._mount_status_contents()
        self.query_one("#landing-input", PromptArea).focus()
        try:
            await self._connect()
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
            self._append_line(_c("error", message))
        else:
            # the header-text Static is rendered with Textual markup → $error resolves
            self.query_one("#header-text", Static).update(f"[$error]{message}[/]")

    # ---- input handling (works in both states; id differs) ----

    def _active_input(self) -> PromptArea:
        return self.query_one("#conversation-input" if self._started else "#landing-input", PromptArea)

    async def on_prompt_area_submitted(self, event: PromptArea.Submitted) -> None:
        text = event.text.strip()
        # slash command: run the highlighted command (or the typed one) instead of
        # sending a prompt.
        if text.startswith("/"):
            await self._run_slash(text)
            return
        if not text or self._conn is None or self._busy:
            return
        if not self._started:
            await self._enter_conversation()
        self._add_user_message(text)
        inp = self._active_input()
        inp.value = ""
        inp.disabled = True
        self._turn_start = time.monotonic()
        self._send_gen = self._gen            # tag this turn's worker with its generation
        self.run_worker(self._send_prompt(text), thread=False)

    # ---- slash menu ----

    async def on_text_area_changed(self, event: TextArea.Changed) -> None:
        # show/hide/filter the slash menu as '/' text changes in the active input
        value = event.text_area.text
        if value.startswith("/"):
            await self._open_or_update_slash(value[1:])
        elif self._slash is not None:
            await self._close_slash()

    async def _open_or_update_slash(self, query: str) -> None:
        if self._slash is None:
            self._slash = SlashMenu(self._commands)
            if self._started:
                # conversation: composer is docked to the bottom, so mounting the
                # menu in-flow directly above it already grows upward.
                await self.mount(self._slash, before="#composer")
            else:
                # landing: the compose box is vertically centered, so an in-flow
                # mount would push it down. Float the menu inside an overlay box
                # whose height we pin to the input's top row; the menu docks to that
                # box's bottom and grows UPWARD from the input — no offset math, so
                # nothing races as the row count changes while filtering.
                inp = self.query_one("#landing-input", PromptArea)
                self._slash_overlay = Container(self._slash, id="slash-overlay")
                self._slash_overlay.styles.height = inp.region.y
                await self.mount(self._slash_overlay)
        self._slash.update_query(query)

    async def on_resize(self, event) -> None:
        # the floating menu's height is pinned to the input's top row at open time;
        # a resize moves the centered input, detaching the menu. The menu is a
        # transient in-progress element, so just close it — the next keystroke
        # reopens it correctly anchored to the input's new row.
        if self._slash_overlay is not None:
            self._active_input().value = ""
            await self._close_slash()

    async def _close_slash(self) -> None:
        if self._slash is not None:
            # remove the overlay wrapper if we floated it (landing); else the menu
            target = self._slash_overlay if self._slash_overlay is not None else self._slash
            await target.remove()
            self._slash = None
            self._slash_overlay = None

    async def on_key(self, event) -> None:
        # while the slash menu is open, ↑/↓ move the selection; esc closes it
        if self._slash is None:
            # menu closed: esc with text in the box clears it; empty box falls
            # through to action_cancel (the global "Cancel turn" binding).
            if event.key == "escape" and self._active_input().value:
                self._active_input().value = ""
                event.stop()
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
            cmd = resolve_command(self._commands, name)   # canonical name or exact alias
        self._active_input().value = ""
        await self._close_slash()
        if cmd is None:
            self._notify_line(f"unknown command: {text}")
            return
        await cmd.handler(self)

    def _notify_line(self, message: str) -> None:
        """Show a one-off informational line (in transcript if started, else in
        the landing header's text column, leaving the icon in place)."""
        if self._started:
            self._append_line(_c("muted", message))
        else:
            self.query_one("#header-text", Static).update(f"[$muted]{message}[/]")

    # ---- commands: /models, /help, /exit, /quit ----

    async def action_select_model(self) -> None:
        if self._busy:
            return            # lifecycle guard (§6): no model picker mid-reload
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

    def _refresh_meta_line(self) -> None:
        # update the landing model lines (compose meta + header) in place, so a
        # model switch re-renders them rather than clobbering the header.
        label = _model_label(self.model, self._worker_model_id)
        provider = _provider_label(self.model)
        try:
            self.query_one(".compose-meta", Static).update(
                self._compose_meta_markup(label, provider))
        except Exception:
            pass
        try:
            self.query_one("#header-text", Static).update(self._header_markup())
        except Exception:
            pass

    def show_help(self) -> None:
        lines = ["[b]commands[/b]"]
        for c in self._commands:
            lines.append(f"  [$accent]/{c.name}[/]  [$muted]{c.description}[/]")
        msg = "\n".join(lines)
        if self._started:
            for ln in lines:
                self._append_line(ln)
        else:
            self.query_one("#header-text", Static).update(msg)

    async def _enter_conversation(self) -> None:
        """Tear down the landing view, build the transcript + bottom composer."""
        self._started = True
        await self.query_one("#landing", Container).remove()
        await self.mount(VerticalScroll(id="transcript"), before="#statusbar")
        composer = Vertical(id="composer", classes="compose")
        await self.mount(composer, before="#statusbar")
        await composer.mount(PromptArea(placeholder="Reply…", id="conversation-input"))
        self._refresh_status()
        self.query_one("#conversation-input", PromptArea).focus()

    async def _reset_conversation(self) -> None:
        """Empty the transcript and reset per-conversation state WITHOUT leaving
        the conversation view (flipping _started=False would query the removed
        #landing-input/#header-text and crash). No-op before the first prompt."""
        if self._started:
            await self._transcript.remove_children()
        self._streaming_md = None
        self._stream_buf = ""
        self._stream_closed = True
        self._boundary_after = False
        self._tokens = 0
        self._refresh_status()

    @property
    def _transcript(self) -> VerticalScroll:
        return self.query_one("#transcript", VerticalScroll)

    def _append(self, widget) -> None:
        """Mount a widget into the transcript and keep the view pinned to the end."""
        self._transcript.mount(widget)
        self._transcript.scroll_end(animate=False)

    def _append_line(self, markup: str) -> None:
        """Append a discrete themed line (chips, user msg, tool calls, meta, errors)."""
        self._append(Static(markup, markup=True))

    # ---- "model is working" indicator ----

    def _show_working(self) -> None:
        if self._transcript.query("#working"):
            return                                  # idempotent
        self._append(LoadingIndicator(id="working"))

    def _hide_working(self) -> None:
        for ind in self._transcript.query("#working"):
            ind.remove()

    def _end_stream(self, *, boundary: bool = False) -> None:
        """Close the current live Markdown block: the NEXT message delta starts a
        fresh widget. The widget reference is KEPT (not nulled) so that a late
        delta belonging to the just-closed answer (notification-delivery can lag
        prompt() returning) still appends to ITS block, in place — rather than
        spawning a stray block under the next user prompt. Called when a tool call
        or thought interleaves, and when a new user turn begins.

        `boundary=True` marks an IN-TURN step boundary (tool call, thought, or an
        explicit stream_reset): the agent is still producing this turn, so the
        next prose is a genuinely NEW step that must open its own widget. The
        default (`boundary=False`) is the turn-end / new-user-turn close, after
        which a trailing late delta of the just-closed answer extends it in
        place. `_stream_message` keys on `_boundary_after` to tell the two
        apart."""
        self._stream_closed = True
        if boundary:
            self._boundary_after = True

    def _add_user_message(self, text: str) -> None:
        # A new user turn: close the prior answer's stream first so its widget is
        # finalized and any late delta lands in it, not under this message. This
        # is NOT an in-turn boundary — clear _boundary_after so a trailing late
        # delta of the prior answer extends its widget rather than opening a new
        # block under this prompt.
        self._end_stream()
        self._boundary_after = False
        # accent bar glyph + bold text (the bordered-box look, inline).
        self._append_line(f"{_c('accent', '▌')} [b]{self._escape(text)}[/b]")

    @staticmethod
    def _escape(s: str) -> str:
        return s.replace("[", "\\[")

    async def _send_prompt(self, text: str) -> None:
        # The prior answer's stream was already closed by _add_user_message (which
        # runs before this on a new turn). Closing keeps the widget reference so a
        # late delta from the prior answer extends ITS block in place rather than
        # spawning a stray block under this prompt (see _stream_message).
        gen = self._send_gen                      # this turn belongs to this generation
        self._show_working()                      # spinner until the first token
        try:
            resp = await self._conn.prompt(
                prompt=[acp.text_block(text)], session_id=self._session_id)
            elapsed = time.monotonic() - self._turn_start
            self._write_meta(elapsed)
            if getattr(resp, "stop_reason", "end_turn") != "end_turn":
                self._append_line(_c("muted", f"— turn ended: {resp.stop_reason} —"))
        except Exception as e:
            self._append_line(_c("error", f"agent disconnected — restart to continue ({e})"))
        finally:
            if gen == self._gen:                  # only the CURRENT generation touches the UI
                self._hide_working()
                self._active_input().disabled = False
                self._active_input().focus()

    def _write_meta(self, elapsed: float) -> None:
        model_label = _model_label(self.model, self._worker_model_id)
        self._append_line(
            f"{_c('accent', '▣ ' + _MODE)} {_c('muted', f'· {model_label} · {elapsed:.1f}s')}")
        self._refresh_status()

    # ---- streaming session updates → themed transcript ----

    def _stream_message(self, text: str) -> None:
        """Accumulate an agent message delta into a single live Markdown widget.

        Routing distinguishes three cases for a delta that arrives after the
        stream was closed:
          - a NEW answer (its first delta) opens a fresh widget at the bottom;
          - a NEW agent STEP within the same turn (after a tool call / thought /
            explicit stream_reset) opens its own fresh widget — so multi-step
            narration does not merge into the previous step's block;
          - a LATE delta for the just-finished answer (notification lag, after a
            NEW USER turn began) extends that prior widget in place — never a
            stray block under the next prompt.
        The new-step and late-delta cases have IDENTICAL positional signals
        (prior widget closed and no longer last), so position alone cannot
        separate them. We use the `_boundary_after` flag instead: set by
        `_end_stream(boundary=True)` on an in-turn boundary, cleared by
        `_add_user_message` (a new user turn) and `_reset_conversation`. Flag set
        ⇒ new step (fresh widget); flag clear with a closed prior ⇒ late delta
        (extend in place).

        Markdown.update() is a no-op until the widget is mounted, so the render is
        scheduled via call_after_refresh — by the next refresh the mount has
        completed and the accumulated buffer renders."""
        kids = list(self._transcript.children)
        prior_is_last = self._streaming_md is not None and kids and kids[-1] is self._streaming_md
        # An IN-TURN boundary (tool line / thought / explicit stream_reset) closed
        # the prior block while the agent keeps producing this turn, so the next
        # prose is a genuinely NEW step that must open its own widget — NOT a late
        # delta of the just-closed answer. `_boundary_after` is set by
        # _end_stream(boundary=True) and cleared by _add_user_message (a new user
        # turn is the late-delta case, where the prior widget extends in place).
        boundary_after = self._boundary_after and self._streaming_md is not None

        if self._stream_closed and self._streaming_md is not None \
                and not prior_is_last and not boundary_after:
            # late delta for the just-closed answer → extend its widget in place;
            # the stream stays CLOSED (this delta does not begin a new answer).
            pass
        elif self._streaming_md is None or self._stream_closed:
            # new answer / new in-turn step → fresh widget at the bottom; stream
            # is now OPEN and the boundary has been consumed.
            self._hide_working()
            self._streaming_md = Markdown("")
            self._append(self._streaming_md)
            self._stream_buf = ""
            self._stream_closed = False
            self._boundary_after = False
        # else: stream already open → keep extending it.
        self._stream_buf += text
        md, buf = self._streaming_md, self._stream_buf
        self.call_after_refresh(md.update, buf)
        self._transcript.scroll_end(animate=False)

    def on_session_update(self, msg: SessionUpdate) -> None:
        if not self._started:
            return  # updates before first send (shouldn't happen) are ignored
        # drop updates from a reloaded-away session: the generation tag is the
        # load-bearing filter (stamped at post time); a gen-less message falls
        # through to the session_id check (defense-in-depth).
        if msg.gen is not None and msg.gen != self._gen:
            return
        if msg.session_id is not None and self._session_id is not None \
                and msg.session_id != self._session_id:
            return
        # token usage, if the agent surfaced any under _meta
        self._maybe_update_tokens(getattr(msg.update, "field_meta", None))
        # an explicit per-step boundary signal: Task 4 emits an empty message_chunk
        # carrying _meta stream_reset (nested under "harness" by with_meta()). Close
        # the current block as an IN-TURN boundary so the next prose opens a fresh
        # widget, then return early — the empty chunk must NOT render a blank line.
        meta = getattr(msg.update, "field_meta", None)
        if isinstance(meta, dict) and (meta.get("harness") or {}).get("stream_reset"):
            self._end_stream(boundary=True)
            return
        for chip in harness_chips(getattr(msg.update, "field_meta", None)):
            self._append_line(_c("muted", f"\\[{chip}]"))
        item = render_update(msg.update)
        if item is None:
            return
        if item.kind == "message":
            if item.text:
                self._stream_message(item.text)
        elif item.kind == "thought":
            if item.text:
                self._end_stream(boundary=True)  # a thought ends the current step's block
                self._append_line(f"[{COLORS['muted']} italic]{self._escape(item.text)}[/]")
        elif item.kind == "user":
            if item.text:
                self._append_line(f"{_c('accent', '▌')} [b]{self._escape(item.text)}[/b]")
        elif item.kind == "tool":
            self._end_stream(boundary=True)  # a tool call finalizes the current step's block
            color = self._status_hex(item.status)
            self._append_line(f"[{color}]{self._escape(item.title)}[/]")
        elif item.kind == "tool_update":
            color = self._status_hex(item.status)
            glyph = _GLYPH.get(item.status, "")
            line = f"  [{color}]→ {item.status} {glyph}[/]"
            if item.body:
                line += f"  {self._escape(item.body.splitlines()[0][:120])}"
            self._append_line(line)

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

        # The agent sends the real command in tool_call.title (e.g. "$ sed ...");
        # strip a leading "$ " so the modal doesn't double it.
        title = getattr(msg.tool_call, "title", "") or ""
        command = title[2:] if title.startswith("$ ") else title
        self.push_screen(PermissionModal(command, msg.options), _resolve)

    async def action_cancel(self) -> None:
        if self._conn is not None and self._session_id is not None:
            await self._conn.cancel(session_id=self._session_id)

    async def action_clear(self) -> None:
        if self._busy:
            return
        self._busy = True
        try:
            self._cancel_inflight()
            await self._reset_conversation()
            if self._started:                     # no transcript on the landing screen
                self._append_line(_c("muted", "— clearing… —"))
            await self._teardown()
            try:
                await self._connect()
                await self._reset_conversation()  # success → wipe the transient line
            except Exception as e:
                self._fatal(f"clear failed: {e}")
        finally:
            self._busy = False

    def _cancel_inflight(self) -> None:
        """Cancel any running prompt/model worker and resolve a pending permission
        future (the subprocess about to die will never answer it)."""
        self.workers.cancel_all()
        if self._pending_perm is not None and not self._pending_perm.done():
            self._pending_perm.set_result(None)
            self._pending_perm = None
        if isinstance(self.screen, PermissionModal):
            self.pop_screen()

    async def action_reload(self) -> None:
        if self._busy:
            return
        self._busy = True                         # never released; the process is replaced
        self._reexec = True                       # main() re-execs after run() returns
        self.exit()                               # Textual restores the terminal; run() returns

    async def on_unmount(self) -> None:
        if self._pending_perm is not None and not self._pending_perm.done():
            self._pending_perm.set_result(None)
        if self._cm is not None:
            try:
                await self._cm.__aexit__(None, None, None)
            except Exception:
                pass
