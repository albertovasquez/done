import io, tarfile, hashlib, pytest
from harness.proxy_service import download, binary


def _make_targz(inner_name="cli-proxy-api", content=b"#!/bin/echo fake-binary\n"):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo(inner_name); info.size = len(content)
        tf.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def _fake_urlopen_factory(targz_bytes, checksums_text, counter=None):
    def _open(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        is_checksums = url.endswith("checksums.txt")
        if counter is not None and not is_checksums:
            counter.append(url)                        # record TARBALL downloads only
        body = checksums_text.encode() if is_checksums else targz_bytes
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


def test_cache_hit_skips_redownload(tmp_path, monkeypatch):
    """A second install of the SAME pinned version must NOT re-download the
    tarball — the binary is already on disk and the recorded checksum matches."""
    monkeypatch.setattr(binary.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(binary.platform, "machine", lambda: "arm64")
    targz = _make_targz()
    sha = hashlib.sha256(targz).hexdigest()
    name = binary.asset_name("v7.2.47", "darwin", "aarch64")
    checksums = f"{sha}  {name}\n"
    dest = tmp_path / "cli-proxy-api"

    # 1st install: downloads (counter records one tarball fetch)
    downloads = []
    download.download_and_install(
        "v7.2.47", urlopen=_fake_urlopen_factory(targz, checksums, downloads), dest=lambda: dest)
    assert len(downloads) == 1
    assert (dest.parent / (dest.name + ".sha256")).read_text().strip() == sha

    # 2nd install (same version, binary present): NO tarball download
    downloads2 = []
    out = download.download_and_install(
        "v7.2.47", urlopen=_fake_urlopen_factory(targz, checksums, downloads2), dest=lambda: dest)
    assert out == dest and dest.exists()
    assert downloads2 == []                           # cache hit — skipped the 41MB fetch


def test_changed_checksum_forces_redownload(tmp_path, monkeypatch):
    """A new pinned version (different checksum) must re-download even though a
    binary exists — the stale stamp won't match."""
    monkeypatch.setattr(binary.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(binary.platform, "machine", lambda: "arm64")
    name = binary.asset_name("v7.2.47", "darwin", "aarch64")
    dest = tmp_path / "cli-proxy-api"

    old = _make_targz(content=b"OLD\n"); old_sha = hashlib.sha256(old).hexdigest()
    download.download_and_install(
        "v7.2.47", urlopen=_fake_urlopen_factory(old, f"{old_sha}  {name}\n"), dest=lambda: dest)

    new = _make_targz(content=b"NEW-VERSION\n"); new_sha = hashlib.sha256(new).hexdigest()
    downloads = []
    download.download_and_install(
        "v7.2.47", urlopen=_fake_urlopen_factory(new, f"{new_sha}  {name}\n", downloads), dest=lambda: dest)
    assert len(downloads) == 1                        # checksum changed → re-downloaded
    assert (dest.parent / (dest.name + ".sha256")).read_text().strip() == new_sha
