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

import asyncio
import os
import time
from typing import Any

import acp
from acp.schema import ClientCapabilities, ElicitationCapabilities
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.widgets import LoadingIndicator, Markdown, Static, TextArea

from harness.tui.client import TuiClient
from harness.tui.commands import build_registry, resolve_command
from harness.tui.messages import SessionUpdate, PermissionRequest
from harness.tui.render import render_update, harness_chips, format_cwd
from harness.tui.state import (
    initial_snapshot, reduce, TurnStarted, TurnEnded, ItemReceived,
    TokensUpdated, DecisionOpened, decision_from_meta,
    PersonaResolved, persona_from_meta, AgentState,
    strip_done_sentinel_prose,
)
from harness.tui.theme import HARNESS_THEME, COLORS
from harness.tui.widgets.activity_region import ActivityRegion
from harness.tui.widgets.permission_modal import PermissionModal
from harness.tui.widgets.select_modal import SelectModal, SelectOption
from harness.tui.widgets.agent_rail import AgentRail, PersonaSelected
from harness.tui.widgets.cron_dashboard import CronDashboard, JobActionFailed
from harness.tui.widgets.cron_detail import CronDetail
from harness.tui.widgets.slash_menu import SlashMenu
from harness.tui.widgets.prompt_area import PromptArea
from harness.tui.widgets.decision_modal import DecisionModal, TYPE_SOMETHING, CHAT_ABOUT_IT
from harness.tui.widgets.status_chip import StatusChip
from harness.tui.header import icon_markup, header_text_markup
from harness import config as _config
from harness import hooks as _hooks
from harness.compress import auto_regen as _auto_regen  # noqa: F401 — import-time hook registration
from harness.compaction import resolve_ctx_window


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


def extract_agent_trace(tracer, update) -> None:
    """If `update` carries a relayed trace payload (field_meta['harness']['trace'],
    stamped by the agent's _trace / RelayEmitter), write it with source='agent'.
    No-op when the payload is absent or the tracer is a NullTracer."""
    meta = getattr(update, "field_meta", None)
    if isinstance(meta, dict):
        tr = (meta.get("harness") or {}).get("trace")
        if isinstance(tr, dict) and "type" in tr:
            tracer.emit("agent", tr["type"], **(tr.get("data") or {}))


