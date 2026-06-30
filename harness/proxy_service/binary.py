from __future__ import annotations
import hashlib
import platform
from pathlib import Path
from harness.proxy_service import paths

# Asset naming verified: versioned .tar.gz (e.g., CLIProxyAPI_7.2.47_darwin_aarch64.tar.gz),
# aarch64/amd64 platform tokens, GitHub releases URL pattern.
# Pinned version: v7.2.47 (filename uses 7.2.47, release tag uses v7.2.47).
PINNED_VERSION = "v7.2.47"
_REPO = "router-for-me/CLIProxyAPI"


def platform_key() -> tuple[str, str]:
    """(os, arch) using CLIProxyAPI's release-asset tokens."""
    os_name = platform.system().lower()                       # 'darwin' | 'linux'
    m = platform.machine().lower()
    arch = {"arm64": "aarch64", "aarch64": "aarch64",
            "x86_64": "amd64", "amd64": "amd64"}.get(m, m)
    return os_name, arch


def asset_name(version: str, os_name: str, arch: str) -> str:
    ver = version.lstrip("v")                                  # filename has no leading v
    return f"CLIProxyAPI_{ver}_{os_name}_{arch}.tar.gz"


def asset_url(version: str, os_name: str, arch: str) -> str:
    name = asset_name(version, os_name, arch)
    return f"https://github.com/{_REPO}/releases/download/{version}/{name}"


def checksums_url(version: str) -> str:
    return f"https://github.com/{_REPO}/releases/download/{version}/checksums.txt"


def target_path() -> Path:
    return paths.data_dir() / "cli-proxy-api"


def verify_checksum(path: Path, expected_sha256: str) -> bool:
    h = hashlib.sha256(path.read_bytes()).hexdigest()
    return h == expected_sha256
