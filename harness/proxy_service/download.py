from __future__ import annotations
import hashlib
import os
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from harness.proxy_service import binary


class ChecksumMismatch(Exception):
    pass


def _default_urlopen(url, timeout=60):
    return urllib.request.urlopen(url, timeout=timeout)   # noqa: S310


def fetch_checksums(version: str, *, urlopen=_default_urlopen) -> dict:
    """Parse the release checksums.txt → {filename: sha256}."""
    with urlopen(binary.checksums_url(version)) as resp:
        text = resp.read().decode()
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        sha, _, name = line.partition("  ")            # '<sha>  <file>'
        if name:
            out[name.strip()] = sha.strip()
    return out


def download_and_install(version: str, *, urlopen=_default_urlopen,
                         dest=binary.target_path) -> Path:
    os_name, arch = binary.platform_key()
    name = binary.asset_name(version, os_name, arch)
    expected = fetch_checksums(version, urlopen=urlopen).get(name)
    if not expected:
        raise ChecksumMismatch(f"no checksum for {name} in release {version}")

    # Cache hit: if the binary already on disk was extracted from a tarball with
    # THIS release's checksum, skip the ~41MB download. We record the tarball
    # checksum in a `.sha256` sidecar on install (the extracted binary's own hash
    # differs from the tarball's, so we can't recompute it from the binary alone).
    # This still honors the security gate — the sidecar is only written after a
    # fresh download passed checksum verification.
    target = dest()
    stamp = target.with_name(target.name + ".sha256")
    if target.exists() and stamp.exists() and stamp.read_text().strip() == expected:
        return target

    with tempfile.TemporaryDirectory() as td:
        tgz = Path(td) / name
        with urlopen(binary.asset_url(version, os_name, arch)) as resp:
            tgz.write_bytes(resp.read())
        actual = hashlib.sha256(tgz.read_bytes()).hexdigest()
        if actual != expected:
            raise ChecksumMismatch(f"{name}: expected {expected}, got {actual}")
        # extract the top-level `cli-proxy-api` binary
        with tarfile.open(tgz, "r:gz") as tf:
            member = tf.getmember("cli-proxy-api")
            extracted = Path(td) / "cli-proxy-api"
            with tf.extractfile(member) as src, open(extracted, "wb") as dst:
                shutil.copyfileobj(src, dst)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(extracted), str(target))
        os.chmod(target, 0o755)
        stamp.write_text(expected)        # record the verified tarball checksum
        return target
