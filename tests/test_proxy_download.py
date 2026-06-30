import io, tarfile, hashlib, pytest
from harness.proxy_service import download, binary


def _make_targz(inner_name="cli-proxy-api", content=b"#!/bin/echo fake-binary\n"):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo(inner_name); info.size = len(content)
        tf.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def _fake_urlopen_factory(targz_bytes, checksums_text):
    def _open(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        body = checksums_text.encode() if url.endswith("checksums.txt") else targz_bytes
        return io.BytesIO(body)
    return _open


def test_fetch_checksums_parses_lines():
    text = "abc123  CLIProxyAPI_7.2.47_darwin_aarch64.tar.gz\ndef456  other.tar.gz\n"
    d = download.fetch_checksums("v7.2.47", urlopen=_fake_urlopen_factory(b"", text))
    assert d["CLIProxyAPI_7.2.47_darwin_aarch64.tar.gz"] == "abc123"


def test_download_and_install_verifies_and_extracts(tmp_path, monkeypatch):
    monkeypatch.setattr(binary.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(binary.platform, "machine", lambda: "arm64")
    targz = _make_targz()
    sha = hashlib.sha256(targz).hexdigest()
    name = binary.asset_name("v7.2.47", "darwin", "aarch64")
    checksums = f"{sha}  {name}\n"
    dest = tmp_path / "cli-proxy-api"
    out = download.download_and_install(
        "v7.2.47", urlopen=_fake_urlopen_factory(targz, checksums), dest=lambda: dest)
    assert out == dest and dest.exists()
    assert dest.stat().st_mode & 0o111            # executable bit set


def test_download_and_install_aborts_on_checksum_mismatch(tmp_path, monkeypatch):
    monkeypatch.setattr(binary.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(binary.platform, "machine", lambda: "arm64")
    targz = _make_targz()
    name = binary.asset_name("v7.2.47", "darwin", "aarch64")
    checksums = f"{'0'*64}  {name}\n"               # wrong sha
    dest = tmp_path / "cli-proxy-api"
    with pytest.raises(download.ChecksumMismatch):
        download.download_and_install(
            "v7.2.47", urlopen=_fake_urlopen_factory(targz, checksums), dest=lambda: dest)
    assert not dest.exists()                         # nothing installed
