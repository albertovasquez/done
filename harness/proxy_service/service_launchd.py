from __future__ import annotations
import plistlib

LABEL = "com.quiubo.done.proxy"


def build_plist(binary: str, config_path: str, mgmt_password: str, label: str) -> bytes:
    doc = {
        "Label": label,
        "ProgramArguments": [binary, "--config", config_path],
        "EnvironmentVariables": {"MANAGEMENT_PASSWORD": mgmt_password},
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,        # mirror cron backend; avoid respawn hot-loop
        "ProcessType": "Background",
    }
    return plistlib.dumps(doc)
