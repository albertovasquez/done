# CLIProxyAPI Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace VibeProxy with CLIProxyAPI as a harness-managed, first-class model proxy — installed, run as an OS service, and authenticated through `dn proxy` commands.

**Architecture:** A thin `harness/proxy.py` connection seam (renamed from `vibeproxy.py`, `os`-only, no litellm) fronts CLIProxyAPI on `localhost:8317`. A new `harness/proxy_service/` package owns the binary download + OS-service lifecycle (mirroring `harness/jobs/` cron service). `dn proxy` subcommands route through `tui_main.main()` to a new CLI module. A capability-split login modal drives provider OAuth. The model-name env migration (`VIBEPROXY_* → PROXY_*`) is centralized so the precedence ladder honors both names everywhere it is consulted.

**Tech Stack:** Python 3.11+, Textual (TUI), litellm (via callers, not the seam), launchd/systemd (OS service), pytest. CLIProxyAPI is an external Go binary (MIT).

## Global Constraints

- **Always work in the `cliproxy-integration` worktree** (`.worktrees/cliproxy-integration`), never the primary checkout. Verify `pwd` + `git branch --show-current` before edits/commits. (AGENTS.md #1)
- **`harness/proxy.py` must import only `os`** — never litellm (it costs ~1s at import and sits on the startup path; callers own the litellm call).
- **Single model home:** the persisted worker model lives only in `done.conf [agents.<id>]`. Do NOT add a `done.conf [proxy].default model`. The `[proxy]` section holds infrastructure only (`port`, `version`).
- **Env precedence when both names set:** `PROXY_MODEL` wins over `VIBEPROXY_MODEL`. Empty strings count as absent.
- **Management secret:** never rely on `remote-management.secret-key` in config (it gets bcrypt-hashed on boot). Use the in-memory `MANAGEMENT_PASSWORD` env var injected into the service.
- **Test command (from worktree root):** `.venv/bin/python -m pytest tests/ -q`
- **Real ripgrep** (the shell aliases `rg`→`grep`): use `/opt/homebrew/bin/rg` for searches.
- **Commit message footer:** `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

## Phase 1 — Centralize env-name handling (prerequisite for everything)

This phase makes the `VIBEPROXY_* → PROXY_*` migration *correct*. It must land
before any doc tells users to use `PROXY_MODEL`. It touches the model-resolution
path in ~8 modules + tests. No behavior change for existing `VIBEPROXY_*` users.

### Task 1: Add centralized env-name helpers to the seam

**Files:**
- Modify: `harness/vibeproxy.py` (still named this until Task 6 renames it)
- Test: `tests/test_proxy_env_names.py` (create)

**Interfaces:**
- Produces: `vibeproxy.model_set_in(env) -> bool`, `vibeproxy.model_value(env) -> str | None`, and dual-name `base_url()`/`api_key()`/`default_model()`. `_MODEL_ENVS = ("PROXY_MODEL", "VIBEPROXY_MODEL")` (PROXY_ first = wins).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_proxy_env_names.py
from harness import vibeproxy


def test_model_set_in_detects_either_name():
    assert vibeproxy.model_set_in({"PROXY_MODEL": "x"}) is True
    assert vibeproxy.model_set_in({"VIBEPROXY_MODEL": "x"}) is True
    assert vibeproxy.model_set_in({"OTHER": "x"}) is False


def test_model_value_prefers_proxy_over_vibeproxy():
    assert vibeproxy.model_value({"PROXY_MODEL": "new", "VIBEPROXY_MODEL": "old"}) == "new"
    assert vibeproxy.model_value({"VIBEPROXY_MODEL": "old"}) == "old"
    assert vibeproxy.model_value({}) is None


def test_model_value_treats_empty_as_absent():
    assert vibeproxy.model_value({"PROXY_MODEL": "", "VIBEPROXY_MODEL": "old"}) == "old"


def test_default_model_reads_proxy_first(monkeypatch):
    monkeypatch.setenv("PROXY_MODEL", "p")
    monkeypatch.setenv("VIBEPROXY_MODEL", "v")
    assert vibeproxy.default_model() == "p"


def test_base_url_and_api_key_dual_name(monkeypatch):
    monkeypatch.delenv("VIBEPROXY_BASE_URL", raising=False)
    monkeypatch.setenv("PROXY_BASE_URL", "http://x/v1")
    assert vibeproxy.base_url() == "http://x/v1"
    monkeypatch.delenv("PROXY_BASE_URL", raising=False)
    monkeypatch.setenv("VIBEPROXY_BASE_URL", "http://y/v1")
    assert vibeproxy.base_url() == "http://y/v1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_proxy_env_names.py -q`
Expected: FAIL (`AttributeError: module 'harness.vibeproxy' has no attribute 'model_set_in'`).

- [ ] **Step 3: Implement the helpers**

Replace the env-reading bodies in `harness/vibeproxy.py` (keep the `os`-only rule, keep `DEFAULT_MODEL`/`_DEFAULT_BASE_URL`/`_DEFAULT_API_KEY`):

```python
# precedence: PROXY_* is the new canonical name and wins over the legacy name.
_MODEL_ENVS = ("PROXY_MODEL", "VIBEPROXY_MODEL")


def model_set_in(env) -> bool:
    """True if a worker-model env var (either name) is present AND non-empty."""
    return any(env.get(k) for k in _MODEL_ENVS)


def model_value(env):
    """The worker-model value under either name, PROXY_MODEL first. None if absent."""
    for k in _MODEL_ENVS:
        v = env.get(k)
        if v:
            return v
    return None


def base_url() -> str:
    return (os.getenv("PROXY_BASE_URL") or os.getenv("VIBEPROXY_BASE_URL")
            or _DEFAULT_BASE_URL)


def api_key() -> str:
    return (os.getenv("PROXY_API_KEY") or os.getenv("VIBEPROXY_API_KEY")
            or _DEFAULT_API_KEY)


def default_model() -> str:
    return model_value(os.environ) or DEFAULT_MODEL
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_proxy_env_names.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Run the full suite to confirm no regression**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS (existing tests still green — dual-name fallback preserves old behavior).

- [ ] **Step 6: Commit**

```bash
git add harness/vibeproxy.py tests/test_proxy_env_names.py
git commit -m "feat(proxy): centralized dual-name env helpers (PROXY_*/VIBEPROXY_*)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task 2: Route the resolution path through the helpers

**Files:**
- Modify: `harness/tui_main.py:112`, `harness/acp_main.py:117,141,168`
- Modify: `harness/jobs/executor.py:207-208`, `harness/jobs/cron_main.py:58`
- Test: `tests/test_proxy_model_precedence.py` (create)

**Interfaces:**
- Consumes: `vibeproxy.model_set_in(env)`, `vibeproxy.model_value(env)` from Task 1.
- The `resolve_session_model(...)` / `model_resolve.resolve_model(...)` signatures are UNCHANGED — only the *values fed in* change from literal-`VIBEPROXY_MODEL` reads to the helpers.

- [ ] **Step 1: Write the failing test** (integration of the snapshot logic)

```python
# tests/test_proxy_model_precedence.py
from harness import vibeproxy
from harness.persona_sessions import resolve_session_model


def _resolve(env, persisted=None, backend="vibeproxy"):
    """Mimic the acp_main snapshot: shell_set_model + shell_env from the helpers."""
    shell_set = vibeproxy.model_set_in(env)
    val = vibeproxy.model_value(env)
    # acp_main passes the post-load_env value as both shell_env and dotenv.
    import harness.config as config
    config_load = config.load_agent
    try:
        config.load_agent = lambda pid: type("C", (), {"model": persisted})() if persisted else None
        return resolve_session_model(
            "p1", shell_set_model=shell_set, shell_env=val, dotenv=val, backend=backend)
    finally:
        config.load_agent = config_load


def test_proxy_model_in_shell_resolves():
    # Regression for the CRITICAL finding: PROXY_MODEL (not VIBEPROXY_MODEL) set
    # in the shell must win — under the old literal snapshot it was invisible.
    assert _resolve({"PROXY_MODEL": "glm"}) == "glm"


def test_legacy_vibeproxy_model_still_resolves():
    assert _resolve({"VIBEPROXY_MODEL": "gpt-5.4"}) == "gpt-5.4"


def test_proxy_wins_when_both_set():
    assert _resolve({"PROXY_MODEL": "glm", "VIBEPROXY_MODEL": "gpt-5.4"}) == "glm"


def test_persona_persisted_used_when_no_env():
    assert _resolve({}, persisted="claude-sonnet") == "claude-sonnet"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_proxy_model_precedence.py::test_proxy_model_in_shell_resolves -q`
Expected: FAIL — current code only reads `VIBEPROXY_MODEL`, so a `PROXY_MODEL`-only env yields the engine default, not `"glm"`.
(Note: this test exercises the helper-fed path directly; Steps 3–4 wire the real modules so the helpers are the source.)

- [ ] **Step 3: Replace literal snapshots in `tui_main.py`**

`harness/tui_main.py:112` — change:
```python
    shell_set_model = "VIBEPROXY_MODEL" in os.environ
```
to:
```python
    from harness import vibeproxy
    shell_set_model = vibeproxy.model_set_in(os.environ)
```

- [ ] **Step 4: Replace literal snapshots + reads in `acp_main.py`**

`harness/acp_main.py:117`:
```python
    shell_set_model = vibeproxy.model_set_in(os.environ)
```
`harness/acp_main.py:141`:
```python
    shell_env = vibeproxy.model_value(os.environ)   # shell OR .env at this point
```
`harness/acp_main.py:168` (the `shell_env=` kwarg in the `HarnessAgent(...)` call):
```python
        shell_env=vibeproxy.model_value(os.environ),
```
(`vibeproxy` is already imported in `acp_main`; if not, add `from harness import vibeproxy`.)

- [ ] **Step 5: Replace reads in the cron executor**

`harness/jobs/executor.py:207-208`:
```python
            shell_set_model=vibeproxy.model_set_in(os.environ),
            shell_env=vibeproxy.model_value(os.environ),
```
Add `from harness import vibeproxy` near the other harness imports if absent.
Update the comment at `harness/jobs/cron_main.py:58` to say "a project-only `PROXY_MODEL`/`VIBEPROXY_MODEL`" (text only; no code there reads the var).

- [ ] **Step 6: Run the precedence + full suite**

Run: `.venv/bin/python -m pytest tests/test_proxy_model_precedence.py tests/ -q`
Expected: PASS — `PROXY_MODEL` now resolves; legacy still works.

- [ ] **Step 7: Commit**

```bash
git add harness/tui_main.py harness/acp_main.py harness/jobs/executor.py harness/jobs/cron_main.py tests/test_proxy_model_precedence.py
git commit -m "fix(proxy): route model precedence through dual-name helpers

PROXY_MODEL set in shell/.env was invisible to the resolution ladder
because the snapshot keyed on the literal VIBEPROXY_MODEL. Feed the
ladder from vibeproxy.model_set_in/model_value so both names are honored.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task 3: Migrate `compress_cli.py` + `run_traced.py` reads

**Files:**
- Modify: `harness/compress_cli.py:83` (and the help text at :147)
- Modify: `harness/run_traced.py` (any `VIBEPROXY_MODEL` read — confirm with rg)
- Test: extend `tests/test_proxy_env_names.py`

**Interfaces:**
- Consumes: `vibeproxy.model_value(env)` from Task 1.

- [ ] **Step 1: Find the exact read sites**

Run: `/opt/homebrew/bin/rg -n 'VIBEPROXY_MODEL' harness/compress_cli.py harness/run_traced.py`
Note each line; the env *read* (`os.environ.get("VIBEPROXY_MODEL")`) becomes `vibeproxy.model_value(os.environ)`. Comment/help-text mentions get "`PROXY_MODEL` (or legacy `VIBEPROXY_MODEL`)".

- [ ] **Step 2: Write the failing test**

```python
# append to tests/test_proxy_env_names.py
def test_compress_cli_honors_proxy_model(monkeypatch):
    monkeypatch.delenv("VIBEPROXY_MODEL", raising=False)
    monkeypatch.delenv("COMPRESS_MODEL", raising=False)
    monkeypatch.setenv("PROXY_MODEL", "haiku-x")
    from harness import compress_cli
    # _resolve_compress_model falls back to the worker model env when COMPRESS_MODEL
    # is unset; assert PROXY_MODEL is now seen.
    assert compress_cli._resolve_compress_model() == "haiku-x"
```
(Confirm the real function name at `compress_cli.py:68-96`; adjust the call to match. If it takes an `env` arg, pass `os.environ`.)

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_proxy_env_names.py::test_compress_cli_honors_proxy_model -q`
Expected: FAIL (only `VIBEPROXY_MODEL` read today).

- [ ] **Step 4: Replace the read**

In `harness/compress_cli.py`, change `os.environ.get("VIBEPROXY_MODEL")` to `vibeproxy.model_value(os.environ)` (add `from harness import vibeproxy` if absent). Update the help string at :147 to mention `PROXY_MODEL`. Apply the same substitution in `run_traced.py` if rg found a read there.

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/test_proxy_env_names.py tests/ -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add harness/compress_cli.py harness/run_traced.py tests/test_proxy_env_names.py
git commit -m "fix(proxy): compress_cli/run_traced honor PROXY_MODEL via helper

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase 2 — The connection seam rename

### Task 4: Rename `vibeproxy.py → proxy.py` with a back-compat alias

**Files:**
- Create: `harness/proxy.py` (the renamed module)
- Modify: `harness/vibeproxy.py` → becomes a 2-line re-export shim
- Test: `tests/test_proxy_alias.py` (create)

**Interfaces:**
- Produces: `harness.proxy` with all of `vibeproxy`'s exports. `harness.vibeproxy` remains importable (one-release alias) so existing importers don't break.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_proxy_alias.py
def test_proxy_module_exists_and_has_seam():
    from harness import proxy
    assert hasattr(proxy, "base_url")
    assert hasattr(proxy, "model_kwargs")
    assert hasattr(proxy, "model_value")


def test_vibeproxy_alias_is_same_module():
    from harness import proxy, vibeproxy
    assert vibeproxy.default_model is proxy.default_model
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_proxy_alias.py -q`
Expected: FAIL (`ModuleNotFoundError: harness.proxy`).

- [ ] **Step 3: Perform the rename**

```bash
git mv harness/vibeproxy.py harness/proxy.py
```
Then create `harness/vibeproxy.py` as a shim:
```python
"""Back-compat alias. `vibeproxy` was renamed to `proxy`; import from
`harness.proxy`. This shim keeps existing importers working for one release."""
from harness.proxy import *          # noqa: F401,F403
from harness.proxy import (          # explicit re-export of module-level names
    DEFAULT_MODEL, base_url, api_key, default_model, model_id,
    completion_kwargs, model_kwargs, model_set_in, model_value,
)
```
Update the module docstring at the top of `harness/proxy.py` to say "proxy" / CLIProxyAPI instead of VibeProxy.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_proxy_alias.py tests/ -q`
Expected: PASS — all existing `from harness import vibeproxy` importers still resolve via the shim.

- [ ] **Step 5: Commit**

```bash
git add harness/proxy.py harness/vibeproxy.py tests/test_proxy_alias.py
git commit -m "refactor(proxy): rename vibeproxy.py -> proxy.py (alias kept)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase 3 — Lifecycle: binary download + OS service

### Task 5: Binary download + checksum verification

**Files:**
- Create: `harness/proxy_service/__init__.py`, `harness/proxy_service/binary.py`
- Create: `harness/proxy_service/paths.py`
- Test: `tests/test_proxy_binary.py` (create)

**Interfaces:**
- Produces: `binary.target_path() -> Path` (harness-owned bin location), `binary.verify_checksum(path: Path, expected_sha256: str) -> bool`, `binary.PINNED_VERSION: str`, `binary.asset_url(version, platform_key) -> str`.
- Consumes: nothing from prior phases.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_proxy_binary.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_proxy_binary.py -q`
Expected: FAIL (`ModuleNotFoundError: harness.proxy_service`).

- [ ] **Step 3: Implement `paths.py` and `binary.py`**

```python
# harness/proxy_service/paths.py
from __future__ import annotations
from pathlib import Path


def data_dir() -> Path:
    """Harness-owned data dir for the proxy (binary, config, secret)."""
    d = Path.home() / ".local" / "share" / "harness" / "proxy"
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_path() -> Path:
    return data_dir() / "config.yaml"


def secret_path() -> Path:
    return data_dir() / "management-password"   # 0600, plaintext, in-memory injected
```

```python
# harness/proxy_service/binary.py
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
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_proxy_binary.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add harness/proxy_service/ tests/test_proxy_binary.py
git commit -m "feat(proxy): binary path + checksum verification

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task 6: Config + management-password generation

**Files:**
- Create: `harness/proxy_service/config_gen.py`
- Test: `tests/test_proxy_config_gen.py` (create)

**Interfaces:**
- Produces: `config_gen.generate(port: int = 8317) -> str` (YAML text), `config_gen.ensure_management_password() -> str` (reads existing 0600 secret or generates one).
- Consumes: `proxy_service.paths` from Task 5.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_proxy_config_gen.py
import os
import stat
from harness.proxy_service import config_gen, paths


def test_generated_config_is_localhost_and_has_no_secret_key():
    yaml_text = config_gen.generate(port=8317)
    assert "port: 8317" in yaml_text
    assert "127.0.0.1" in yaml_text
    # We deliberately do NOT write remote-management.secret-key (it gets hashed).
    assert "secret-key:" not in yaml_text


def test_management_password_is_persisted_0600(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(paths, "secret_path", lambda: tmp_path / "management-password")
    pw1 = config_gen.ensure_management_password()
    pw2 = config_gen.ensure_management_password()       # idempotent: same value
    assert pw1 == pw2 and len(pw1) >= 32
    mode = stat.S_IMODE(os.stat(paths.secret_path()).st_mode)
    assert mode == 0o600
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_proxy_config_gen.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `config_gen.py`**

```python
# harness/proxy_service/config_gen.py
from __future__ import annotations
import os
import secrets
from harness.proxy_service import paths


def generate(port: int = 8317) -> str:
    # localhost-bound; client auth disabled (empty api-keys) since localhost-only;
    # management reachability comes from the injected MANAGEMENT_PASSWORD env, so
    # we intentionally omit remote-management.secret-key (config plaintext is
    # bcrypt-hashed on boot and unusable thereafter).
    return (
        'host: "127.0.0.1"\n'
        f"port: {port}\n"
        "api-keys: []\n"
        "remote-management:\n"
        "  allow-remote: false\n"
        "# openai-compatibility upstreams (e.g. NeuralWatt) are appended by docs.\n"
    )


def ensure_management_password() -> str:
    p = paths.secret_path()
    if p.exists():
        return p.read_text().strip()
    pw = secrets.token_urlsafe(32)
    p.write_text(pw)
    os.chmod(p, 0o600)
    return pw
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_proxy_config_gen.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add harness/proxy_service/config_gen.py tests/test_proxy_config_gen.py
git commit -m "feat(proxy): config generation + 0600 management password

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task 7: OS-service units (launchd + systemd)

**Files:**
- Create: `harness/proxy_service/service_launchd.py`, `harness/proxy_service/service_systemd.py`
- Test: `tests/test_proxy_service_units.py` (create)

**Interfaces:**
- Produces: `service_launchd.build_plist(binary, config_path, mgmt_password, label) -> bytes` and `service_systemd.build_unit(binary, config_path, mgmt_password, label) -> str`. Both reuse the cron `ServiceResult` shape conceptually but live in `proxy_service`.
- Consumes: `proxy_service.{binary,config_gen,paths}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_proxy_service_units.py
import plistlib
from harness.proxy_service import service_launchd, service_systemd

LABEL = "com.quiubo.done.proxy"


def test_launchd_plist_passes_config_and_mgmt_env():
    raw = service_launchd.build_plist("/bin/cli-proxy-api", "/cfg/config.yaml",
                                      "secret123", LABEL)
    doc = plistlib.loads(raw)
    assert doc["Label"] == LABEL
    assert "--config" in doc["ProgramArguments"]
    assert "/cfg/config.yaml" in doc["ProgramArguments"]
    assert doc["EnvironmentVariables"]["MANAGEMENT_PASSWORD"] == "secret123"
    assert doc["KeepAlive"] is True
    assert doc["ThrottleInterval"] == 10


def test_systemd_unit_has_config_restart_and_mgmt_env():
    unit = service_systemd.build_unit("/bin/cli-proxy-api", "/cfg/config.yaml",
                                      "secret123", LABEL)
    assert "ExecStart=/bin/cli-proxy-api --config /cfg/config.yaml" in unit
    assert "Environment=MANAGEMENT_PASSWORD=secret123" in unit
    assert "Restart=always" in unit
    assert "RestartSec=5" in unit
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_proxy_service_units.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement the two unit builders**

```python
# harness/proxy_service/service_launchd.py
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
```

```python
# harness/proxy_service/service_systemd.py
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
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_proxy_service_units.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add harness/proxy_service/service_launchd.py harness/proxy_service/service_systemd.py tests/test_proxy_service_units.py
git commit -m "feat(proxy): launchd+systemd units (--config, mgmt env, restart)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task 8: Management-API client (status + readiness)

**Files:**
- Create: `harness/proxy_service/management.py`
- Test: `tests/test_proxy_management.py` (create)

**Interfaces:**
- Produces: `management.auth_status(password, base="http://localhost:8317") -> dict` (provider → bool), `management.is_ready(password, base=...) -> bool`, `management.auth_url(provider, password, base=...) -> tuple[str, str]` (url, state), `management.poll_auth_status(state, password, base=...) -> str`.
- Consumes: the management password from Task 6.

- [ ] **Step 1: Write the failing test** (HTTP mocked)

```python
# tests/test_proxy_management.py
from harness.proxy_service import management


class _FakeResp:
    def __init__(self, status, payload): self.status_code, self._p = status, payload
    def json(self): return self._p
    def raise_for_status(self): pass


def test_is_ready_true_on_200(monkeypatch):
    monkeypatch.setattr(management, "_get",
                        lambda path, password, base: _FakeResp(200, {"status": "ok"}))
    assert management.is_ready("pw") is True


def test_auth_url_returns_url_and_state(monkeypatch):
    monkeypatch.setattr(management, "_get",
        lambda path, password, base: _FakeResp(200, {"url": "https://x", "state": "anth-1"}))
    url, state = management.auth_url("anthropic", "pw")
    assert url == "https://x" and state == "anth-1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_proxy_management.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `management.py`**

```python
# harness/proxy_service/management.py
from __future__ import annotations
import urllib.request
import json

_BASE = "http://localhost:8317/v0/management"
# Provider → management auth-url path. Only these three expose the browser+poll
# flow (verified vs help.router-for.me). xAI/Kimi are CLI-flag; gemini is API-key.
_AUTH_URL_PATHS = {
    "anthropic": "anthropic-auth-url",
    "codex": "codex-auth-url",
    "antigravity": "antigravity-auth-url",
}


def _get(path: str, password: str, base: str = _BASE):
    req = urllib.request.Request(f"{base}/{path}",
                                 headers={"Authorization": f"Bearer {password}"})
    resp = urllib.request.urlopen(req, timeout=5)        # noqa: S310 (localhost)
    body = json.loads(resp.read().decode())
    return type("R", (), {"status_code": resp.status, "json": lambda self=None: body})()


def is_ready(password: str, base: str = _BASE) -> bool:
    try:
        return _get("get-auth-status", password, base).status_code == 200
    except Exception:
        return False


def auth_url(provider: str, password: str, base: str = _BASE):
    r = _get(_AUTH_URL_PATHS[provider], password, base).json()
    return r["url"], r["state"]


def poll_auth_status(state: str, password: str, base: str = _BASE) -> str:
    # OPEN ITEM (spec #4): confirm exact response field/terminal states.
    r = _get(f"get-auth-status?state={state}", password, base).json()
    return r.get("status", "pending")
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_proxy_management.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add harness/proxy_service/management.py tests/test_proxy_management.py
git commit -m "feat(proxy): management API client (status, readiness, auth-url)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task 9: Lifecycle orchestrator + `dn proxy` CLI

**Files:**
- Create: `harness/proxy_service/cli.py` (the `dn proxy` subcommand runner)
- Create: `harness/proxy_service/lifecycle.py` (install/uninstall/start/stop/status compose the above)
- Modify: `harness/tui_main.py` (route `["proxy", ...]` to `proxy_service.cli.run`, mirroring the cron route)
- Test: `tests/test_dn_proxy_routing.py` (create)

**Interfaces:**
- Consumes: `binary`, `config_gen`, `service_launchd/systemd`, `management` from Tasks 5–8.
- Produces: `proxy_service.cli.run(argv) -> int`; `dn proxy {install,uninstall,start,stop,status,upgrade,login}` reach it.

- [ ] **Step 1: Write the failing routing test** (mirror `test_dn_cron_routing.py`)

```python
# tests/test_dn_proxy_routing.py
import pytest
from harness import tui_main


def test_dn_proxy_routes_to_cli_without_launching_tui(monkeypatch):
    seen = {}
    monkeypatch.setattr("harness.proxy_service.cli.run",
                        lambda argv: (seen.__setitem__("argv", argv) or 0))
    monkeypatch.setattr(tui_main, "HarnessTui",
                        lambda *a, **k: pytest.fail("TUI must not launch for `dn proxy`"))
    rc = tui_main.main(["proxy", "status"])
    assert rc == 0
    assert seen["argv"] == ["status"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_dn_proxy_routing.py -q`
Expected: FAIL (no `proxy_service.cli` and no route in `tui_main`).

- [ ] **Step 3: Implement `cli.py` + `lifecycle.py` (minimal), add the route**

In `harness/tui_main.py`, next to the existing `cron` route, add:
```python
    if argv and argv[0] == "proxy":
        from harness.proxy_service import cli as proxy_cli
        return proxy_cli.run(argv[1:])
```
`harness/proxy_service/cli.py`:
```python
from __future__ import annotations
from harness.proxy_service import lifecycle


def run(argv) -> int:
    cmd = argv[0] if argv else "status"
    fn = {
        "install": lifecycle.install, "uninstall": lifecycle.uninstall,
        "start": lifecycle.start, "stop": lifecycle.stop,
        "status": lifecycle.status, "upgrade": lifecycle.upgrade,
        "login": lambda: lifecycle.login(argv[1] if len(argv) > 1 else None),
    }.get(cmd)
    if fn is None:
        print(f"unknown: dn proxy {cmd}")
        return 2
    result = fn()
    print(result)
    return 0
```
`harness/proxy_service/lifecycle.py` — implement `status()` first (compose `management.is_ready` + `management` auth status) and stub the rest to return a clear "not yet implemented" string so the route is testable. Flesh out `install()` (download via `binary`, write config via `config_gen`, register the OS unit) in the same task once `status` is green.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_dn_proxy_routing.py tests/ -q`
Expected: PASS — routing works; bare `dn` still reaches the TUI (existing cron routing test still green proves the dispatcher is intact).

- [ ] **Step 5: Commit**

```bash
git add harness/proxy_service/cli.py harness/proxy_service/lifecycle.py harness/tui_main.py tests/test_dn_proxy_routing.py
git commit -m "feat(proxy): dn proxy CLI routing + lifecycle orchestrator

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase 4 — Login modal (capability-split)

### Task 10: Provider capability map

**Files:**
- Create: `harness/proxy_service/providers.py`
- Test: `tests/test_proxy_providers.py` (create)

**Interfaces:**
- Produces: `providers.PROVIDERS: list[Provider]` where `Provider` has `id`, `label`, `mechanism` ∈ {"browser_poll", "cli_flag", "api_key"}, and (for cli_flag) `login_flag`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_proxy_providers.py
from harness.proxy_service import providers


def test_mechanism_split_matches_verified_facts():
    by_id = {p.id: p for p in providers.PROVIDERS}
    assert by_id["anthropic"].mechanism == "browser_poll"
    assert by_id["codex"].mechanism == "browser_poll"
    assert by_id["antigravity"].mechanism == "browser_poll"
    assert by_id["xai"].mechanism == "cli_flag" and by_id["xai"].login_flag == "--xai-login"
    assert by_id["kimi"].mechanism == "cli_flag" and by_id["kimi"].login_flag == "--kimi-login"
    assert by_id["gemini"].mechanism == "api_key"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_proxy_providers.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `providers.py`**

```python
# harness/proxy_service/providers.py
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    mechanism: str            # "browser_poll" | "cli_flag" | "api_key"
    login_flag: str | None = None


PROVIDERS = [
    Provider("anthropic", "Claude", "browser_poll"),
    Provider("codex", "OpenAI / Codex", "browser_poll"),
    Provider("antigravity", "Antigravity", "browser_poll"),
    Provider("xai", "Grok (xAI)", "cli_flag", "--xai-login"),
    Provider("kimi", "Kimi", "cli_flag", "--kimi-login"),
    Provider("gemini", "Gemini / AI Studio", "api_key"),
]
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_proxy_providers.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/proxy_service/providers.py tests/test_proxy_providers.py
git commit -m "feat(proxy): provider capability map (browser_poll/cli_flag/api_key)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task 11: Login dispatch (headless, testable core)

**Files:**
- Create: `harness/proxy_service/login.py`
- Test: `tests/test_proxy_login.py` (create)

**Interfaces:**
- Consumes: `providers`, `management`, `binary` from earlier tasks.
- Produces: `login.start(provider_id, password, *, open_browser, run_subprocess) -> LoginHandle`. The two callables are injected so tests pass fakes (no real browser/subprocess). `browser_poll` → `management.auth_url` + `open_browser(url)`; `cli_flag` → `run_subprocess([binary, flag])`; `api_key` → returns a "see docs" sentinel.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_proxy_login.py
from harness.proxy_service import login


def test_browser_poll_opens_url(monkeypatch):
    opened = {}
    monkeypatch.setattr("harness.proxy_service.management.auth_url",
                        lambda p, pw, base=None: ("https://x", "st-1"))
    h = login.start("anthropic", "pw",
                    open_browser=lambda url: opened.__setitem__("url", url),
                    run_subprocess=lambda argv: 0)
    assert opened["url"] == "https://x" and h.state == "st-1"


def test_cli_flag_runs_subprocess(monkeypatch):
    ran = {}
    h = login.start("xai", "pw",
                    open_browser=lambda url: None,
                    run_subprocess=lambda argv: ran.__setitem__("argv", argv) or 0)
    assert "--xai-login" in ran["argv"]


def test_api_key_provider_returns_docs_sentinel():
    h = login.start("gemini", "pw", open_browser=lambda u: None, run_subprocess=lambda a: 0)
    assert h.mechanism == "api_key"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_proxy_login.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `login.py`**

```python
# harness/proxy_service/login.py
from __future__ import annotations
from dataclasses import dataclass
from harness.proxy_service import providers as _providers, management, binary


@dataclass
class LoginHandle:
    provider_id: str
    mechanism: str
    state: str | None = None       # browser_poll only
    rc: int | None = None          # cli_flag only


def _provider(pid: str):
    for p in _providers.PROVIDERS:
        if p.id == pid:
            return p
    raise KeyError(pid)


def start(provider_id, password, *, open_browser, run_subprocess) -> LoginHandle:
    p = _provider(provider_id)
    if p.mechanism == "browser_poll":
        url, state = management.auth_url(p.id, password)
        open_browser(url)
        return LoginHandle(p.id, p.mechanism, state=state)
    if p.mechanism == "cli_flag":
        rc = run_subprocess([str(binary.target_path()), p.login_flag])
        return LoginHandle(p.id, p.mechanism, rc=rc)
    return LoginHandle(p.id, p.mechanism)        # api_key → docs
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_proxy_login.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add harness/proxy_service/login.py tests/test_proxy_login.py
git commit -m "feat(proxy): capability-split login dispatch (injectable I/O)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task 12: Login modal widget + wiring

**Files:**
- Create: `harness/tui/widgets/proxy_login_modal.py`
- Modify: `harness/tui/app.py` (push the modal; add a `dn proxy login` action / keybinding)
- Test: `tests/test_proxy_login_modal.py` (create)

**Interfaces:**
- Consumes: `providers`, `login`, `management` from Tasks 10–11. Reuses `SelectModal`/`NewPersonaModal` patterns (`ModalScreen`, `dismiss(value)`, spinner glyphs `['◐','◓','◑','◒']`).
- Produces: `ProxyLoginModal(ModalScreen)` — lists providers with `✓/✗`, dispatches via `login.start`, polls `management.poll_auth_status` for `browser_poll`, shows docs hint for `api_key`.

- [ ] **Step 1: Write the failing test** (logic-level, no live Textual run)

```python
# tests/test_proxy_login_modal.py
from harness.tui.widgets.proxy_login_modal import provider_rows


def test_rows_render_status_and_mechanism():
    rows = provider_rows(status={"anthropic": True, "xai": False})
    anth = next(r for r in rows if r["id"] == "anthropic")
    xai = next(r for r in rows if r["id"] == "xai")
    assert anth["mark"] == "✓" and "browser" in anth["hint"]
    assert xai["mark"] == "✗" and "CLI" in xai["hint"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_proxy_login_modal.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `provider_rows` + the modal shell**

Put the pure row-builder at module scope (testable without a running app), then
the `ModalScreen` subclass that renders it and dispatches `login.start`:
```python
# harness/tui/widgets/proxy_login_modal.py  (excerpt — the pure, tested part)
from harness.proxy_service import providers as _p

_HINT = {"browser_poll": "browser", "cli_flag": "CLI flag", "api_key": "API key (docs)"}


def provider_rows(status: dict) -> list[dict]:
    rows = []
    for prov in _p.PROVIDERS:
        authed = status.get(prov.id, False)
        rows.append({
            "id": prov.id, "label": prov.label,
            "mark": "✓" if authed else ("—" if prov.mechanism == "api_key" else "✗"),
            "hint": _HINT[prov.mechanism],
        })
    return rows
```
The `ModalScreen` subclass (mechanics mirror `new_persona_modal.py`: spinner via
`set_interval(0.15, ...)`, `set_error()` keeps it open, `dismiss(result)` on
success) consumes `provider_rows` and calls `login.start(...)` with the app's
real `webbrowser.open` and `subprocess.run`. Wire a push point in `app.py` next
to the cron prompt.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_proxy_login_modal.py tests/ -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/tui/widgets/proxy_login_modal.py harness/tui/app.py tests/test_proxy_login_modal.py
git commit -m "feat(proxy): capability-aware login modal

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase 5 — Docs & migration

### Task 13: Docs — upstreams, login, migration

**Files:**
- Create: `docs/proxy.md`
- Modify: `README.md` (point the proxy setup section at CLIProxyAPI + `dn proxy`)
- Modify: `.env.example` (add `PROXY_*`, keep `VIBEPROXY_*` noted as legacy)

**Interfaces:** none (docs only).

- [ ] **Step 1: Write `docs/proxy.md`**

Cover, with copy-paste-ready blocks: (a) `dn proxy install` / `login <provider>` /
`status`; (b) **Adding an API-key upstream (NeuralWatt → GLM)** — the
`openai-compatibility` YAML from the spec, with `NEURALWATT_API_KEY` and the
`glm` alias; (c) the provider login matrix (Claude/Codex/Antigravity = browser,
Grok/Kimi = CLI flag, Gemini = API key); (d) **Migration from VibeProxy**:
`dn proxy install` → `dn proxy login` per provider → add the NeuralWatt block →
rename `VIBEPROXY_*` to `PROXY_*` in `.env` (both honored; `PROXY_*` wins).

- [ ] **Step 2: Update `.env.example`**

```bash
# Model proxy (CLIProxyAPI). PROXY_* is canonical; VIBEPROXY_* still honored.
PROXY_BASE_URL=http://localhost:8317/v1
PROXY_MODEL=gpt-5.4
PROXY_API_KEY=dummy-not-used
```

- [ ] **Step 3: Update the README proxy section**

Replace VibeProxy setup prose with the `dn proxy install` + `login` flow and a
link to `docs/proxy.md`.

- [ ] **Step 4: Commit**

```bash
git add docs/proxy.md README.md .env.example
git commit -m "docs(proxy): CLIProxyAPI setup, NeuralWatt upstream, migration

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Final verification

- [ ] Run the full suite from the worktree root: `.venv/bin/python -m pytest tests/ -q` — all green.
- [ ] Confirm primary checkout clean: `git -C /Users/alberto/Work/Quiubo/harness status --short` — empty.
- [ ] Resolve the four spec Open Items before any real install runs:
  1. NeuralWatt GLM model id (from NeuralWatt `/v1/models`).
  2. Pinned version + asset URL pattern + checksum source (`binary.py`).
  3. Gemini OAuth path (keep API-key/docs-only if none).
  4. `get-auth-status` response shape (`management.poll_auth_status`).
