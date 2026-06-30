from __future__ import annotations
import os
import secrets
from harness.proxy_service import paths


def generate(port: int = 8317) -> str:
    # localhost-bound; client auth disabled (empty api-keys) since localhost-only;
    # management reachability comes from the injected MANAGEMENT_PASSWORD env, so
    # we intentionally omit remote-management.secret-key (config plaintext is
    # bcrypt-hashed on boot and unusable thereafter).
    return (
        'host: "127.0.0.1"\n'
        f"port: {port}\n"
        "api-keys: []\n"
        "remote-management:\n"
        "  allow-remote: false\n"
        "# openai-compatibility upstreams (e.g. NeuralWatt) are appended by docs.\n"
    )


def ensure_management_password() -> str:
    p = paths.secret_path()
    if p.exists():
        return p.read_text().strip()
    pw = secrets.token_urlsafe(32)
    p.write_text(pw)
    os.chmod(p, 0o600)
    return pw
