from __future__ import annotations
import os
import secrets
from harness.proxy_service import paths
from harness.proxy_service.model_map import NEURALWATT_MODELS as _NEURALWATT_MODELS, alias_to_upstream  # noqa: F401


def generate(port: int = 8317, *, env=None) -> str:
    # localhost-bound; management reachability comes from the injected
    # MANAGEMENT_PASSWORD env, so we intentionally omit
    # remote-management.secret-key (config plaintext is bcrypt-hashed on boot
    # and unusable thereafter).
    # api-keys carries the machine-local client key once one is provisioned —
    # NOT for security (the bind is 127.0.0.1) but because CLIProxyAPI derives
    # the codex upstream's prompt_cache_key from the inbound API key; with
    # api-keys empty the ChatGPT/codex backend never serves prompt-cache reads
    # (#139 diagnostics 2026-07-02). generate() only READS the key file;
    # ensure_client_api_key() creates it in install()/upgrade()/refresh paths.
    if env is None:
        env = _machine_global_env()
    # auth-dir pins where OAuth tokens (auths/*.json) are written and read. Without
    # it CLIProxyAPI defaults to ./auths relative to the process cwd, so the
    # `dn proxy login` foreground process and the background service would write to
    # different places and not share credentials. Pin both to the harness data dir.
    auths_dir = paths.data_dir() / "auths"
    client_key = client_api_key()
    api_keys = f'api-keys:\n  - "{client_key}"\n' if client_key else "api-keys: []\n"
    base = (
        'host: "127.0.0.1"\n'
        f"port: {port}\n"
        f"{api_keys}"
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
    "drifted" warning that `dn proxy upgrade` could never clear.

    Only safe for callers whose os.environ never merges a project-local .env
    (e.g. the `dn proxy` CLI, which deliberately loads machine-global env
    only). The TUI's own drift check does NOT use this default — its
    os.environ is polluted by `paths.load_env(cwd)` before it ever runs, so it
    builds its own env explicitly from a pre-load_env shell snapshot instead
    (see harness/tui/app.py::HarnessTui._check_proxy_config_drift). Do not
    reuse this function's default from a process that loads a project .env.
    Empty-string values are treated as absent — an exported empty key must
    never mask the file's real key."""
    from dotenv import dotenv_values
    from harness import paths as _harness_paths

    merged = dict(dotenv_values(_harness_paths.config_dir() / ".env"))
    # Process env wins — matches load_env()'s override=False precedence — but an
    # EMPTY exported value is "absent", not an override: it must not mask a real
    # file key (the 2026-07-01 ten-reinstalls foot-gun).
    merged.update({k: v for k, v in os.environ.items() if v != ""})
    return {k: v for k, v in merged.items() if v}


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


def client_api_key() -> str | None:
    """The provisioned client API key, or None before first install/upgrade.
    Empty file = absent (same rule as _machine_global_env's empty-is-absent)."""
    p = paths.client_key_path()
    if p.exists():
        return p.read_text().strip() or None
    return None


def ensure_client_api_key() -> str:
    existing = client_api_key()
    if existing:
        return existing
    key = secrets.token_urlsafe(32)
    p = paths.client_key_path()
    p.write_text(key)
    os.chmod(p, 0o600)
    return key


def summarize(config_text: str) -> str:
    """One truthful line about what a generated config actually contains, for
    install()/upgrade()/refresh output. The 2026-07-01 failure survived ten
    reinstalls because install() said "running" about a keyless config."""
    if "openai-compatibility:" not in config_text:
        return ("config: NO upstream providers — no NEURALWATT_API_KEY in "
                "~/.config/harness/.env")
    n = config_text.count('\n      - name: "')     # model entries (6-space indent)
    return f"config: neuralwatt ({n} models)"


def masking_note(file_env: dict, process_env: dict) -> str | None:
    """Note when a non-empty shell export differs from a non-empty file key —
    the shell wins (documented precedence) but never silently."""
    f = (file_env.get("NEURALWATT_API_KEY") or "").strip()
    p = (process_env.get("NEURALWATT_API_KEY") or "").strip()
    if f and p and f != p:
        return "note: shell NEURALWATT_API_KEY overrides ~/.config/harness/.env"
    return None


def removal_note(old_text: str, new_text: str) -> str | None:
    """Name a provider that a regen dropped. The file is truth — removal is
    honored, never silent."""
    if '- name: "neuralwatt"' in old_text and '- name: "neuralwatt"' not in new_text:
        return "removed: neuralwatt (key no longer present)"
    return None
