from __future__ import annotations
import time
import webbrowser
from dataclasses import dataclass
from harness.proxy_service import providers as _providers, management, binary


@dataclass
class LoginHandle:
    provider_id: str
    mechanism: str
    state: str | None = None       # browser_poll only
    rc: int | None = None          # cli_flag only


def _provider(pid: str):
    for p in _providers.PROVIDERS:
        if p.id == pid:
            return p
    raise KeyError(pid)


def start(provider_id, password, *, open_browser, run_subprocess) -> LoginHandle:
    p = _provider(provider_id)
    if p.mechanism == "browser_poll":
        url, state = management.auth_url(p.id, password)
        open_browser(url)
        return LoginHandle(p.id, p.mechanism, state=state)
    if p.mechanism == "cli_flag":
        rc = run_subprocess([str(binary.target_path()), p.login_flag])
        return LoginHandle(p.id, p.mechanism, rc=rc)
    return LoginHandle(p.id, p.mechanism)        # api_key → docs


def run_cli_login(provider, password, *, open_browser=webbrowser.open,
                  poll=management.poll_auth_status, sleep=time.sleep, out=print,
                  attempts=60,
                  terminal=frozenset({"ok", "success", "completed", "authenticated"})):
    url, state = management.auth_url(provider, password)
    if open_browser(url):
        out("opened browser — waiting for sign-in…")
    else:
        out(f"open this URL to sign in:\n  {url}\nwaiting for sign-in…")
    for _ in range(attempts):
        status = poll(state, password)
        if status in terminal:
            out(f"✓ {provider} authenticated")
            return True
        sleep(2)
    out(f"sign-in didn't complete — re-run `dn proxy login {provider}`")
    return False
