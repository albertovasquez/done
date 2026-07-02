from __future__ import annotations
from pathlib import Path


def data_dir() -> Path:
    """Harness-owned data dir for the proxy (binary, config, secret)."""
    d = Path.home() / ".local" / "share" / "harness" / "proxy"
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_path() -> Path:
    return data_dir() / "config.yaml"


def secret_path() -> Path:
    return data_dir() / "management-password"   # 0600, plaintext, in-memory injected


def client_key_path() -> Path:
    return data_dir() / "client-api-key"        # 0600, plaintext, baked into config.yaml
