# Finish `dn proxy` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user authenticate Done against Codex (OAuth), Claude (OAuth), and GLM via NeuralWatt (API key) entirely through `dn proxy` commands — by finishing the stubbed bodies shipped in PR #195.

**Architecture:** Fill in `harness/proxy_service/` stubs. Correct `binary.py`'s asset facts; add `download.py` (fetch+verify+extract the real `.tar.gz`); make `lifecycle.install/upgrade/start/stop/uninstall` real; add a headless `login.run_cli_login` and wire `lifecycle.login`; extend `config_gen.generate` to append the NeuralWatt upstream when `NEURALWATT_API_KEY` is set.

**Tech Stack:** Python 3.11+, stdlib only for new code (`urllib.request`, `tarfile`, `hashlib`, `tempfile`, `shutil`, `webbrowser`, `subprocess`), pytest. External: CLIProxyAPI Go binary (downloaded).

## Global Constraints

- **Work in the `dn-proxy-finish` worktree** (`.worktrees/dn-proxy-finish`), NEVER primary `main`. Verify `pwd` + `git branch --show-current` + `git rev-parse HEAD` before edits; verify the commit parent after each commit. (AGENTS.md #1; two implementers leaked to main last time.)
- **No new third-party deps** — stdlib only for the new modules.
- **Security: never run an unverified binary.** `download_and_install` MUST checksum-verify against the release `checksums.txt` and abort on mismatch before the binary is placed at `target_path()`.
- **Injected I/O for testability** — network, browser, subprocess, sleep are parameters with real defaults; tests pass fakes. No real network/browser/sleep/subprocess in tests.
- **Verified facts (do NOT re-guess):**
  - Pinned version `v7.2.47` (tag). Asset = `CLIProxyAPI_<ver-no-v>_<os>_<arch>.tar.gz`.
  - Arch tokens `aarch64`/`amd64`; map `platform.machine()`: `arm64|aarch64→aarch64`, `x86_64|amd64→amd64`. OS `darwin`/`linux`.
  - Inner tarball: binary is top-level `cli-proxy-api` in the archive root.
  - `checksums.txt` lines: `<sha256>  <filename>`.
  - Login: `GET /v0/management/{anthropic,codex}-auth-url` → `{status,url,state}`; poll `GET /v0/management/get-auth-status?state=<state>`. `anthropic` = Claude's provider id.
  - `get-auth-status` terminal value is NOT documented — make the terminal check a configurable accepted-set (default `{"ok","success","completed","authenticated"}`); confirm live during impl, do not hardcode one guess.
- **Test command (from worktree root):** `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/ -q` (full suite can be slow; scope to the touched test files, the suite has one known-pre-existing cron `test_service_launchd` failure unrelated to this work).
- **Real ripgrep:** `/opt/homebrew/bin/rg` (shell aliases `rg`→`grep`).
- **Commit footer:** `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

## Phase 1 — Correct the binary facts + download

### Task 1: Correct `binary.py` asset naming

**Files:**
- Modify: `harness/proxy_service/binary.py`
- Test: `tests/test_proxy_binary.py` (extend existing)

**Interfaces:**
- Produces: `binary.platform_key() -> tuple[str,str]` (os, arch), `binary.asset_name(version) -> str`, `binary.asset_url(version) -> str`, `binary.checksums_url(version) -> str`. `PINNED_VERSION="v7.2.47"`. `target_path()`/`verify_checksum()` unchanged.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_proxy_binary.py — add
from harness.proxy_service import binary


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
```

- [ ] **Step 2: Run to verify they fail**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_proxy_binary.py -q`
Expected: FAIL (old `platform_key` returns a string `"darwin-arm64"`; no `asset_name`/`checksums_url`).

- [ ] **Step 3: Rewrite the helpers in `binary.py`**

```python
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
```
Update the `# OPEN ITEM` comment at the top: replace with a note that the asset
naming is verified (versioned `.tar.gz`, `aarch64`/`amd64`, top-level
`cli-proxy-api`), pin `v7.2.47`.

- [ ] **Step 4: Run to verify pass**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_proxy_binary.py -q`
Expected: PASS (existing checksum test + 3 new).

- [ ] **Step 5: Commit**

```bash
git add harness/proxy_service/binary.py tests/test_proxy_binary.py
git commit -m "fix(proxy): correct CLIProxyAPI asset naming (versioned tarball, aarch64/amd64)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task 2: `download.py` — fetch, verify, extract

**Files:**
- Create: `harness/proxy_service/download.py`
- Test: `tests/test_proxy_download.py` (create)

**Interfaces:**
- Consumes: `binary` (Task 1), `paths`.
- Produces: `download.fetch_checksums(version, *, urlopen=...) -> dict[str,str]`, `download.download_and_install(version, *, urlopen=..., dest=binary.target_path) -> Path`. Raises `ChecksumMismatch` (a module exception) on bad checksum — installs nothing.

- [ ] **Step 1: Write the failing tests** (injected fakes; in-memory tar.gz)

```python
# tests/test_proxy_download.py
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_proxy_download.py -q`
Expected: FAIL (`ModuleNotFoundError: harness.proxy_service.download`).

- [ ] **Step 3: Implement `download.py`**

```python
# harness/proxy_service/download.py
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
        sha, _, name = line.partition("  ")            # '<sha>␣␣<file>'
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
        target = dest()
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(extracted), str(target))
        os.chmod(target, 0o755)
        return target
```

- [ ] **Step 4: Run to verify pass**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_proxy_download.py -q`
Expected: PASS (3 tests) — including the mismatch-aborts case.

- [ ] **Step 5: Commit**

```bash
git add harness/proxy_service/download.py tests/test_proxy_download.py
git commit -m "feat(proxy): binary download — fetch checksums, verify, extract tarball

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase 2 — Lifecycle bodies

### Task 3: `start` / `stop` service control

**Files:**
- Modify: `harness/proxy_service/lifecycle.py` (replace `start`/`stop` stubs)
- Test: `tests/test_proxy_lifecycle.py` (create)

**Interfaces:**
- Produces: `lifecycle.start() -> str`, `lifecycle.stop() -> str`. Each shells out via a module-level `_run(argv) -> tuple[int,str]` seam (so tests monkeypatch it), platform-dispatched.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_proxy_lifecycle.py
from harness.proxy_service import lifecycle


def test_start_uses_service_manager(monkeypatch):
    calls = []
    monkeypatch.setattr(lifecycle, "_run", lambda argv: (calls.append(argv) or (0, "")))
    monkeypatch.setattr(lifecycle.platform, "system", lambda: "Darwin")
    out = lifecycle.start()
    assert calls and any("launchctl" in a[0] for a in calls)
    assert "start" in out.lower() or "started" in out.lower()


def test_stop_reports_failure_gracefully(monkeypatch):
    monkeypatch.setattr(lifecycle, "_run", lambda argv: (1, "boom"))
    monkeypatch.setattr(lifecycle.platform, "system", lambda: "Linux")
    out = lifecycle.stop()
    assert "boom" in out or "fail" in out.lower()
```

- [ ] **Step 2: Run to verify they fail**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_proxy_lifecycle.py -q`
Expected: FAIL (stubs return "not yet implemented"; no `_run`).

- [ ] **Step 3: Implement `_run`, `start`, `stop`**

Add a `_run` seam and platform-dispatched start/stop to `lifecycle.py`. Use the
labels from the service modules (`service_launchd.LABEL` / `service_systemd.LABEL`).

```python
import subprocess

def _run(argv: list[str]) -> tuple[int, str]:
    p = subprocess.run(argv, capture_output=True, text=True)
    return p.returncode, (p.stderr or p.stdout).strip()


def start() -> str:
    sysname = platform.system()
    if sysname == "Darwin":
        from harness.proxy_service import service_launchd as s
        rc, err = _run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{s.LABEL}"])
    elif sysname == "Linux":
        from harness.proxy_service import service_systemd as s
        rc, err = _run(["systemctl", "--user", "start", f"{s.LABEL}.service"])
    else:
        return f"unsupported platform: {sysname}"
    return "CLIProxyAPI started" if rc == 0 else f"start failed: {err}"


def stop() -> str:
    sysname = platform.system()
    if sysname == "Darwin":
        from harness.proxy_service import service_launchd as s
        rc, err = _run(["launchctl", "bootout", f"gui/{os.getuid()}/{s.LABEL}"])
    elif sysname == "Linux":
        from harness.proxy_service import service_systemd as s
        rc, err = _run(["systemctl", "--user", "stop", f"{s.LABEL}.service"])
    else:
        return f"unsupported platform: {sysname}"
    return "CLIProxyAPI stopped" if rc == 0 else f"stop failed: {err}"
```
Add `import os` at the top if not present.

- [ ] **Step 4: Run to verify pass**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_proxy_lifecycle.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/proxy_service/lifecycle.py tests/test_proxy_lifecycle.py
git commit -m "feat(proxy): dn proxy start/stop service control

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task 4: Real `install` + `upgrade` + `uninstall`

**Files:**
- Modify: `harness/proxy_service/lifecycle.py` (replace `install` download-skip + `upgrade`/`uninstall` stubs)
- Test: extend `tests/test_proxy_lifecycle.py`

**Interfaces:**
- Consumes: `download.download_and_install` (Task 2), `_register_os_service` + `start` (existing/Task 3), `management.is_ready`.
- Produces: `install()` downloads→config→register→start→readiness; `upgrade()` re-downloads + restarts; `uninstall()` stop + deregister + remove data dir.

- [ ] **Step 1: Write the failing tests** (download + service mocked)

```python
# extend tests/test_proxy_lifecycle.py
from harness.proxy_service import lifecycle, binary


def test_install_downloads_then_registers_and_starts(monkeypatch, tmp_path):
    seq = []
    monkeypatch.setattr(lifecycle.download, "download_and_install",
                        lambda v: (seq.append("download") or tmp_path / "cli-proxy-api"))
    (tmp_path / "cli-proxy-api").write_text("x")
    monkeypatch.setattr(lifecycle, "_register_os_service",
                        lambda *a: (seq.append("register") or "registered"))
    monkeypatch.setattr(lifecycle, "start", lambda: (seq.append("start") or "started"))
    monkeypatch.setattr(lifecycle.management, "is_ready", lambda pw: True)
    monkeypatch.setattr(lifecycle.binary, "target_path", lambda: tmp_path / "cli-proxy-api")
    out = lifecycle.install()
    assert seq == ["download", "register", "start"]
    assert "running" in out.lower() or "started" in out.lower()


def test_uninstall_stops_and_removes_data_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(lifecycle, "stop", lambda: "stopped")
    monkeypatch.setattr(lifecycle, "_deregister_os_service", lambda: "deregistered")
    monkeypatch.setattr(lifecycle.paths, "data_dir", lambda: tmp_path)
    (tmp_path / "config.yaml").write_text("x")
    out = lifecycle.uninstall()
    assert not (tmp_path / "config.yaml").exists()
    assert "removed" in out.lower() or "uninstall" in out.lower()
```

- [ ] **Step 2: Run to verify they fail**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_proxy_lifecycle.py -q`
Expected: FAIL.

- [ ] **Step 3: Rewrite `install`, implement `upgrade`/`uninstall`/`_deregister_os_service`**

Replace `install()` so Step 2 actually downloads (call `download.download_and_install(binary_mod.PINNED_VERSION)` — import `download` and `binary` at module top), then `_register_os_service`, then `start()`, then a brief `management.is_ready` readiness poll (a few attempts). `upgrade()` = re-download `PINNED_VERSION` + `stop()`+`start()`. `uninstall()` = `stop()` + `_deregister_os_service()` (bootout/disable + remove the unit file) + `shutil.rmtree(paths.data_dir())`; note in the output that downloaded auth tokens are removed too. Keep every shell-out wrapped so failures return a string, never raise.

(Reference signatures: `_register_os_service(binary, config_path, mgmt_password)` exists; add `_deregister_os_service()` mirroring the register launchd/systemd file-removal + bootout/disable.)

- [ ] **Step 4: Run to verify pass**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_proxy_lifecycle.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/proxy_service/lifecycle.py tests/test_proxy_lifecycle.py
git commit -m "feat(proxy): real dn proxy install/upgrade/uninstall

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase 3 — Login

### Task 5: `login.run_cli_login` (headless driver)

**Files:**
- Modify: `harness/proxy_service/login.py` (add `run_cli_login`)
- Test: `tests/test_proxy_login.py` (extend)

**Interfaces:**
- Consumes: `management.auth_url`, `management.poll_auth_status`.
- Produces: `login.run_cli_login(provider, password, *, open_browser=webbrowser.open, poll=management.poll_auth_status, sleep=time.sleep, out=print, attempts=60, terminal=frozenset({"ok","success","completed","authenticated"})) -> bool`.

- [ ] **Step 1: Write the failing tests**

```python
# extend tests/test_proxy_login.py
from harness.proxy_service import login, management


def test_run_cli_login_browser_success(monkeypatch):
    monkeypatch.setattr(management, "auth_url", lambda p, pw, base=None: ("https://x", "st"))
    polls = iter(["pending", "pending", "ok"])
    out = []
    ok = login.run_cli_login("codex", "pw",
        open_browser=lambda u: True, poll=lambda s, pw, base=None: next(polls),
        sleep=lambda s: None, out=out.append, attempts=5)
    assert ok is True
    assert any("waiting" in m.lower() or "browser" in m.lower() for m in out)


def test_run_cli_login_headless_prints_url(monkeypatch):
    monkeypatch.setattr(management, "auth_url", lambda p, pw, base=None: ("https://X", "st"))
    out = []
    ok = login.run_cli_login("codex", "pw",
        open_browser=lambda u: False,                 # no browser
        poll=lambda s, pw, base=None: "ok", sleep=lambda s: None, out=out.append, attempts=2)
    assert ok is True
    assert any("https://X" in m for m in out)         # URL printed for manual open


def test_run_cli_login_timeout_returns_false(monkeypatch):
    monkeypatch.setattr(management, "auth_url", lambda p, pw, base=None: ("https://x", "st"))
    ok = login.run_cli_login("codex", "pw",
        open_browser=lambda u: True, poll=lambda s, pw, base=None: "pending",
        sleep=lambda s: None, out=lambda m: None, attempts=3)
    assert ok is False
```

- [ ] **Step 2: Run to verify they fail**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_proxy_login.py -q`
Expected: FAIL (no `run_cli_login`).

- [ ] **Step 3: Implement `run_cli_login`**

```python
import time, webbrowser
from harness.proxy_service import management

def run_cli_login(provider, password, *, open_browser=webbrowser.open,
                  poll=management.poll_auth_status, sleep=time.sleep, out=print,
                  attempts=60, terminal=frozenset({"ok","success","completed","authenticated"})):
    url, state = management.auth_url(provider, password)
    if open_browser(url):
        out("opened browser — waiting for sign-in…")
    else:
        out(f"open this URL to sign in:\n  {url}\nwaiting for sign-in…")
    for _ in range(attempts):
        status = poll(state, password)
        if status in terminal:
            out(f"✓ {provider} authenticated")
            return True
        sleep(2)
    out(f"sign-in didn't complete — re-run `dn proxy login {provider}`")
    return False
```

- [ ] **Step 4: Run to verify pass**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_proxy_login.py -q`
Expected: PASS (existing `start` tests + 3 new).

- [ ] **Step 5: Commit**

```bash
git add harness/proxy_service/login.py tests/test_proxy_login.py
git commit -m "feat(proxy): headless CLI login driver (browser + poll + fallback)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task 6: Wire `lifecycle.login` (auto-start + run_cli_login)

**Files:**
- Modify: `harness/proxy_service/lifecycle.py` (replace `login` stub)
- Test: extend `tests/test_proxy_lifecycle.py`

**Interfaces:**
- Consumes: `management.is_ready`, `start` (Task 3), `login.run_cli_login` (Task 5), `config_gen.ensure_management_password`.
- Produces: `lifecycle.login(provider)` — preflight is_ready→start if down→run_cli_login; validates provider against the browser set `{"anthropic","codex"}`.

- [ ] **Step 1: Write the failing test**

```python
# extend tests/test_proxy_lifecycle.py
def test_login_autostarts_then_runs(monkeypatch):
    seq = []
    monkeypatch.setattr(lifecycle.management, "is_ready",
                        lambda pw: (seq.append("check") or len(seq) > 1))  # False first, True after start
    monkeypatch.setattr(lifecycle, "start", lambda: seq.append("start") or "started")
    import harness.proxy_service.login as login_mod
    monkeypatch.setattr(login_mod, "run_cli_login", lambda *a, **k: seq.append("login") or True)
    out = lifecycle.login("codex")
    assert "start" in seq and "login" in seq
    assert "codex" in out.lower() or "authenticated" in out.lower()


def test_login_rejects_unknown_provider():
    out = lifecycle.login("banana")
    assert "unknown" in out.lower() or "choose" in out.lower()
```

- [ ] **Step 2: Run to verify it fails**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_proxy_lifecycle.py -q`
Expected: FAIL (stub returns "not yet implemented").

- [ ] **Step 3: Implement `lifecycle.login`**

```python
_LOGIN_PROVIDERS = {"anthropic", "codex"}   # browser-OAuth set in scope (claude=anthropic)

def login(provider: str | None = None) -> str:
    if provider is None or provider not in _LOGIN_PROVIDERS:
        return f"dn proxy login: choose a provider from: {', '.join(sorted(_LOGIN_PROVIDERS))}"
    pw = config_gen.ensure_management_password()
    if not management.is_ready(pw):
        start()
        # brief readiness wait
        import time as _t
        for _ in range(10):
            if management.is_ready(pw):
                break
            _t.sleep(1)
    from harness.proxy_service import login as login_mod
    ok = login_mod.run_cli_login(provider, pw)
    return f"{provider}: authenticated" if ok else f"{provider}: sign-in not completed"
```

- [ ] **Step 4: Run to verify pass**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_proxy_lifecycle.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/proxy_service/lifecycle.py tests/test_proxy_lifecycle.py
git commit -m "feat(proxy): dn proxy login — auto-start preflight + headless driver

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase 4 — GLM via NeuralWatt

### Task 7: `config_gen.generate` appends NeuralWatt upstream when key is set

**Files:**
- Modify: `harness/proxy_service/config_gen.py`
- Test: `tests/test_proxy_config_gen.py` (extend)

**Interfaces:**
- Produces: `generate(port=8317, *, env=os.environ) -> str` — appends an `openai-compatibility` block for NeuralWatt when `env.get("NEURALWATT_API_KEY")` is set; omits it otherwise.

- [ ] **Step 1: Write the failing tests**

```python
# extend tests/test_proxy_config_gen.py
from harness.proxy_service import config_gen


def test_generate_includes_neuralwatt_when_key_set():
    y = config_gen.generate(env={"NEURALWATT_API_KEY": "nw-123"})
    assert "openai-compatibility" in y
    assert "api.neuralwatt.com/v1" in y
    assert "alias: \"glm\"" in y or "alias: glm" in y


def test_generate_omits_neuralwatt_when_key_absent():
    y = config_gen.generate(env={})
    assert "openai-compatibility" not in y
```

- [ ] **Step 2: Run to verify they fail**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_proxy_config_gen.py -q`
Expected: FAIL (`generate` has no `env` kwarg; never emits the block).

- [ ] **Step 3: Extend `generate`**

```python
_NEURALWATT_GLM_MODEL = "zai-org/GLM-4.6"   # OPEN ITEM: confirm exact id via NeuralWatt /v1/models

def generate(port: int = 8317, *, env=None) -> str:
    if env is None:
        env = os.environ
    base = (
        'host: "127.0.0.1"\n'
        f"port: {port}\n"
        "api-keys: []\n"
        "remote-management:\n"
        "  allow-remote: false\n"
    )
    nw_key = env.get("NEURALWATT_API_KEY")
    if nw_key:
        base += (
            "openai-compatibility:\n"
            '  - name: "neuralwatt"\n'
            '    base-url: "https://api.neuralwatt.com/v1"\n'
            "    api-key-entries:\n"
            f'      - api-key: "{nw_key}"\n'
            "    models:\n"
            f'      - name: "{_NEURALWATT_GLM_MODEL}"\n'
            '        alias: "glm"\n'
        )
    return base
```

- [ ] **Step 4: Run to verify pass**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_proxy_config_gen.py -q`
Expected: PASS (existing config/password tests + 2 new).

- [ ] **Step 5: Commit**

```bash
git add harness/proxy_service/config_gen.py tests/test_proxy_config_gen.py
git commit -m "feat(proxy): config_gen appends NeuralWatt->GLM upstream when key set

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase 5 — Docs

### Task 8: Update `docs/proxy.md` for the now-real flow

**Files:**
- Modify: `docs/proxy.md`

- [ ] **Step 1:** Replace the "coming in a follow-up" caveats with the real flow:
  `dn proxy install` (downloads + verifies + registers + starts), `dn proxy login codex`,
  `dn proxy login claude` (note: provider id `anthropic` accepted as `claude`'s OAuth),
  set `NEURALWATT_API_KEY` then `dn proxy install` (or `upgrade`) to pick up GLM,
  `dn proxy status` to confirm. Keep the existing NeuralWatt YAML block; align it
  with the `_NEURALWATT_GLM_MODEL` value (note it's the value to confirm from
  NeuralWatt `/v1/models`).
- [ ] **Step 2: Commit**

```bash
git add docs/proxy.md
git commit -m "docs(proxy): document the real install/login/GLM flow

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Final verification

- [ ] `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/ -q -k "proxy or download or lifecycle or login or config_gen or binary"` — all green (the unrelated cron `test_service_launchd` may still fail; confirm it's the same pre-existing one).
- [ ] `git -C /Users/alberto/Work/Quiubo/harness status -s` — primary main clean (no leak).
- [ ] **Live smoke (optional, needs network + a provider login):** `dn proxy install` actually downloads + verifies the real `v7.2.47` binary and starts the service; `dn proxy status` shows it running. Confirm the `get-auth-status` terminal value against a real `dn proxy login codex` and adjust `run_cli_login`'s `terminal` set if needed.
- [ ] Resolve remaining Open Items: live `get-auth-status` terminal value; exact NeuralWatt GLM model id.
