from __future__ import annotations
import os
import secrets
from harness.proxy_service import paths


# NeuralWatt upstream models exposed via the proxy, as (upstream model id, alias).
# The alias is what the harness/router requests (e.g. ROUTER_MODEL=openai/qwen).
# Upstream ids must match NeuralWatt's /v1/models exactly — confirm with:
#   curl -s https://api.neuralwatt.com/v1/models -H "Authorization: Bearer $NEURALWATT_API_KEY"
# and update here if they differ. (IDs below are from NeuralWatt's docs as of
# 2026-06-30; the GLM id in particular is unverified against a live key.)
_NEURALWATT_MODELS = [
    ("zai-org/GLM-4.6", "glm"),
    ("Qwen/Qwen3-Coder-480B-A35B-Instruct", "qwen"),
]


def generate(port: int = 8317, *, env=None) -> str:
    # localhost-bound; client auth disabled (empty api-keys) since localhost-only;
    # management reachability comes from the injected MANAGEMENT_PASSWORD env, so
    # we intentionally omit remote-management.secret-key (config plaintext is
    # bcrypt-hashed on boot and unusable thereafter).
    if env is None:
        env = os.environ
    # auth-dir pins where OAuth tokens (auths/*.json) are written and read. Without
    # it CLIProxyAPI defaults to ./auths relative to the process cwd, so the
    # `dn proxy login` foreground process and the background service would write to
    # different places and not share credentials. Pin both to the harness data dir.
    auths_dir = paths.data_dir() / "auths"
    base = (
        'host: "127.0.0.1"\n'
        f"port: {port}\n"
        "api-keys: []\n"
        f'auth-dir: "{auths_dir}"\n'
        "remote-management:\n"
        "  allow-remote: false\n"
    )
    nw_key = env.get("NEURALWATT_API_KEY")
    if nw_key:
        models_yaml = "".join(
            f'      - name: "{model_id}"\n        alias: "{alias}"\n'
            for model_id, alias in _NEURALWATT_MODELS
        )
        base += (
            "openai-compatibility:\n"
            '  - name: "neuralwatt"\n'
            '    base-url: "https://api.neuralwatt.com/v1"\n'
            "    api-key-entries:\n"
            f'      - api-key: "{nw_key}"\n'
            "    models:\n"
            f"{models_yaml}"
        )
    return base


def ensure_management_password() -> str:
    p = paths.secret_path()
    if p.exists():
        return p.read_text().strip()
    pw = secrets.token_urlsafe(32)
    p.write_text(pw)
    os.chmod(p, 0o600)
    return pw
