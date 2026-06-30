from __future__ import annotations
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
