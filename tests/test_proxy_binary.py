import hashlib
from pathlib import Path
from harness.proxy_service import binary


def test_pinned_version_is_set():
    assert binary.PINNED_VERSION.startswith("v")


def test_verify_checksum_matches(tmp_path):
    f = tmp_path / "cli-proxy-api"
    f.write_bytes(b"hello-binary")
    digest = hashlib.sha256(b"hello-binary").hexdigest()
    assert binary.verify_checksum(f, digest) is True
    assert binary.verify_checksum(f, "0" * 64) is False


def test_asset_url_includes_version_and_platform():
    url = binary.asset_url("v7.2.47", "darwin", "aarch64")
    assert "v7.2.47" in url and "darwin" in url and "aarch64" in url


def test_platform_key_maps_arch_tokens(monkeypatch):
    monkeypatch.setattr(binary.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(binary.platform, "machine", lambda: "arm64")
    assert binary.platform_key() == ("darwin", "aarch64")
    monkeypatch.setattr(binary.platform, "machine", lambda: "x86_64")
    assert binary.platform_key() == ("darwin", "amd64")
    monkeypatch.setattr(binary.platform, "system", lambda: "Linux")
    monkeypatch.setattr(binary.platform, "machine", lambda: "aarch64")
    assert binary.platform_key() == ("linux", "aarch64")


def test_asset_name_is_versioned_tarball():
    # version without leading v in the filename; tag keeps the v in the URL
    assert binary.asset_name("v7.2.47", "darwin", "aarch64") == "CLIProxyAPI_7.2.47_darwin_aarch64.tar.gz"


def test_asset_url_and_checksums_url_use_tag():
    u = binary.asset_url("v7.2.47", "darwin", "aarch64")
    assert u == ("https://github.com/router-for-me/CLIProxyAPI/releases/download/"
                 "v7.2.47/CLIProxyAPI_7.2.47_darwin_aarch64.tar.gz")
    c = binary.checksums_url("v7.2.47")
    assert c == "https://github.com/router-for-me/CLIProxyAPI/releases/download/v7.2.47/checksums.txt"
