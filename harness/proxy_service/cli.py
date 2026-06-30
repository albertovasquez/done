from __future__ import annotations
from harness.proxy_service import lifecycle


def run(argv) -> int:
    cmd = argv[0] if argv else "status"
    fn = {
        "install": lifecycle.install, "uninstall": lifecycle.uninstall,
        "start": lifecycle.start, "stop": lifecycle.stop,
        "status": lifecycle.status, "upgrade": lifecycle.upgrade,
        "login": lambda: lifecycle.login(argv[1] if len(argv) > 1 else None),
    }.get(cmd)
    if fn is None:
        print(f"unknown: dn proxy {cmd}")
        return 2
    result = fn()
    print(result)
    return 0
