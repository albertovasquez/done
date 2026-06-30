"""ProxyLoginModal — list all CLIProxy providers with auth status, dispatch login.

Lifecycle:
  - Opens with a table of providers (✓ authed, ✗/— not authed).
  - User selects a row (↑↓ + Enter).
  - browser_poll: calls login.start → opens browser, then polls management.poll_auth_status
    on a timer until the state resolves (✓) or the user presses Esc.
  - cli_flag: calls login.start synchronously; shows result inline.
  - api_key: shows a docs hint (no interactive flow).
  - Dismiss with None on Esc; True after a successful auth.

The pure module-level function `provider_rows(status)` is unit-testable without a
running Textual app — the modal is NOT unit-tested (Textual widget convention).
"""
from __future__ import annotations

import subprocess
import webbrowser

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, ListItem, ListView, Static

from harness.proxy_service import providers as _p
from harness.proxy_service import login, management

_HINT = {
    "browser_poll": "browser",
    "cli_flag": "CLI flag",
    "api_key": "API key (docs)",
}

_SPINNER = ["◐", "◓", "◑", "◒"]            # mirrors ActivityStatus._CYCLE


# ---------------------------------------------------------------------------
# Pure, tested helper
# ---------------------------------------------------------------------------

def provider_rows(status: dict) -> list[dict]:
    """Build display rows for every provider in PROVIDERS.

    Each row has: id, label, mark (✓/✗/—), hint (human-readable mechanism).
    api_key providers get — because there is no live auth state to verify.
    """
    rows = []
    for prov in _p.PROVIDERS:
        authed = status.get(prov.id, False)
        rows.append({
            "id": prov.id,
            "label": prov.label,
            "mark": "✓" if authed else ("—" if prov.mechanism == "api_key" else "✗"),
            "hint": _HINT[prov.mechanism],
        })
    return rows


# ---------------------------------------------------------------------------
# Modal widget
# ---------------------------------------------------------------------------

class ProxyLoginModal(ModalScreen):
    """Provider-list modal — select a provider to log in.

    Constructor args:
        status  dict mapping provider_id → bool (authed)
        password  CLIProxy management password (for browser-poll follow-up)
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, status: dict, password: str = "") -> None:
        super().__init__()
        self._status = status
        self._password = password
        self._rows = provider_rows(status)
        self._i = 0
        self._spinner_timer = None
        self._poll_timer = None
        self._active_handle = None          # LoginHandle for the in-flight login

    # ------------------------------------------------------------------ compose

    def compose(self) -> ComposeResult:
        with Vertical(id="proxy-login-box"):
            yield Static(
                "[b]Proxy login[/b]   [$muted]↑↓ move · enter login · esc cancel[/]",
                id="proxy-login-title",
                markup=True,
            )
            yield ListView(id="proxy-login-list")
            yield Static("", id="proxy-login-status", markup=True)

    def on_mount(self) -> None:
        self._populate()
        self.query_one("#proxy-login-list", ListView).focus()

    # ------------------------------------------------------------------ populate

    def _populate(self) -> None:
        lv = self.query_one("#proxy-login-list", ListView)
        lv.clear()
        for row in self._rows:
            text = f"{row['mark']}  {row['label']}   [$muted]{row['hint']}[/]"
            item = ListItem(Label(text, markup=True))
            item.data = row["id"]           # carry provider id
            lv.append(item)

    # ------------------------------------------------------------------ selection

    @on(ListView.Selected, "#proxy-login-list")
    def _selected(self, event: ListView.Selected) -> None:
        provider_id = getattr(event.item, "data", None)
        if not provider_id:
            return
        prov = next((p for p in _p.PROVIDERS if p.id == provider_id), None)
        if prov is None:
            return
        if prov.mechanism == "api_key":
            self._set_status(
                f"[$muted]{prov.label} uses an API key — see the CLIProxy docs for setup.[/]"
            )
            return
        self._start_login(provider_id)

    # ------------------------------------------------------------------ login dispatch

    def _start_login(self, provider_id: str) -> None:
        self._set_status(f"[$accent]◐[/] connecting…")
        self._start_spinner()
        self.run_worker(lambda: self._do_login(provider_id), thread=True)

    def _do_login(self, provider_id: str) -> None:
        try:
            handle = login.start(
                provider_id,
                self._password,
                open_browser=webbrowser.open,
                run_subprocess=subprocess.run,
            )
        except Exception as exc:
            self._stop_spinner()
            self._set_status(f"[$error]login failed: {exc}[/]")
            return

        self._active_handle = handle

        if handle.mechanism == "browser_poll":
            # Spinner stays running; poll on a timer for auth completion.
            self._set_status("[$muted]Browser opened — waiting for auth…[/]")
            self._poll_timer = self.set_interval(2.0, self._poll_browser_auth)

        elif handle.mechanism == "cli_flag":
            self._stop_spinner()
            if handle.rc is not None and handle.rc.returncode == 0:
                self._set_status(f"[$accent]✓[/] logged in")
                self._finish_success()
            else:
                rc = handle.rc.returncode if handle.rc else "?"
                self._set_status(f"[$error]login failed (exit {rc})[/]")

    def _poll_browser_auth(self) -> None:
        if self._active_handle is None or self._active_handle.state is None:
            return
        try:
            state = management.poll_auth_status(
                self._active_handle.state, self._password
            )
        except Exception:
            return          # transient error — keep polling
        if state == "complete":
            if self._poll_timer is not None:
                self._poll_timer.stop()
                self._poll_timer = None
            self._stop_spinner()
            self._set_status("[$accent]✓[/] authenticated")
            self._finish_success()

    def _finish_success(self) -> None:
        self.dismiss(True)

    # ------------------------------------------------------------------ spinner

    def _start_spinner(self) -> None:
        self._i = 0
        self._spinner_timer = self.set_interval(0.15, self._tick_spinner)

    def _tick_spinner(self) -> None:
        glyph = _SPINNER[self._i % len(_SPINNER)]
        self._i += 1
        # Preserve the current status text; just update the leading glyph.
        status = self.query_one("#proxy-login-status", Static)
        current = status.renderable
        # Replace the leading glyph markup if present, else prepend.
        status.update(f"[$accent]{glyph}[/] {self._status_text}")

    def _stop_spinner(self) -> None:
        if self._spinner_timer is not None:
            self._spinner_timer.stop()
            self._spinner_timer = None

    # ------------------------------------------------------------------ status line

    _status_text: str = ""

    def _set_status(self, markup: str) -> None:
        # Strip the leading glyph+space prefix that the spinner may have written.
        # We store the bare message so the spinner tick can prefix its own glyph.
        import re
        bare = re.sub(r"^\[.*?\].{1,2}\[/\]\s*", "", markup)
        self._status_text = bare if bare else markup
        self.query_one("#proxy-login-status", Static).update(markup)

    # ------------------------------------------------------------------ cancel

    def action_cancel(self) -> None:
        self._stop_spinner()
        if self._poll_timer is not None:
            self._poll_timer.stop()
            self._poll_timer = None
        self.dismiss(None)
