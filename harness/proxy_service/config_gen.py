from __future__ import annotations
import os
import secrets
from harness.proxy_service import paths


# NeuralWatt upstream models exposed via the proxy, as (upstream model id, alias).
# The alias is what the harness/router requests (e.g. ROUTER_MODEL=openai/qwen).
# IDs below were CONFIRMED against a live NeuralWatt /v1/models on 2026-06-30.
# To see the full list (more glm/qwen/kimi variants exist):
#   curl -s https://api.neuralwatt.com/v1/models -H "Authorization: Bearer $NEURALWATT_API_KEY"
_NEURALWATT_MODELS = [
    ("glm-5.2", "glm"),                      # GLM 5.2 — worker model (dn --model glm)
    ("qwen3.5-397b-fast", "qwen"),          # cheap ROUTER model (ROUTER_MODEL=openai/qwen)
    ("glm-5.2-short-fast", "glm-fast"),     # lighter GLM — router fallback (ROUTER_FALLBACK_MODEL=openai/glm-fast)
]


def alias_to_upstream() -> dict:
    """Public {alias: upstream_model_id} map for the NeuralWatt upstreams. Used by
    the TUI to show the full model name next to its short alias in the model menu
    (the proxy's /v1/models only exposes the alias)."""
    return {alias: model_id for model_id, alias in _NEURALWATT_MODELS}


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
