from __future__ import annotations
import os
import secrets
from harness.proxy_service import paths
from harness.proxy_service.model_map import NEURALWATT_MODELS as _NEURALWATT_MODELS, alias_to_upstream  # noqa: F401


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


def config_drift(*, env=None) -> str:
    """Compare config.yaml on disk against what generate() would produce now.

    Returns "missing" (no config.yaml yet — never installed), "drifted"
    (config.yaml exists but differs from current generate() output — e.g.
    NEURALWATT_API_KEY changed since the last install/upgrade), or "ok"
    (matches). Effectively pure — generate() calls paths.data_dir(), which
    mkdirs the data dir as a side effect (harmless, idempotent, same dir
    install() creates anyway), but this function never writes config.yaml
    itself and never raises on a missing file.
    """
    cfg_path = paths.config_path()
    if not cfg_path.exists():
        return "missing"
    current = generate(env=env)
    on_disk = cfg_path.read_text()
    return "ok" if current == on_disk else "drifted"


def ensure_management_password() -> str:
    p = paths.secret_path()
    if p.exists():
        return p.read_text().strip()
    pw = secrets.token_urlsafe(32)
    p.write_text(pw)
    os.chmod(p, 0o600)
    return pw
