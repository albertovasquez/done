from __future__ import annotations
import urllib.request
import json

_BASE = "http://localhost:8317/v0/management"
# Provider → management auth-url path. Only these three expose the browser+poll
# flow (verified vs help.router-for.me). xAI/Kimi are CLI-flag; gemini is API-key.
_AUTH_URL_PATHS = {
    "anthropic": "anthropic-auth-url",
    "codex": "codex-auth-url",
    "antigravity": "antigravity-auth-url",
}


def _get(path: str, password: str, base: str = _BASE):
    req = urllib.request.Request(f"{base}/{path}",
                                 headers={"Authorization": f"Bearer {password}"})
    resp = urllib.request.urlopen(req, timeout=5)        # noqa: S310 (localhost)
    body = json.loads(resp.read().decode())
    return type("R", (), {"status_code": resp.status, "json": lambda self=None: body})()


def is_ready(password: str, base: str = _BASE) -> bool:
    try:
        return _get("get-auth-status", password, base).status_code == 200
    except Exception:
        return False


def auth_url(provider: str, password: str, base: str = _BASE):
    r = _get(_AUTH_URL_PATHS[provider], password, base).json()
    return r["url"], r["state"]


def poll_auth_status(state: str, password: str, base: str = _BASE) -> str:
    # OPEN ITEM (spec #4): confirm exact response field/terminal states.
    r = _get(f"get-auth-status?state={state}", password, base).json()
    return r.get("status", "pending")
