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
    url = binary.asset_url("v7.2.47", "darwin-arm64")
    assert "v7.2.47" in url and "darwin-arm64" in url
