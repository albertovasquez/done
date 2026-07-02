from __future__ import annotations
from harness.proxy_service import lifecycle


def run(argv) -> int:
    # `dn proxy …` returns from tui_main BEFORE its paths.load_env() runs, so load
    # ~/.config/harness/.env here — otherwise config_gen never sees a key that
    # lives only in that file (e.g. NEURALWATT_API_KEY) and glm/qwen silently drop
    # out of the generated config. No project_dir: proxy install is machine-global,
    # so it must not pick up a per-project ./.env. override=False keeps shell > .env.
    from harness import paths
    paths.load_env()

    cmd = argv[0] if argv else "status"
    fn = {
        "install": lifecycle.install, "uninstall": lifecycle.uninstall,
        "start": lifecycle.start, "stop": lifecycle.stop,
        "status": lifecycle.status, "upgrade": lifecycle.upgrade,
        "refresh": lifecycle.refresh_config,
        "login": lambda: lifecycle.login(argv[1] if len(argv) > 1 else None),
    }.get(cmd)
    if fn is None:
        print(f"unknown: dn proxy {cmd}")
        return 2
    result = fn()
    print(result)
    return 0
