from __future__ import annotations

LABEL = "com.quiubo.done.proxy"


def build_unit(binary: str, config_path: str, mgmt_password: str, label: str) -> str:
    return (
        "[Unit]\n"
        "Description=Done CLIProxyAPI model proxy\n"
        "After=network-online.target\n\n"
        "[Service]\n"
        f"ExecStart={binary} --config {config_path}\n"
        f"Environment=MANAGEMENT_PASSWORD={mgmt_password}\n"
        "Restart=always\n"
        "RestartSec=5\n\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )
