from __future__ import annotations
import hashlib
import platform
from pathlib import Path
from harness.proxy_service import paths

# OPEN ITEM (spec #2): confirm exact version + asset URL pattern + checksum source
# before shipping. Placeholder pin chosen from the latest observed release.
PINNED_VERSION = "v7.2.47"
_REPO = "router-for-me/CLIProxyAPI"


def platform_key() -> str:
    sysname = platform.system().lower()       # 'darwin' | 'linux'
    arch = platform.machine().lower()         # 'arm64' | 'x86_64'
    arch = {"x86_64": "amd64", "aarch64": "arm64"}.get(arch, arch)
    return f"{sysname}-{arch}"


def asset_url(version: str, platform_key: str) -> str:
    # CONFIRM the real asset naming on the releases page before relying on this.
    return (f"https://github.com/{_REPO}/releases/download/{version}/"
            f"cli-proxy-api-{platform_key}")


def target_path() -> Path:
    return paths.data_dir() / "cli-proxy-api"


def verify_checksum(path: Path, expected_sha256: str) -> bool:
    h = hashlib.sha256(path.read_bytes()).hexdigest()
    return h == expected_sha256