class HarnessTui(App):
    CSS_PATH = "app.tcss"  # relative to this module's dir (harness/tui/)
    BINDINGS = [("escape", "cancel", "Cancel turn"),
                ("ctrl+o", "toggle_details", "Tool details"),
                ("ctrl+j", "toggle_cron", "Cron jobs")]

    def __init__(self, agent_cmd: list[str], cwd: str, model: str,
                 worker_model_id: str | None = None, version: str = "0.5.0",
                 yolo: bool = False, persona: str | None = None,
                 debug: bool = False) -> None:
        super().__init__()
        self.agent_cmd = agent_cmd
        self._debug = debug                   # --debug: write runs/<ts>/trace.jsonl
        self._tracer = None                   # opened lazily in _connect (Task 4)
        self.cwd = cwd
        self.model = model
        self._worker_model_id = worker_model_id
        self._version = version
        self._yolo = yolo                          # live gate (TUI mirror of the agent's)
        self._launch_persona = persona or "default"  # the persona id this process launched as
        # Read the pin for THIS process's persona (not always "default") — a process
        # launched with `--persona fred` shows fred's pin. (Also used to highlight the
        # launched persona in the rail before the first turn — see _current_persona.)
        self._yolo_pinned = _config.yolo_pinned(self._launch_persona)
        # Compress-aware mode: default ON; pinned value IS the launch default.
        self._compress_aware = _config.compress_aware_pinned(self._launch_persona)
        self._compress_aware_pinned = self._compress_aware
        self._client = TuiClient(self)
        self._conn = None
        self._cm = None                       # the spawn_agent_process context manager
        self._proc = None                     # the agent subprocess (for stderr drain)
        self._stderr_task = None              # background task draining the agent's stderr
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
        self._stream_dirty = False            # buffer changed since last paint
        self._stream_timer = None             # Textual Timer while a stream is open
        self._stream_closed = True            # True => the next message delta starts a fresh widget
        self._boundary_after = False          # True => an in-turn boundary (tool/thought/stream_reset) closed the block; next prose opens a FRESH widget (vs. a late delta of the prior answer, which extends in place)
        self._tokens = 0                      # last-known token count from usage updates
        self._compacted: dict | None = None   # context.compacted event for the current turn, if any
        self._persona_seen = False            # True after the first real PersonaResolved lands
        self._decision_open = False            # True while a DecisionModal is on the screen stack (guards double-push)
        self._turn_active = False             # True while a prompt turn is in flight (used by Esc-rail guard)
        self._cancel_posted = False           # True after the first action_cancel this turn (de-dupe double BINDING fire)
        self._queued: list[str] = []          # prompts typed mid-turn; drained FIFO when the turn ends
        self._pending_persona: str | None = None   # a switch requested mid-turn; applied on turn-end
        self._snapshot = initial_snapshot()   # the presentation model (pure, immutable)
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
                    yield PromptArea(placeholder=self._landing_placeholder(),
                                     id="landing-input")
                    yield Static(self._compose_meta_markup(model_label, provider),
                                 classes="compose-meta", markup=True)
                yield Static("[b]tab[/b] agents", id="hint", markup=True)
        yield self._status_bar()
        from harness.tui.widgets.quick_keys import QuickKeysPanel
        drawer = Vertical(AgentRail(id="agent-rail"), QuickKeysPanel(), id="agent-drawer")
        drawer.display = False            # the whole drawer (rail + legend) toggles as one
        yield drawer
        # Cron jobs drawer — mirrors #agent-drawer: roster + run-history chart,
        # hidden until ctrl+j. Empty job id until a job is selected.
        cron_drawer = Vertical(
            CronDashboard(id="cron-dashboard"),
            CronDetail("", id="cron-detail"),
            id="cron-drawer",
        )
        cron_drawer.display = False
        yield cron_drawer

    def _mode_label(self) -> str:
        """The label that replaced the old 'Build' mode word: the active persona's
        display name (bare, no parens). Shared by the compose-meta line and the
        per-turn run caption so the two stay in sync on a persona switch."""
        return self._persona_display_name(self._current_persona())

    def _landing_placeholder(self) -> str:
        """Empty-input placeholder for the landing compose box. On the default
        persona it keeps the original prompt + example; a non-default persona
        swaps to a personalized 'Ask <name> anything…' (no example). Both carry a
        leading › chevron."""
        pid = self._current_persona()
        if pid == "default":
            return '› Ask anything... "What is the tech stack of this project?"'
        return f"› Ask {self._persona_display_name(pid)} anything…"

    def _conversation_placeholder(self) -> str:
        """Idle placeholder for the conversation composer (between turns). Mirrors
        _landing_placeholder's persona awareness: default → a plain 'Reply…'; a
        non-default persona → '› Reply to <name>…' so the composer keeps its
        identity after a turn instead of snapping back to a generic prompt. The
        mid-turn queue hint ('Type to queue…') is separate and not persona-aware."""
        pid = self._current_persona()
        if pid == "default":
            return "Reply…"
        return f"› Reply to {self._persona_display_name(pid)}…"

    def _compose_meta_markup(self, model_label: str, provider: str) -> str:
        # Just the persona name now (replaced the 'Build' mode word). The
        # model·provider moved up under the header rule (see _header_markup);
        # bypass posture shows in the footer chip. model_label/provider are
        # accepted but unused — kept so the call site and tests stay stable.
        return f"[$accent][b]{self._mode_label()}[/b][/]"

    def _model_line(self) -> str:
        """The 'gpt-5.4 Vibeproxy' line shown under the header rule. Mirrors the
        old compose-meta logic: 'mock model' (no provider) in mock mode, else
        '<model> <Provider>'."""
        model_label = _model_label(self.model, self._worker_model_id)
        if self.model == "mock":
            return model_label
        return f"{model_label} {_provider_label(self.model)}"

    def _header_markup(self) -> str:
        """Build the landing header text (name + tagline + rule + model line).
        The model·provider sits directly under the rule; the compose-meta line
        below the input is just the mode word."""
        return header_text_markup("≡", self._version, "Get Shit Done",
                                  model_line=self._model_line())

    def _status_bar(self) -> ComposeResult:
        bar = Container(id="statusbar")
        return bar

    # ---- lifecycle ----

    async def _new_session(self) -> None:
        new = await self._conn.new_session(cwd=self.cwd, mcp_servers=[])
        self._session_id = new.session_id

    def _ensure_tracer(self) -> None:
        """Open the --debug trace file once per process (the TUI is the sole
        writer). Reconnects (reload/clear) reuse the same file. When debug is
        off, install a NullTracer so every call site is an unconditional no-op."""
        if self._tracer is not None:
            return
        from harness.debug_trace import make_tracer, NullTracer
        if self._debug:
            import os as _os
            import time as _time
            from harness import paths
            # pid suffix so two launches in the same second don't collide on one
            # run dir (Emitter opens mode "w" → the second would truncate the first).
            run_dir = paths.runs_dir() / f"{_time.strftime('%Y%m%d-%H%M%S')}-{_os.getpid()}"
            self._tracer = make_tracer(True, run_dir)
        else:
            self._tracer = NullTracer()

    async def _connect(self) -> None:
        """Spawn the agent subprocess, initialize, open a session, re-apply the
        preserved model, and bump the generation. Failure-atomic: if anything
        after __aenter__ raises, tear the half-open context down before re-raising."""
        self._ensure_tracer()
        self._cm = acp.spawn_agent_process(
            self._client, self.agent_cmd[0], *self.agent_cmd[1:],
            env=dict(os.environ), cwd=self.cwd,
        )
        self._conn, self._proc = await self._cm.__aenter__()
        # spawn_agent_process pipes the agent's stderr (transports.py stderr=PIPE)
        # but NOTHING reads it. litellm and friends write to stderr on a chat turn;
        # once the ~64KB pipe buffer fills, the agent BLOCKS on its next stderr
        # write — mid-turn, after streaming chat.done but before writing the prompt
        # RESPONSE frame — so our await prompt() never resolves ("Responding…"
        # sticks, composer locks). Continuously drain it so the buffer never fills.
        self._stderr_task = asyncio.create_task(self._drain_stderr(self._proc))
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

    async def _drain_stderr(self, proc) -> None:
        """Read the agent subprocess's stderr to EOF so its pipe buffer can never
        fill and block the agent mid-turn. Under --debug, relay each line to the
        trace; otherwise discard. Best-effort: any error just ends the drain."""
        stderr = getattr(proc, "stderr", None)
        if stderr is None:
            return
        try:
            while True:
                line = await stderr.readline()
                if not line:
                    break                              # EOF: agent exited
                if self._tracer is not None:
                    self._tracer.emit("agent", "stderr",
                                      text=line.decode("utf-8", "replace").rstrip("\n"))
        except asyncio.CancelledError:
            raise                                      # teardown cancelled us — propagate
        except Exception:
            return                                     # any read error just ends the drain

    async def _teardown(self) -> None:
        """Close the subprocess context (terminates the child). Clears connection
        state in finally so a raising __aexit__ can't leave a stale _conn."""
        if self._stderr_task is not None:
            self._stderr_task.cancel()
        try:
            if self._cm is not None:
                await self._cm.__aexit__(None, None, None)
        finally:
            self._cm = self._conn = self._proc = self._stderr_task = self._session_id = None

    async def _reapply_model(self) -> None:
        """After a respawn, re-apply a runtime-selected model. No-op if unchanged
        from launch; swallow method-not-found for agents without the extension."""
        if (self._worker_model_id is not None
                and self._worker_model_id != self._launch_worker_model_id):
            try:
                await self._conn.ext_method("harness/set_model", {"model": self._worker_model_id})
            except Exception as e:
                # Benign for an editor client without the harness extension
                # (method-not-found); but a real failure here means the TUI
                # silently runs the WRONG model after a respawn. self.log keeps it
                # off the user's screen while making it visible in `textual console`.
                self.log(f"reapply_model failed ({self._worker_model_id!r}): {e!r}")

    async def on_mount(self) -> None:
        # theme is registered + activated in __init__ (before CSS parse)
        # populate the status bar (left: path:branch, right: version)
        await self._mount_status_contents()
        self.query_one("#landing-input", PromptArea).focus()
        self.set_interval(0.25, self._tick_elapsed)
        try:
            await self._connect()
        except Exception as e:                # startup failure is fatal but must not crash the UI
            # _connect opened the tracer before spawning, so this lands in the
            # trace file too — a failed spawn/init is otherwise just a UI line.
            self.log(f"agent startup failed: {e!r}")
            if self._tracer is not None:
                self._tracer.emit("dn", "spawn.failed", error=str(e))
            self._fatal(f"could not start agent: {e}")

        # Ensure scheduled jobs can fire. PRIMARY path is the OS service (launchd/
        # systemd) installed via `dn cron install`; if present, the OS owns the
        # daemon and we do nothing. On first run we offer to install it; otherwise
        # we fall back to a best-effort detached spawn (survives window close but
        # not reboot). Never let any of this break boot.
        try:
            self._decide_cron_autostart(show_prompt=self._show_cron_install_prompt)
        except Exception as e:
            self.log(f"cron autostart skipped: {e!r}")
            if self._tracer is not None:
                self._tracer.emit("dn", "cron.autostart.failed", error=str(e))

        _hooks.dispatch("session_start", tracer=self._tracer,
                        cwd=self.cwd, persona_id=self._current_persona())

    def _decide_cron_autostart(self, *, show_prompt) -> str:
        """Decide how to ensure cron runs. Returns the branch taken (testable).

        1. OS service already installed → do nothing; the OS owns the lifecycle.
        2. First run on a supported platform → show the opt-in prompt (once) AND
           start a best-effort fallback daemon for this session. The modal governs
           the *durable* OS service; whatever the user clicks, jobs created this
           session still fire (the PID lock keeps a later OS-service daemon
           single-instance, so the temporary spawn is safe). (#165)
        3. Otherwise (declined before, or unsupported platform) → best-effort
           fallback spawn so jobs still fire while this window is open.
        """
        from harness.jobs import service, prompt_state
        from harness.jobs.supervisor import ensure_daemon_running

        if service.current_backend() != "unsupported":
            if service.service_status().state == "installed":
                return "os-service-present"
            if not prompt_state.has_been_asked():
                prompt_state.mark_asked()
                show_prompt()
                ensure_daemon_running()      # cover THIS session regardless of the modal choice (#165)
                return "prompted"
        ensure_daemon_running()
        return "fallback-spawn"

    def _show_cron_install_prompt(self) -> None:
        """Offer to install the OS autostart service (once). Yes → service.install();
        the result detail is surfaced in the activity log. Mirrors NewPersonaModal's
        push_screen(..., callback=...) lifecycle."""
        from harness.tui.widgets.cron_install_modal import CronInstallModal
        from harness.jobs import service

        def _on_choice(accepted: bool) -> None:
            if not accepted:
                return
            res = service.install()
            self.log(f"cron autostart: {res.detail}")

        self.push_screen(CronInstallModal(), callback=_on_choice)

    def _show_proxy_login(self) -> None:
        """Open the proxy-login modal. Mirrors _show_cron_install_prompt's
        push_screen(modal, callback) lifecycle. Password sourced from
        config_gen.ensure_management_password() (same 0600-file source the
        lifecycle uses)."""
        from harness.tui.widgets.proxy_login_modal import ProxyLoginModal
        from harness.proxy_service import config_gen
        password = config_gen.ensure_management_password()

        def _on_done(result) -> None:
            if result:
                self.log("proxy login: authenticated")

        self.push_screen(ProxyLoginModal(status={}, password=password), callback=_on_done)

    async def _mount_status_contents(self) -> None:
        bar = self.query_one("#statusbar", Container)
        # Mode chip FIRST (leftmost), where the eye lands — a security-bypass
        # indicator must not be buried behind the 1fr cwd at the far right.
        chip = StatusChip.for_yolo(self._yolo, self._yolo_pinned)
        chip.id = "statusbar-mode"
        await bar.mount(chip)
        ca_chip = StatusChip.for_compress_aware(self._compress_aware, self._compress_aware_pinned)
        ca_chip.id = "statusbar-compress-aware"
        await bar.mount(ca_chip)
        await bar.mount(Static(self._status_persona(), id="statusbar-persona", markup=True))
        await bar.mount(Static(self._status_left(), id="statusbar-left", markup=True))
        await bar.mount(Static(self._status_right(), id="statusbar-right", markup=True))

    def _status_left(self) -> str:
        return format_cwd(self.cwd, home=os.path.expanduser("~"))

    def _status_right(self) -> str:
        # No command palette is bound, so the old 'ctrl+p commands' hint is gone;
        # the right side carries context usage (or the version pre-start).
        right = self._context_tagline()
        if not self._started:
            right = self._version
        return f"[$muted]{right}[/]"

    @staticmethod
    def _fmt_tokens(n: int) -> str:
        if n >= 1_000_000:
            return f"{n/1_000_000:.1f}M"
        return f"{n/1000:.1f}K" if n >= 1000 else str(n)

    def _context_tagline(self) -> str:
        # _tokens is the latest llm.return total (prompt+completion for that call),
        # which tracks current context footprint until the next model call (or compaction).
        window = resolve_ctx_window(_model_label(self.model, self._worker_model_id))
        if self._tokens <= 0:
            return f"ctx --/{self._fmt_tokens(window)}"
        remaining = max(window - self._tokens, 0)
        return (f"ctx {self._fmt_tokens(self._tokens)}/{self._fmt_tokens(window)} "
                f"| {self._fmt_tokens(remaining)} left")

    def _refresh_status(self) -> None:
        try:
            self.query_one("#statusbar-right", Static).update(self._status_right())
        except Exception:
            pass

    def _current_persona(self) -> str:
        """The persona id this process is currently running as.

        After the first PersonaResolved chip arrives (_persona_seen True), the
        snapshot's active_id is authoritative. Before that chip lands, fall back
        to _launch_persona (the id passed via --persona on startup)."""
        if self._persona_seen:
            return self._snapshot.active_id
        return self._launch_persona

    def _status_persona(self) -> str:
        if not getattr(self, "_persona_seen", False):
            return ""              # pre-first-chip: chip is blank/hidden
        a = self._snapshot.active
        return f"[$muted]persona: {a.id}[/]" if a is not None else ""

    def _refresh_persona(self) -> None:
        try:
            self.query_one("#statusbar-persona", Static).update(self._status_persona())
        except Exception:
            pass

    # ---- YOLO mode chip (clickable footer mode chip; see components.md §A) ----

    def _refresh_yolo_chip(self) -> None:
        """Re-render the footer mode chip in place from the current live + pin
        state (mirrors _refresh_status: update one widget, no full re-render)."""
        try:
            chip = self.query_one("#statusbar-mode", StatusChip)
        except Exception:
            return
        fresh = StatusChip.for_yolo(self._yolo, self._yolo_pinned)
        chip._label = fresh._label
        chip._token = fresh._token          # keep the chip's internal state self-consistent
        chip.update(fresh._Static__content)  # the raw markup string (pre-render), re-evaluated by Textual
        self._refresh_meta_line()           # keep the top mode line ('Build · YOLO …') in sync

    def action_toggle_yolo(self) -> None:
        """Flip the live auto-allow gate (chip click / bare /yolo). Persisting is
        a separate gesture (/yolo pin); a click never changes the pin."""
        self._yolo = not self._yolo
        self._refresh_yolo_chip()
        self.run_worker(self._send_set_yolo(active=self._yolo), thread=False)

    async def action_yolo_pin(self) -> None:
        """Persist 'always launch in YOLO' (and turn it on now — pinning a mode
        you're not in is incoherent). Reconciles the chip to the TRUE persisted
        state the agent reports, so a failed write can't show a false 'pinned'."""
        self._yolo = True
        self._yolo_pinned = True            # optimistic; reconciled below
        self._refresh_yolo_chip()
        resp = await self._send_set_yolo(active=True, pin=True)
        self._reconcile_yolo(resp, want_pinned=True, verb="pin")

    async def action_yolo_unpin(self) -> None:
        """Stop auto-launching in YOLO. Leaves the live state alone. Reconciles
        from the agent's reported pin so a failed write can't silently leave the
        config pinned while the chip shows unpinned (the silent-bypass hazard)."""
        self._yolo_pinned = False           # optimistic; reconciled below
        self._refresh_yolo_chip()
        resp = await self._send_set_yolo(pin=False)
        self._reconcile_yolo(resp, want_pinned=False, verb="unpin")

    def _reconcile_yolo(self, resp: dict | None, *, want_pinned: bool, verb: str) -> None:
        """Trust the agent's reported persisted state over our optimistic guess.
        If it disagrees with what we intended (write failed / no agent), correct
        the chip and tell the user — never leave a persisted bypass hidden."""
        if not resp:
            self._notify_line(f"could not {verb}: agent unavailable — persisted state unchanged")
            return
        pinned = resp.get("pinned")
        active = resp.get("active")
        if isinstance(active, bool):
            self._yolo = active
        if isinstance(pinned, bool):
            self._yolo_pinned = pinned
            if pinned != want_pinned or not resp.get("ok", True):
                self._notify_line(
                    f"/yolo {verb} did not persist — config is "
                    f"{'pinned' if pinned else 'not pinned'}")
        self._refresh_yolo_chip()

    async def _send_set_yolo(self, *, active: bool | None = None,
                             pin: bool | None = None) -> dict | None:
        """Push the live/pin change to the agent (which owns the gate) and return
        its authoritative {ok, active, pinned} response, or None if no agent /
        the call failed."""
        if self._conn is None:
            return None
        params: dict = {}
        if active is not None:
            params["active"] = active
        if pin is not None:
            params["pin"] = pin
        try:
            return await self._conn.ext_method("harness/set_yolo", params)
        except Exception:
            return None

    # ---- Compress-aware mode chip (clickable footer chip; mirrors YOLO pattern) ----

    def _refresh_compress_aware_chip(self) -> None:
        """Re-render the compress-aware footer chip in place from the current live
        + pin state (mirrors _refresh_yolo_chip: update one widget, no full re-render)."""
        try:
            chip = self.query_one("#statusbar-compress-aware", StatusChip)
        except Exception:
            return
        fresh = StatusChip.for_compress_aware(self._compress_aware, self._compress_aware_pinned)
        chip._label = fresh._label
        chip._token = fresh._token
        chip.update(fresh._Static__content)

    def action_toggle_compress_aware(self) -> None:
        """Flip the live compress-aware gate. Persisting is a separate gesture
        (action_compress_aware_pin); a click never changes the pin."""
        self._compress_aware = not self._compress_aware
        self._refresh_compress_aware_chip()

    def action_compress_aware_pin(self) -> None:
        """Persist 'always launch with compress-aware on'. Sets the live value and
        pin marker, then writes to config. Honors the YOLO contract: pin is a
        deliberate separate gesture, not triggered by a plain toggle/click."""
        self._compress_aware = True
        self._compress_aware_pinned = True
        self._refresh_compress_aware_chip()
        _config.set_compress_aware(self._launch_persona, True)

    def action_compress_aware_unpin(self) -> None:
        """Stop auto-launching with compress-aware. Leaves the live state alone."""
        self._compress_aware_pinned = False
        self._refresh_compress_aware_chip()
        _config.set_compress_aware(self._launch_persona, False)

    def on_click(self, event) -> None:
        # Footer mode chip: a click anywhere on it toggles YOLO. Guard on the id
        # so other clicks are unaffected.
        widget = getattr(event, "widget", None)
        if widget is not None and getattr(widget, "id", None) == "statusbar-mode":
            self.action_toggle_yolo()
            return
        # Compress-aware chip: a click toggles the live gate (never persists —
        # same YOLO contract: pin is a separate gesture via /compress-aware pin).
        if widget is not None and getattr(widget, "id", None) == "statusbar-compress-aware":
            self.action_toggle_compress_aware()
            return
        # Turn footer: a click copies that turn's response and flips "(copy)" to
        # "(copied)". Guard on the _copyable marker so only turn footers respond.
        if widget is not None and getattr(widget, "_copyable", False):
            self._copy_turn_response(widget)

    def _response_md_for(self, footer):
        """The response Markdown widget this footer summarizes: the nearest Markdown
        among the footer's transcript siblings. The footer is appended right after
        its turn's answer, so we scan the footer's neighbours and pick the closest
        Markdown (searching both sides — a late delta can land the response just
        below the footer). Returns None when no answer rendered (e.g. a tool-only
        turn)."""
        try:
            kids = list(self._transcript.children)
            idx = kids.index(footer)
        except (ValueError, Exception):
            return None
        for off in range(1, len(kids)):
            for j in (idx - off, idx + off):
                if 0 <= j < len(kids) and isinstance(kids[j], Markdown):
                    return kids[j]
        return None

    def _copy_turn_response(self, footer) -> None:
        """Copy this turn's response to the clipboard and flip the label to
        (copied). The text is read LIVE from the response Markdown widget — robust
        to late-draining deltas. A turn with no rendered answer is a no-op."""
        md = self._response_md_for(footer)
        text = (getattr(md, "source", None) or "") if md is not None else ""
        if not text:
            return
        if self._copy_to_clipboard(text):
            footer.update(self._meta_markup(getattr(footer, "_elapsed", 0.0), copied=True))
        else:
            # No native tool AND no terminal to emit OSC 52 to — tell the user how
            # to enable it, and leave the label as (copy) so they know it didn't take.
            self._notify_line("clipboard unavailable — install xclip/wl-copy (Linux) "
                              "or use a terminal that supports OSC 52")

    def _copy_to_clipboard(self, text: str) -> bool:
        """Put `text` on the clipboard. Native OS tool first (pbcopy/xclip/wl-copy
        — a real success signal that also works in Terminal.app, which has no OSC
        52 support); else fall back to Textual's OSC 52 escape (works over SSH and
        in OSC-52 terminals like Ghostty/iTerm/kitty). Returns False only when
        neither path is viable (no binary AND no terminal driver, e.g. headless)."""
        from harness.tui.clipboard import native_copy
        if native_copy(text):
            return True
        if getattr(self, "_driver", None) is None:
            return False                  # OSC 52 has nowhere to write → real failure
        self.copy_to_clipboard(text)      # OSC 52 (fire-and-forget; best effort)
        return True

    # ---- presentation model (reducer) ----

    def _apply(self, event) -> None:
        """Fold one event into the snapshot, then push fresh data to the live widgets."""
        self._snapshot = reduce(self._snapshot, event)
        a = self._snapshot.active
        if a is None:
            return
        try:
            self.query_one("#activity-region", ActivityRegion).update_from(a)
        except Exception:
            pass

    def _tick_elapsed(self) -> None:
        """Quarter-second tick: update the active agent's elapsed time in-snapshot and
        refresh ActivityStatus while a turn is in flight. This 4Hz tick is a no-op
        while idle (guarded by the early return below), so no need to pause it."""
        from dataclasses import replace as _replace
        a = self._snapshot.active
        if a is None:
            return
        working_states = {
            "thinking", "responding", "running_tool",
            "awaiting_permission", "awaiting_decision",
        }
        if a.state.value not in working_states:
            return
        if not self._turn_start:
            return  # no turn timestamp yet — don't show monotonic-since-boot
        elapsed = time.monotonic() - self._turn_start
        agents = tuple(
            _replace(x, elapsed=elapsed) if x.id == a.id else x
            for x in self._snapshot.agents
        )
        self._snapshot = type(self._snapshot)(agents=agents,
                                              active_id=self._snapshot.active_id)
        try:
            self.query_one("#activity-region", ActivityRegion).update_from(self._snapshot.active)
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
        # A turn is already in flight: queue this message instead of starting a
        # second concurrent prompt() on the same session. It auto-sends when the
        # current turn ends (see _send_prompt's finally → _drain_queue).
        if self._turn_active:
            self._queued.append(text)
            self._active_input().value = ""
            self._append_line(_c("muted", f"⏳ queued: {self._escape(text)}"))
            return
        if not self._started:
            await self._enter_conversation()
        await self._submit_text(text)

    async def _submit_text(self, text: str) -> None:
        """Start a user turn for `text` — the shared path for a typed prompt AND a
        decision selection. Assumes conversation state is established."""
        self._add_user_message(text)
        inp = self._active_input()
        inp.value = ""
        # The input stays ENABLED during a turn so the user can type / queue the
        # next message (Enter while _turn_active enqueues — see on_prompt_area_submitted).
        self._turn_start = time.monotonic()
        self._compacted = None
        self._turn_active = True
        self._cancel_posted = False           # reset per-turn so ESC is fresh again
        self._active_input().placeholder = "Type to queue your next message…"
        self._apply(TurnStarted())
        self._send_gen = self._gen            # tag this turn's worker with its generation
        self.run_worker(self._send_prompt(text), thread=False)

    # ---- structured clarification ----

    def _on_decision(self, index: "int | None") -> None:
        """DecisionModal dismiss callback. `index` is an option index (0..n-1),
        TYPE_SOMETHING / CHAT_ABOUT_IT for the fallbacks, or None on esc/cancel.
        Real option -> submit its title as the next prompt; fallbacks focus/prefill
        the composer; esc just closes. The modal already dismissed itself."""
        self._decision_open = False
        active = self._snapshot.active
        view = active.decision if active else None
        if index is None:
            return                                # esc/cancel: just close, keep state
        elif index == TYPE_SOMETHING:
            self._active_input().focus()
        elif index == CHAT_ABOUT_IT:
            inp = self._active_input()
            inp.value = "Let's discuss: "
            inp.focus()
        elif view is not None and 0 <= index < len(view.options):
            self.run_worker(self._submit_text(view.options[index][0]), thread=False)
        if active and active.decision is not None:
            self._apply(DecisionOpened(None))     # clear state.decision

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
            # ESC precedence ladder (spec R5): slash already handled below
            # (this branch is the slash-closed case). Turn active → cancel FIRST,
            # before clear-text and rail-close, so a slow turn is always escapable.
            if event.key == "escape" and self._turn_active:
                event.stop()
                await self.action_cancel()
                return
            # menu closed, no turn: esc with text clears it; empty box falls
            # through to action_cancel (the global "Cancel turn" binding).
            if event.key == "escape" and self._active_input().value:
                self._active_input().value = ""
                event.stop()
                return
            # Focus-traversal model for the agents rail:
            # Tab from the prompt (when rail is hidden) → reveal and focus the rail.
            if event.key == "tab":
                rail = self.query_one("#agent-rail", AgentRail)
                if isinstance(self.focused, PromptArea) and not self._drawer_visible():
                    rail.set_rows(self._persona_rows(), subline_of=self._persona_subline)
                    self._show_drawer(True)
                    rail.focus()
                    event.stop()
                # Otherwise let Tab do normal focus traversal (don't stop).
                return
            # Esc closes the drawer whenever it's visible and no turn is active,
            # regardless of where focus is. Turn-cancel still takes priority (line 781).
            if event.key == "escape":
                if self._drawer_visible() and not self._turn_active:
                    self._show_drawer(False)
                    self._active_input().focus()
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
        # the text after the command name (e.g. "pin" in "/yolo pin")
        parts = text[1:].split() if len(text) > 1 else []
        arg = " ".join(parts[1:]) if len(parts) > 1 else ""
        if cmd is None:
            name = parts[0] if parts else ""
            cmd = resolve_command(self._commands, name)   # canonical name or exact alias
        self._active_input().value = ""
        await self._close_slash()
        if cmd is None:
            self._notify_line(f"unknown command: {text}")
            return
        await cmd.handler(self, arg)

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
        from harness import vibeproxy
        url = vibeproxy.base_url().rstrip("/") + "/models"

        def _get() -> list[str]:
            with urllib.request.urlopen(url, timeout=5) as r:
                data = json.load(r)
            return sorted(m.get("id") for m in data.get("data", []) if m.get("id"))

        import asyncio as _asyncio
        return await _asyncio.get_running_loop().run_in_executor(None, _get)

    @staticmethod
    def _pretty_model(model_id: str) -> str:
        # For a NeuralWatt alias (glm/qwen/glm-fast), show the full upstream model
        # name next to it so it's clear exactly which model the alias points at —
        # the proxy's /v1/models only exposes the short alias.
        try:
            from harness.proxy_service import config_gen
            full = config_gen.alias_to_upstream().get(model_id)
        except Exception:
            full = None
        return f"{model_id} — {full}" if full and full != model_id else model_id

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
        await composer.mount(PromptArea(placeholder=self._conversation_placeholder(),
                                        id="conversation-input"))
        await self.mount(ActivityRegion(id="activity-region"), before="#composer")
        self._refresh_status()
        self.query_one("#conversation-input", PromptArea).focus()

    def _clear_transcript(self) -> None:
        """Sync visual reset: empty the transcript and reset stream-accumulation
        state so no late delta bleeds into a fresh view. Does NOT touch _snapshot
        (its owner re-applies it) or _tokens. Safe to call from sync paths (e.g.
        the persona switch) — unlike async _reset_conversation."""
        if self._started:
            self._transcript.remove_children()
        # R2: stop the flusher and clear dirty BEFORE nulling the widget so a
        # stale flush can't fire (or paint into) a reset/replaced stream.
        self._stop_stream_timer()
        self._stream_dirty = False
        self._streaming_md = None
        self._stream_buf = ""
        self._stream_closed = True
        self._boundary_after = False

    async def _reset_conversation(self) -> None:
        """Empty the transcript and reset per-conversation state WITHOUT leaving
        the conversation view (flipping _started=False would query the removed
        #landing-input/#header-text and crash). No-op before the first prompt."""
        self._clear_transcript()
        self._tokens = 0
        self._snapshot = initial_snapshot()
        self._refresh_status()
        # Refresh mounted widgets if they exist (they may not be in all states)
        try:
            self.query_one("#activity-region", ActivityRegion).update_from(self._snapshot.active)
        except Exception:
            pass

    @property
    def _transcript(self) -> VerticalScroll:
        return self.query_one("#transcript", VerticalScroll)

    def _append(self, widget) -> None:
        """Mount a widget into the transcript and keep the view pinned to the end."""
        self._transcript.mount(widget)
        self._transcript.scroll_end(animate=False)

    def _append_streaming_below_footer(self, widget) -> None:
        """Mount a streaming answer widget, keeping any trailing run-caption footer
        last. A late-draining response (deltas arrive after prompt() returned and
        already mounted this turn's footer) must render ABOVE the footer, not under
        it. If the last transcript child is a `_copyable` footer, mount before it;
        otherwise append at the end."""
        kids = self._transcript.children
        footer = kids[-1] if kids else None
        if footer is not None and getattr(footer, "_copyable", False):
            self._transcript.mount(widget, before=footer)
            self._transcript.scroll_end(animate=False)
        else:
            self._append(widget)

    def _append_line(self, markup: str, *, classes: str | None = None) -> None:
        """Append a discrete themed line (chips, user msg, tool calls, meta, errors).

        `classes` optionally tags the Static for CSS (e.g. 'turn-meta' for the
        dimmed, indented metadata captions). Default None ⇒ byte-identical to
        every existing caller."""
        self._append(Static(markup, markup=True, classes=classes))

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
        self._flush_stream()          # R1: paint any unpainted tail before close
        self._stop_stream_timer()     # R2: no free-running timer between turns
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
        if self._tracer is not None:
            self._tracer.emit("dn", "tx.prompt", sid=self._session_id, text=text)
        try:
            resp = await self._conn.prompt(
                prompt=[acp.text_block(text)], session_id=self._session_id)
            self._apply(TurnEnded(ok=True))
            elapsed = time.monotonic() - self._turn_start
            self._write_meta(elapsed)
            if getattr(resp, "stop_reason", "end_turn") != "end_turn":
                self._append_line(_c("muted", f"— turn ended: {resp.stop_reason} —"))
        except Exception as e:
            self._apply(TurnEnded(ok=False))
            self._append_line(_c("error", f"agent disconnected — restart to continue ({e})"))
        finally:
            self._turn_active = False
            if not self._running:                 # app shut down mid-turn: skip DOM ops
                # Intentionally do NOT drain the queue here: _running is False only
                # during Textual teardown (after workers.cancel_all()), so starting a
                # new worker to process a queued prompt would run on a dying event loop.
                return
            if gen == self._gen:                  # only the CURRENT generation touches the UI
                self._hide_working()
                self._active_input().disabled = False
                self._active_input().placeholder = self._conversation_placeholder()
                self._active_input().focus()
                switched = self._apply_pending_persona()  # honor a mid-turn switch request first…
                if not switched:                          # …drain immediately only when no switch
                    self._drain_queue()                   # (switch worker drains after it resolves)

    def _drain_queue(self) -> None:
        """Start the next message the user queued mid-turn. FIFO, one per turn —
        each drained prompt runs a full turn whose own finally drains the next."""
        if self._turn_active or not self._queued:
            return
        self.run_worker(self._submit_text(self._queued.pop(0)), thread=False)

    # The footer copy affordance: a dimmed "(copy)" the user clicks to copy the
    # turn's response; becomes "(copied)" on success (set by on_click on the footer
    # widget). Plain text — no glyph-portability risk across terminal fonts.
    _COPY_LABEL = "(copy)"
    _COPIED_LABEL = "(copied)"

    def _meta_markup(self, elapsed: float, *, copied: bool = False) -> str:
        """The '▣ <persona> [bypass] · model · Ns · (copy)' run caption markup.
        The leading label is the active persona name (was 'Build'); the trailing
        label is the copy affordance ('(copied)' once copied).
        When context compaction fired this turn, appends a dim '↯ context compacted N→M msgs'."""
        model_label = _model_label(self.model, self._worker_model_id)
        yolo = f" {_c('error', 'bypass on')}" if self._yolo else ""   # records mode at turn time
        label = self._COPIED_LABEL if copied else self._COPY_LABEL
        compact_note = ""
        compacted = getattr(self, "_compacted", None)
        if compacted:
            bm = compacted.get("before_msgs", "?")
            am = compacted.get("after_msgs", "?")
            compact_note = _c("muted", f" ↯ context compacted {bm}→{am} msgs")
        return (f"{_c('accent', '▣ ' + self._mode_label())}{yolo} "
                f"{_c('muted', f'· {model_label} · {elapsed:.1f}s · ')}{_c('muted', label)}"
                f"{compact_note}")

    def _apply_pending_persona(self) -> bool:
        """If a persona switch was requested mid-turn, apply it now (turn-end),
        BEFORE draining queued prompts — so a prompt typed during the old turn
        runs in the NEW persona's room, not the old one.

        Returns True when a switch worker was scheduled (caller must NOT drain
        immediately; _switch_persona will drain after _apply_persona_switch
        repoints _session_id). Returns False when no switch was needed."""
        pid = self._pending_persona
        if pid is None or self._conn is None or pid == self._current_persona():
            self._pending_persona = None
            return False
        self._pending_persona = None
        self.run_worker(self._switch_persona(pid), thread=False)
        return True

    async def _switch_persona(self, pid: str) -> None:
        """The async half of a deferred switch: call set_persona, then apply.
        After applying (which repoints _session_id), drain any queued prompt so
        it runs in the NEW persona's room — not the old one (I1 fix)."""
        try:
            resp = await self._conn.ext_method("harness/set_persona", {"id": pid})
        except Exception as e:
            self._notify_line(f"could not switch persona: {e}")
            return
        if not resp.get("ok"):
            self._notify_line(f"persona: {resp.get('error', 'switch failed')}")
            return
        self._apply_persona_switch(resp)
        self._drain_queue()               # _session_id is now the NEW room

    def _write_meta(self, elapsed: float) -> None:
        """Append the turn's run caption as a FOOTER below the response, once the
        turn ends and the elapsed time is known. A dimmed, indented .turn-meta-run
        line that summarizes the run that produced the answer above it. The footer
        carries a (copy) affordance; on click we resolve THIS turn's response from
        the transcript (the nearest response Markdown widget) — NOT a buffer
        snapshot taken here, because the message deltas can drain AFTER prompt()
        returns (late-delivery; see _stream_message), so `_stream_buf` is often
        empty at this point. `_copyable` marks the footer for the click handler."""
        foot = Static(self._meta_markup(elapsed), markup=True, classes="turn-meta-run")
        foot._copyable = True                    # routes on_click → _copy_turn_response
        foot._elapsed = elapsed                  # to re-render markup when the label flips
        self._append(foot)
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

        opened_new = False
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
            # Late-delivery ordering: prompt() can return (mounting THIS turn's
            # footer) before the trailing message deltas arrive. If the run-caption
            # footer is already the last child, mount the answer ABOVE it so the
            # '… (copy)' caption stays BELOW the prose — otherwise the answer lands
            # under the footer (footer-above-answer bug).
            self._append_streaming_below_footer(self._streaming_md)
            self._stream_buf = ""
            self._stream_closed = False
            self._boundary_after = False
            opened_new = True
        # else: stream already open → keep extending it.
        self._stream_buf += text
        self._stream_dirty = True
        if self._stream_closed or opened_new:
            # R1: a late delta after close cannot rely on the interval (stopped on
            # close) → flush SYNC. opened_new: the first chunk of a new answer must
            # paint immediately, not wait up to 80ms for the timer (avoids a
            # post-_hide_working blank flicker). Subsequent open-stream chunks
            # coalesce on the timer.
            self._flush_stream()
            if not self._stream_closed:
                self._ensure_stream_timer()   # arm for the chunks that follow
        else:
            self._ensure_stream_timer()
        self._transcript.scroll_end(animate=False)

    def _ensure_stream_timer(self) -> None:
        # R2: start a 12Hz flusher on stream-open; it is stopped on close/reset.
        if self._stream_timer is None:
            self._stream_timer = self.set_interval(1 / 12, self._flush_stream)

    def _stop_stream_timer(self) -> None:
        if self._stream_timer is not None:
            self._stream_timer.stop()
            self._stream_timer = None

    def _flush_stream(self) -> None:
        # R2/R3: no-op when nothing to paint or the widget is gone (teardown);
        # capture the CURRENT widget+buffer so a flush can't paint a stale buffer
        # into a new widget after a reset.
        if not self._stream_dirty or self._streaming_md is None:
            return
        md, buf = self._streaming_md, self._stream_buf
        self._stream_dirty = False
        # Drop the turn-end sentinel if a model typed it into its prose (the
        # tool-row guard _is_done_sentinel never sees a typed line). Stripping the
        # WHOLE buffer here — not the raw _stream_buf accumulator — keeps the
        # rendered widget clean (which is also what the (copy) affordance reads,
        # see _copy_turn_response) while leaving the accumulator untouched so a
        # sentinel split across chunks is matched once fully assembled.
        buf = strip_done_sentinel_prose(buf)
        # R4: md.update is a no-op until the widget mounts; call_after_refresh
        # guarantees the FIRST paint lands post-mount, matching prior behavior.
        self.call_after_refresh(md.update, buf)

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
        # --debug trace: record the relayed agent event (if any) + the dn-side
        # receipt, BEFORE any early return below (e.g. stream_reset) so nothing is
        # dropped. NullTracer makes both calls no-ops when debug is off.
        if self._tracer is not None:
            extract_agent_trace(self._tracer, msg.update)
            self._tracer.emit("dn", "rx.update", sid=msg.session_id,
                              kind=type(msg.update).__name__)
        # token usage, if the agent surfaced any under _meta
        self._maybe_update_tokens(getattr(msg.update, "field_meta", None))
        # context compaction note for this turn's footer
        self._maybe_update_compacted(getattr(msg.update, "field_meta", None))
        # an explicit per-step boundary signal: Task 4 emits an empty message_chunk
        # carrying _meta stream_reset (nested under "harness" by with_meta()). Close
        # the current block as an IN-TURN boundary so the next prose opens a fresh
        # widget, then return early — the empty chunk must NOT render a blank line.
        meta = getattr(msg.update, "field_meta", None)
        if isinstance(meta, dict) and (meta.get("harness") or {}).get("stream_reset"):
            self._end_stream(boundary=True)
            return
        if isinstance(meta, dict) and (meta.get("harness") or {}).get("resumed"):
            self._end_stream(boundary=True)
            self._append_line(_c("muted", "── resumed ──────────────────────────────"))
            return
        # A new turn's classification chip is the first thing the agent emits for
        # that turn (acp_agent.py emits task_classified before any prose, on EVERY
        # dispatch path). Treat it as an in-turn boundary so the next prose opens a
        # FRESH widget instead of extending the PRIOR turn's kept block — without
        # this, _add_user_message clears _boundary_after, the prior widget is no
        # longer last (footer/prompt/chip mounted after it), and the late-delta
        # branch in _stream_message would append turn N's answer into turn N-1's
        # widget (answer renders under the wrong prompt). A genuine late delta of
        # the prior turn carries NO task_classified chip, so it still extends in
        # place. NB: this relies on every prose-producing path emitting
        # task_classified first; if a future path streams prose without it, the
        # boundary won't fire and the misroute returns.
        if isinstance(meta, dict) and (meta.get("harness") or {}).get("task_classified"):
            self._end_stream(boundary=True)
        # fold a decision view if present → push a modal (not inline). The
        # question prose rides this SAME chunk (acp_agent emits one message_chunk
        # carrying both), so the modal owns the question and the message branch
        # below suppresses the duplicate inline prose.
        dv = decision_from_meta(getattr(msg.update, "field_meta", None))
        if dv is not None:
            self._apply(DecisionOpened(dv))
            if not self._decision_open:
                self._decision_open = True
                self.push_screen(DecisionModal(dv), self._on_decision)
        # fold a persona resolution if present (structured path — NOT harness_chips)
        pid = persona_from_meta(getattr(msg.update, "field_meta", None))
        if pid:
            self._apply(PersonaResolved(pid))
            self._persona_seen = True
            self._refresh_persona()
        for chip in harness_chips(getattr(msg.update, "field_meta", None)):
            self._append_line(_c("muted", f"\\[{chip}]"), classes="turn-meta")
        item = render_update(msg.update)
        if item is None:
            return
        # fold item into the presentation model
        self._apply(ItemReceived(item))
        if item.kind == "message":
            # Suppress the question prose when this chunk also carried a decision:
            # the modal owns the question, so rendering it inline would duplicate it.
            if item.text and dv is None:
                self._stream_message(item.text)
        elif item.kind == "thought":
            if item.text:
                self._end_stream(boundary=True)  # a thought ends the current step's block
                self._append_line(f"[{COLORS['muted']} italic]{self._escape(item.text)}[/]")
        elif item.kind == "user":
            if item.text:
                self._append_line(f"{_c('accent', '▌')} [b]{self._escape(item.text)}[/b]")
        elif item.kind == "tool":
            self._end_stream(boundary=True)  # finalize the current answer block
            # tool activity is shown in the pinned ActivityRegion (refreshed by _apply),
            # NOT inline in the transcript.
        elif item.kind == "tool_update":
            pass  # handled by the reducer fold + ActivityRegion refresh

    def action_toggle_details(self) -> None:
        try:
            self.query_one("#activity-region", ActivityRegion).toggle_details()
        except Exception:
            pass

    def _maybe_update_tokens(self, field_meta) -> None:
        if not isinstance(field_meta, dict):
            return
        usage = (field_meta.get("harness") or {}).get("usage") if isinstance(
            field_meta.get("harness"), dict) else None
        if isinstance(usage, dict) and isinstance(usage.get("total"), int):
            self._tokens = usage["total"]
            self._apply(TokensUpdated(self._tokens))
            self._refresh_status()

    def _maybe_update_compacted(self, field_meta) -> None:
        """Record a context.compacted event for the current turn (shown in footer)."""
        if not isinstance(field_meta, dict):
            return
        ev = (field_meta.get("harness") or {}).get("context_compacted") if isinstance(
            field_meta.get("harness"), dict) else None
        if isinstance(ev, dict):
            self._compacted = ev

    # ---- permissions / cancel / teardown (unchanged plumbing) ----

    def on_permission_request(self, msg: PermissionRequest) -> None:
        self._pending_perm = msg.future

        # The agent sends the real command in tool_call.title (e.g. "$ sed ...");
        # strip a leading "$ " so the modal doesn't double it. Hoisted above
        # _resolve so the --debug trace can record the command with the decision.
        title = getattr(msg.tool_call, "title", "") or ""
        command = title[2:] if title.startswith("$ ") else title

        def _resolve(chosen) -> None:
            self._pending_perm = None
            if self._tracer is not None:
                self._tracer.emit("dn", "perm", command=command,
                                  decision="allowed" if chosen else "denied")
            if not msg.future.done():
                msg.future.set_result(chosen)

        self.push_screen(PermissionModal(command, msg.options), _resolve)

    async def action_cancel(self) -> None:
        # Gate the ENTIRE body on _cancel_posted: on_key calls action_cancel
        # directly AND Textual's global ("escape","cancel") binding also fires
        # because event.stop() does not suppress binding dispatch.  One ESC press
        # therefore triggers two invocations — gating here means the second is a
        # complete no-op (no extra cancel() RPC, no extra tx.cancel trace, no
        # extra feedback line).  _cancel_posted is reset to False at the top of
        # each new turn in _submit_text, so ESC works again on the next turn.
        if self._cancel_posted:
            return
        if self._conn is not None and self._session_id is not None:
            self._cancel_posted = True
            if self._tracer is not None:
                self._tracer.emit("dn", "tx.cancel", sid=self._session_id)
            await self._conn.cancel(session_id=self._session_id)
            if self._started:
                self._append_line(_c("muted", "— canceling… —"))

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

    def _persona_display_name(self, pid: str) -> str:
        """The persona's display name from its persona.toml `name`, falling back
        to the id. One lookup shared by the rail rows and the room header."""
        from harness import persona_config, paths
        ws = paths.default_workspace_dir() if pid == "default" \
            else paths.config_dir() / "agents" / pid
        return persona_config.read_name(ws) or pid

    def _persona_rows(self):
        from harness import persona_select
        from harness.tui.roster import persona_rows
        active = self._snapshot.active
        return persona_rows(persona_select.list_personas(), self._current_persona(),
                            self._persona_display_name,
                            active_status=(active.state if active else AgentState.IDLE))

    def _persona_subline(self, row):
        """Sub-line for a persona card: real task count for the active persona,
        'idle' for the rest (no fabricated telemetry — others aren't running)."""
        active = self._snapshot.active
        if row.active and active is not None:
            n = len(active.tasks)
            return f"{n} task{'s' if n != 1 else ''}" if n else "idle"
        return "idle"

    def _drawer_visible(self) -> bool:
        try:
            return self.query_one("#agent-drawer").display
        except Exception:
            return False

    def _show_drawer(self, visible: bool) -> None:
        """Toggle the whole agents drawer (rail + QUICK KEYS legend) as one unit."""
        try:
            self.query_one("#agent-drawer").display = visible
        except Exception:
            pass

    def action_toggle_rail(self) -> None:
        rail = self.query_one("#agent-rail", AgentRail)
        if not self._drawer_visible():
            rail.set_rows(self._persona_rows(), subline_of=self._persona_subline)   # refresh on open
            self._show_drawer(True)
            rail.focus()
        else:
            self._show_drawer(False)
            self._active_input().focus()

    # ---- cron jobs drawer (mirrors the agents drawer) ----

    def _cron_drawer_visible(self) -> bool:
        try:
            return self.query_one("#cron-drawer").display
        except Exception:
            return False

    def _show_cron_drawer(self, visible: bool) -> None:
        try:
            self.query_one("#cron-drawer").display = visible
        except Exception:
            pass

    def action_toggle_cron(self) -> None:
        from harness.jobs import ops
        dash = self.query_one("#cron-dashboard", CronDashboard)
        if not self._cron_drawer_visible():
            dash.set_rows(ops.list_jobs())            # refresh roster on open
            self._show_cron_drawer(True)
            dash.focus()
        else:
            self._show_cron_drawer(False)
            self._active_input().focus()


    def on_job_action_failed(self, event: JobActionFailed) -> None:
        """A roster action (run/toggle/remove) raised — surface it and resync."""
        event.stop()
        self._notify_line(f"job {event.job_id}: {event.action} failed — {event.error}")
        try:
            self.query_one("#cron-dashboard", CronDashboard).refresh_jobs()
        except Exception:
            pass

    async def on_persona_selected(self, event: PersonaSelected) -> None:
        event.stop()
        if self._turn_active:                 # don't switch under a live turn — queue it
            if event.id != self._current_persona():
                self._pending_persona = event.id          # last-wins
                name = self._persona_display_name(self._current_persona())
                self._notify_line(f"{name} is still working — switching when this turn finishes.")
            self._show_drawer(False)
            return
        if self._conn is None:
            return
        if event.id == self._current_persona():
            # already this persona — just close the drawer, no switch (no-op enter)
            self._show_drawer(False)
            self._active_input().focus()
            return
        try:
            resp = await self._conn.ext_method("harness/set_persona", {"id": event.id})
        except Exception as e:
            self._notify_line(f"could not switch persona: {e}")
            return
        if not resp.get("ok"):
            self._notify_line(f"persona: {resp.get('error', 'switch failed')}")
            return
        self._apply_persona_switch(resp)

    def _apply_persona_switch(self, resp: dict, note: str | None = None) -> None:
        """Apply a successful set_persona/create_persona result: repoint the session,
        update the indicator + footer, CLEAR the prior persona's transcript, write
        the room header, optionally schedule a transcript replay (Phase 2), close
        the rail, refocus. `note` overrides the default room header (create passes
        its own)."""
        self._session_id = resp["session_id"]
        self._persona_seen = True
        self._apply(PersonaResolved(resp["id"]))   # updates snapshot + ActivityRegion
        self._refresh_persona()                    # _apply does NOT refresh the chip
        model = resp.get("model")
        if model:
            self._worker_model_id = model
            self._refresh_meta_line()
        # Each persona is its own conversation: clear the previous room so its
        # messages don't bleed into this one, then show whose room this is.
        self._clear_transcript()
        count = resp.get("message_count", 0)
        if self._started:
            name = self._persona_display_name(resp["id"])
            if note:
                self._append_line(_c("muted", note))
            else:
                self._append_line(_c("accent", f"now in {name}'s conversation"))
                self._append_line(_c("muted", "a separate conversation · remembers across switches"))
                if count == 0:
                    self._append_line(_c(
                        "muted",
                        f"This is {name}'s conversation. It's separate from your others "
                        f"and remembers across switches. Say hello."))
        # If there's prior history, stream it back via the engine (Phase 2 replay).
        # The worker is scheduled AFTER the room header so header appears first,
        # then replayed messages, then the resumed seam.
        if count > 0 and self._conn is not None:
            self.run_worker(self._replay_session(resp["id"]), thread=False)
        # close the drawer + refocus the prompt
        self._show_drawer(False)
        self._active_input().focus()

    async def _replay_session(self, pid: str) -> None:
        """Ask the engine to stream this persona's prior transcript back; the
        streamed session_updates render through the normal on_session_update path,
        ending with the `resumed` seam."""
        try:
            await self._conn.ext_method("harness/replay_session", {"id": pid})
        except Exception as e:
            self._notify_line(f"could not load earlier messages: {e}")

    def on_new_persona_requested(self, event) -> None:
        event.stop()
        from harness.tui.widgets.new_persona_modal import NewPersonaModal

        if self._turn_active:               # guard at open-time (I3)
            self._notify_line("finish the current turn before creating a persona")
            return

        def _done(resp):
            if resp:                        # resp is the {ok:true,...} dict on success
                self._apply_persona_switch(
                    resp, note=f"created persona: {resp['id']} — now talking to it")

        from harness.persona_select import slugify_persona_name
        self.push_screen(NewPersonaModal(on_create=self._do_create_persona, slugify=slugify_persona_name), _done)

    async def _do_create_persona(self, name: str) -> dict:
        """App-side create callback invoked by NewPersonaModal's worker. Slugs the raw
        typed name to a safe id, keeps the raw name as the display label, and forwards
        both to the engine. Returns the ext_method resp dict (modal interprets ok/error)."""
        from harness.persona_select import slugify_persona_name
        slug = slugify_persona_name(name)
        if not slug:
            return {"ok": False, "error": "enter a name with letters or numbers"}
        if self._conn is None:
            return {}
        return await self._conn.ext_method(
            "harness/create_persona", {"id": slug, "display_name": name.strip()})

    async def action_reload(self) -> None:
        if self._busy:
            return
        self._busy = True                         # never released; the process is replaced
        self._reexec = True                       # main() re-execs after run() returns
        self.exit()                               # Textual restores the terminal; run() returns

    async def on_unmount(self) -> None:
        # Fire session_end while the tracer is still open so hooks can log.
        _hooks.dispatch("session_end", tracer=self._tracer,
                        cwd=self.cwd, persona_id=self._current_persona())
        if self._pending_perm is not None and not self._pending_perm.done():
            self._pending_perm.set_result(None)
        if self._cm is not None:
            try:
                await self._cm.__aexit__(None, None, None)
            except Exception as e:
                # low-stakes at exit, but record it (was a bare swallow) so a
                # subprocess that won't die cleanly leaves a breadcrumb.
                self.log(f"agent teardown raised on exit: {e!r}")
                if self._tracer is not None:
                    self._tracer.emit("dn", "teardown.error", error=str(e))
        if self._tracer is not None:
            self._tracer.close()              # flush the trace file on app exit
            self._tracer = None
