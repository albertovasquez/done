from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    mechanism: str            # "browser_poll" | "cli_flag" | "api_key"
    login_flag: str | None = None


PROVIDERS = [
    Provider("anthropic", "Claude", "browser_poll"),
    Provider("codex", "OpenAI / Codex", "browser_poll"),
    Provider("antigravity", "Antigravity", "browser_poll"),
    Provider("xai", "Grok (xAI)", "cli_flag", "--xai-login"),
    Provider("kimi", "Kimi", "cli_flag", "--kimi-login"),
    Provider("gemini", "Gemini / AI Studio", "api_key"),
]
