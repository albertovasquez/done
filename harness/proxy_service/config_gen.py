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


def _machine_global_env() -> dict:
    """Resolve NEURALWATT_API_KEY (and anything else) from process env layered
    over ONLY the machine-global ~/.config/harness/.env — never a project-local
    .env. This mirrors what `dn proxy install`/`upgrade` actually bakes into
    config.yaml (proxy_service/cli.py:5-12 deliberately excludes project env,
    since the proxy is machine-global). config_drift() must compare against
    THIS, not raw os.environ, so a project-local NEURALWATT_API_KEY (which the
    TUI's own env loading layers in) can never produce an unresolvable
    "drifted" warning that `dn proxy upgrade` could never clear."""
    from dotenv import dotenv_values
    from harness import paths as _harness_paths

    merged = dict(dotenv_values(_harness_paths.config_dir() / ".env"))
    merged.update(os.environ)  # process env always wins — matches load_env()'s override=False precedence
    return merged


def config_drift(*, env=None) -> str:
    """Compare config.yaml on disk against what generate() would produce now.

    Returns "missing" (no config.yaml yet — never installed), "drifted"
    (config.yaml exists but differs from current generate() output — e.g.
    NEURALWATT_API_KEY changed since the last install/upgrade), or "ok"
    (matches). Effectively pure — generate() calls paths.data_dir(), which
    mkdirs the data dir as a side effect (harmless, idempotent, same dir
    install() creates anyway), but this function never writes config.yaml
    itself and never raises on a missing file.

    Defaults (env=None) to _machine_global_env() rather than raw os.environ:
    the two call sites (lifecycle.status() and the TUI's
    _check_proxy_config_drift()) run in processes with DIFFERENT os.environ
    precedence — the TUI layers in a project-local .env, `dn proxy` never
    does — so comparing against raw os.environ would make the two callers
    disagree about drift for the same on-disk config.yaml. config.yaml is
    always written from machine-global env only (see generate()'s callers in
    install()/upgrade()), so that's what we must diff against here,
    regardless of which process calls us.
    """
    cfg_path = paths.config_path()
    if not cfg_path.exists():
        return "missing"
    if env is None:
        env = _machine_global_env()
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
