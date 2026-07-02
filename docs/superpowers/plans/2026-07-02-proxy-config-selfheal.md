# Proxy Config Self-Heal (delta on #292) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Kill the empty-shell-key foot-gun, reduce drifted-config recovery to one keypress, make install/upgrade output truthful, and warn at session start when an agent's configured model isn't served by the proxy.

**Architecture:** Four independent deltas on top of merged PR #292 (`config_drift`/auto-install/warn-on-drift). All secret handling flows through `config_gen._machine_global_env()`; all proxy restarts stay user-consented (a #292 hard constraint); the availability warning wires the existing dead `model_availability.resolve_or_warn` into TUI mount, fail-open.

**Tech Stack:** Python 3.12, Textual (TUI), pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-01-proxy-config-selfheal-design.md`

## Global Constraints

- **Never auto-restart a running proxy** — restarts only behind explicit user consent (PR #292 constraint).
- `resolve_or_warn` **never substitutes** a configured model — warn only.
- Session-start checks are **fail-open**: any error → silent skip, never block or delay startup.
- Empty-string env values are treated as **absent** at every consumption point.
- Worktree: `/Users/alberto/Work/Quiubo/harness/.worktrees/proxy-config-selfheal` — all edits and commits happen there.
- Test command (run from the worktree root; the worktree conftest resolves imports):
  `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/ -q` (target `tests/` only).

---

### Task 1: Empty-is-absent in `_machine_global_env()` + `generate()` default + TUI overlay

The foot-gun: `merged.update(os.environ)` lets an exported `NEURALWATT_API_KEY=""` beat the real key in `~/.config/harness/.env`; the TUI drift check's `if self._shell_neuralwatt_key is not None` overlays `""` the same way, so the poisoned terminal both writes keyless configs and suppresses the drift warning.

**Files:**
- Modify: `harness/proxy_service/config_gen.py` (`_machine_global_env` body ~lines 63-69; `generate` default ~lines 13-14)
- Modify: `harness/tui/app.py` (`_check_proxy_config_drift`, the `is not None` overlay ~line 463)
- Test: `tests/test_proxy_config_gen.py`, `tests/test_tui_widgets.py`

**Interfaces:**
- Consumes: existing `config_gen._machine_global_env()`, `generate(port=8317, *, env=None)`.
- Produces: same signatures; new semantics — empty-string values never survive `_machine_global_env()`, and `generate(env=None)` defaults to `_machine_global_env()` instead of raw `os.environ` (so `install()`/`upgrade()` become immune to empty shell exports too).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_proxy_config_gen.py`:

```python
def test_machine_global_env_empty_shell_value_does_not_mask_file_key(tmp_path, monkeypatch):
    # Poisoned terminal: shell exports NEURALWATT_API_KEY="" while the real key
    # lives in ~/.config/harness/.env. Empty must be treated as absent.
    from harness.proxy_service import config_gen
    from harness import paths
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / ".env").write_text("NEURALWATT_API_KEY=sk-real-key\n")
    monkeypatch.setattr(paths, "config_dir", lambda: cfg_dir)
    monkeypatch.setenv("NEURALWATT_API_KEY", "")
    env = config_gen._machine_global_env()
    assert env.get("NEURALWATT_API_KEY") == "sk-real-key"


def test_machine_global_env_nonempty_shell_value_still_wins(tmp_path, monkeypatch):
    # A REAL shell export keeps #292's documented precedence (process env wins).
    from harness.proxy_service import config_gen
    from harness import paths
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / ".env").write_text("NEURALWATT_API_KEY=sk-file-key\n")
    monkeypatch.setattr(paths, "config_dir", lambda: cfg_dir)
    monkeypatch.setenv("NEURALWATT_API_KEY", "sk-shell-key")
    env = config_gen._machine_global_env()
    assert env.get("NEURALWATT_API_KEY") == "sk-shell-key"


def test_machine_global_env_empty_file_value_is_absent(tmp_path, monkeypatch):
    from harness.proxy_service import config_gen
    from harness import paths
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / ".env").write_text("NEURALWATT_API_KEY=\n")
    monkeypatch.setattr(paths, "config_dir", lambda: cfg_dir)
    monkeypatch.delenv("NEURALWATT_API_KEY", raising=False)
    env = config_gen._machine_global_env()
    assert "NEURALWATT_API_KEY" not in env


def test_generate_default_env_is_machine_global(tmp_path, monkeypatch):
    # generate(env=None) must resolve through _machine_global_env(), so
    # install()/upgrade() write keyed configs even from a poisoned shell.
    from harness.proxy_service import config_gen
    from harness import paths
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / ".env").write_text("NEURALWATT_API_KEY=sk-real-key\n")
    monkeypatch.setattr(paths, "config_dir", lambda: cfg_dir)
    monkeypatch.setenv("NEURALWATT_API_KEY", "")
    y = config_gen.generate()
    assert "neuralwatt" in y and "sk-real-key" in y
```

Append to `tests/test_tui_widgets.py` (mirror the existing drift-check stub pattern at ~line 404; reuse its `_DriftStub`-style stub — read the neighboring tests first and copy their stub construction exactly):

```python
def test_check_proxy_config_drift_empty_shell_key_does_not_mask(monkeypatch, tmp_path):
    # Poisoned terminal: pre-launch snapshot captured "" — the overlay must
    # treat it like None (fall through to the file key), so a keyless on-disk
    # config in that terminal reports "drifted", not "ok".
    import harness.tui.app as app_mod
    from harness import paths
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / ".env").write_text("NEURALWATT_API_KEY=sk-real-key\n")
    monkeypatch.setattr(paths, "config_dir", lambda: cfg_dir)
    seen_envs = []

    def fake_drift(env=None):
        seen_envs.append(env)
        return "ok"

    monkeypatch.setattr("harness.proxy_service.config_gen.config_drift", fake_drift)
    stub = _make_drift_stub(shell_neuralwatt_key="")   # match the file's existing stub helper/pattern
    app_mod.HarnessTui._check_proxy_config_drift(stub)
    assert seen_envs and seen_envs[0].get("NEURALWATT_API_KEY") == "sk-real-key"
```

(If the file has no shared stub helper, construct the stub inline exactly as `test_check_proxy_config_drift_logs_when_drifted` does, with `_shell_neuralwatt_key = ""`.)

- [ ] **Step 2: Run tests to verify they fail**

Run from the worktree root:
```bash
cd /Users/alberto/Work/Quiubo/harness/.worktrees/proxy-config-selfheal
/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_proxy_config_gen.py tests/test_tui_widgets.py -q
```
Expected: the four new config_gen tests FAIL (empty shell value masks file key / generate defaults to os.environ); the TUI test FAILS (env carries `""`).

- [ ] **Step 3: Implement**

In `harness/proxy_service/config_gen.py`, replace the body of `_machine_global_env()` (keep the docstring, append one sentence: "Empty-string values are treated as absent — an exported empty key must never mask the file's real key."):

```python
    from dotenv import dotenv_values
    from harness import paths as _harness_paths

    merged = dict(dotenv_values(_harness_paths.config_dir() / ".env"))
    # Process env wins — matches load_env()'s override=False precedence — but an
    # EMPTY exported value is "absent", not an override: it must not mask a real
    # file key (the 2026-07-01 ten-reinstalls foot-gun).
    merged.update({k: v for k, v in os.environ.items() if v != ""})
    return {k: v for k, v in merged.items() if v}
```

In `generate()`, change the default-env lines:

```python
    if env is None:
        env = _machine_global_env()
```

(`_machine_global_env` is defined below `generate` in the current file — move `_machine_global_env` above `generate` so the name resolves at call time; it already resolves lazily inside the function body, so a move is optional but keeps reading order sane.)

In `harness/tui/app.py` `_check_proxy_config_drift()`, change the overlay condition:

```python
            if self._shell_neuralwatt_key:   # "" = no real export; must not mask the file key
                machine_global["NEURALWATT_API_KEY"] = self._shell_neuralwatt_key
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_proxy_config_gen.py tests/test_tui_widgets.py tests/proxy_service/ tests/test_proxy_lifecycle.py -q
```
Expected: PASS (including all pre-existing #292 tests).

- [ ] **Step 5: Commit**

```bash
git add harness/proxy_service/config_gen.py harness/tui/app.py tests/test_proxy_config_gen.py tests/test_tui_widgets.py
git commit -m "fix(proxy): empty-string env keys are absent, never mask the file key"
```

---

### Task 2: Truthful reporting — `summarize()`, `masking_note()`, `removal_note()` wired into `install()`/`upgrade()`

**Files:**
- Modify: `harness/proxy_service/config_gen.py` (append three pure helpers)
- Modify: `harness/proxy_service/lifecycle.py` (`install()` ~lines 54-98, `upgrade()` ~lines 101-130)
- Test: `tests/test_proxy_config_gen.py`, `tests/test_proxy_lifecycle.py`

**Interfaces:**
- Consumes: `config_gen.generate()` output text (Task 1 semantics).
- Produces (all pure, in `config_gen`):
  - `summarize(config_text: str) -> str` — one line describing providers/models in a generated config.
  - `masking_note(file_env: dict, process_env: dict) -> str | None` — note when a non-empty shell key differs from a non-empty file key.
  - `removal_note(old_text: str, new_text: str) -> str | None` — note when a provider present in the old config is gone from the new.
  - `install()`/`upgrade()` return strings gain these lines.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_proxy_config_gen.py`:

```python
def test_summarize_keyed_config_names_provider_and_model_count():
    from harness.proxy_service import config_gen
    y = config_gen.generate(env={"NEURALWATT_API_KEY": "sk-x"})
    assert config_gen.summarize(y) == "config: neuralwatt (3 models)"


def test_summarize_keyless_config_says_no_providers():
    from harness.proxy_service import config_gen
    y = config_gen.generate(env={})
    s = config_gen.summarize(y)
    assert "NO upstream providers" in s and "NEURALWATT_API_KEY" in s


def test_masking_note_fires_only_on_differing_nonempty_values():
    from harness.proxy_service import config_gen
    assert config_gen.masking_note({"NEURALWATT_API_KEY": "a"}, {"NEURALWATT_API_KEY": "b"})
    assert config_gen.masking_note({"NEURALWATT_API_KEY": "a"}, {"NEURALWATT_API_KEY": "a"}) is None
    assert config_gen.masking_note({}, {"NEURALWATT_API_KEY": "b"}) is None
    assert config_gen.masking_note({"NEURALWATT_API_KEY": "a"}, {}) is None
    assert config_gen.masking_note({"NEURALWATT_API_KEY": "a"}, {"NEURALWATT_API_KEY": ""}) is None


def test_removal_note_names_dropped_provider():
    from harness.proxy_service import config_gen
    old = config_gen.generate(env={"NEURALWATT_API_KEY": "sk-x"})
    new = config_gen.generate(env={})
    assert "neuralwatt" in config_gen.removal_note(old, new)
    assert config_gen.removal_note(new, old) is None      # provider ADDED, not removed
    assert config_gen.removal_note(old, old) is None
```

Append to `tests/test_proxy_lifecycle.py` (follow the file's existing monkeypatch style — it already stubs `download`, `_register_os_service`, `start`, `management.is_ready`):

```python
def test_install_reports_config_summary(monkeypatch, tmp_path):
    from harness.proxy_service import lifecycle, config_gen, paths
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(lifecycle.download, "download_and_install", lambda v: tmp_path / "bin")
    monkeypatch.setattr(lifecycle, "_register_os_service", lambda *a: "")
    monkeypatch.setattr(lifecycle, "start", lambda: "started")
    monkeypatch.setattr(lifecycle.management, "is_ready", lambda pw: True)
    monkeypatch.setattr(config_gen, "generate", lambda env=None: 'host: "x"\n')
    out = lifecycle.install()
    assert "NO upstream providers" in out


def test_upgrade_names_removed_provider(monkeypatch, tmp_path):
    from harness.proxy_service import lifecycle, config_gen, paths
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)
    # Capture real texts BEFORE stubbing: old on-disk = keyed, new generate = keyless.
    keyed = config_gen.generate(env={"NEURALWATT_API_KEY": "sk-x"})
    keyless = config_gen.generate(env={})
    paths.config_path().write_text(keyed)
    monkeypatch.setattr(lifecycle.download, "download_and_install", lambda v: tmp_path / "bin")
    monkeypatch.setattr(lifecycle, "stop", lambda: "stopped")
    monkeypatch.setattr(lifecycle, "start", lambda: "started")
    monkeypatch.setattr(config_gen, "generate", lambda env=None: keyless)
    out = lifecycle.upgrade()
    assert "removed: neuralwatt" in out
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_proxy_config_gen.py tests/test_proxy_lifecycle.py -q
```
Expected: FAIL with `AttributeError: module ... has no attribute 'summarize'` etc.

- [ ] **Step 3: Implement the helpers**

Append to `harness/proxy_service/config_gen.py`:

```python
def summarize(config_text: str) -> str:
    """One truthful line about what a generated config actually contains, for
    install()/upgrade()/refresh output. The 2026-07-01 failure survived ten
    reinstalls because install() said "running" about a keyless config."""
    if "openai-compatibility:" not in config_text:
        return ("config: NO upstream providers — no NEURALWATT_API_KEY in "
                "~/.config/harness/.env")
    n = config_text.count('\n      - name: "')     # model entries (6-space indent)
    return f"config: neuralwatt ({n} models)"


def masking_note(file_env: dict, process_env: dict) -> str | None:
    """Note when a non-empty shell export differs from a non-empty file key —
    the shell wins (documented precedence) but never silently."""
    f = (file_env.get("NEURALWATT_API_KEY") or "").strip()
    p = (process_env.get("NEURALWATT_API_KEY") or "").strip()
    if f and p and f != p:
        return "note: shell NEURALWATT_API_KEY overrides ~/.config/harness/.env"
    return None


def removal_note(old_text: str, new_text: str) -> str | None:
    """Name a provider that a regen dropped. The file is truth — removal is
    honored, never silent."""
    if '- name: "neuralwatt"' in old_text and '- name: "neuralwatt"' not in new_text:
        return "removed: neuralwatt (key no longer present)"
    return None
```

- [ ] **Step 4: Wire into `install()` and `upgrade()`**

In `lifecycle.install()`, replace the config-write step (current lines ~70-76) with:

```python
    # Step 2 — write config.
    try:
        cfg_path = paths.config_path()
        old_text = cfg_path.read_text() if cfg_path.exists() else ""
        config_text = config_gen.generate()
        cfg_path.write_text(config_text)
    except Exception as exc:
        return f"CLIProxyAPI install: config write failed — {exc}"
```

And build the extra report lines just before each success `return` (both the
"running" return and the "readiness check timed out" return):

```python
    from dotenv import dotenv_values
    from harness import paths as harness_paths
    extra = [config_gen.summarize(config_text)]
    removal = config_gen.removal_note(old_text, config_text)
    if removal:
        extra.append(removal)
    masking = config_gen.masking_note(
        dict(dotenv_values(harness_paths.config_dir() / ".env")), dict(os.environ))
    if masking:
        extra.append(masking)
```

Then the two success returns become:

```python
            return "\n".join(["CLIProxyAPI install: running", *extra])
```
```python
    return "\n".join(
        ["CLIProxyAPI install: started (readiness check timed out — may still be starting)", *extra])
```

Apply the same pattern to `upgrade()`: capture `old_text` before its config
write (~line 123), build `extra` the same way, and return
`"\n".join([f"CLIProxyAPI upgrade: complete ({stop_result}; {start_result})", *extra])`.
Add `import os` to lifecycle.py's imports if not present (it is — line 13).

- [ ] **Step 5: Run tests to verify they pass**

```bash
/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_proxy_config_gen.py tests/test_proxy_lifecycle.py -q
```
Expected: PASS, including all pre-existing lifecycle tests (they assert substrings of the old messages; the old first lines are preserved verbatim as line 1).

- [ ] **Step 6: Commit**

```bash
git add harness/proxy_service/config_gen.py harness/proxy_service/lifecycle.py tests/test_proxy_config_gen.py tests/test_proxy_lifecycle.py
git commit -m "feat(proxy): truthful install/upgrade reporting — summary, masking + removal notes"
```

---

### Task 3: `lifecycle.refresh_config()` + `dn proxy refresh` CLI verb

**Files:**
- Modify: `harness/proxy_service/lifecycle.py` (new function after `upgrade()`)
- Modify: `harness/proxy_service/cli.py` (add `"refresh"` to the dispatch map, line ~16)
- Test: `tests/test_proxy_lifecycle.py`

**Interfaces:**
- Consumes: `config_gen.generate()/summarize()/removal_note()` (Tasks 1-2), existing `stop()`/`start()`.
- Produces: `lifecycle.refresh_config() -> str` — regenerate config + restart; `dn proxy upgrade` minus the binary download. Task 4's consent prompt calls this.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_proxy_lifecycle.py`:

```python
def test_refresh_config_regenerates_and_restarts(monkeypatch, tmp_path):
    from harness.proxy_service import lifecycle, config_gen, paths
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)
    calls = []
    monkeypatch.setattr(lifecycle, "stop", lambda: calls.append("stop") or "stopped")
    monkeypatch.setattr(lifecycle, "start", lambda: calls.append("start") or "started")
    monkeypatch.setattr(config_gen, "generate",
                        lambda env=None: 'host: "x"\nopenai-compatibility:\n  - name: "neuralwatt"\n    models:\n      - name: "glm-5.2"\n')
    out = lifecycle.refresh_config()
    assert calls == ["stop", "start"]
    assert "refresh: complete" in out
    assert "neuralwatt" in paths.config_path().read_text()


def test_refresh_config_write_failure_aborts_before_restart(monkeypatch, tmp_path):
    from harness.proxy_service import lifecycle, config_gen, paths
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)
    calls = []
    monkeypatch.setattr(lifecycle, "stop", lambda: calls.append("stop"))
    monkeypatch.setattr(lifecycle, "start", lambda: calls.append("start"))
    monkeypatch.setattr(config_gen, "generate", lambda env=None: (_ for _ in ()).throw(RuntimeError("boom")))
    out = lifecycle.refresh_config()
    assert calls == []                      # never restart against a half-written config
    assert "config write failed" in out


def test_cli_dispatches_refresh(monkeypatch):
    from harness.proxy_service import cli, lifecycle
    monkeypatch.setattr(lifecycle, "refresh_config", lambda: "CLIProxyAPI refresh: complete")
    monkeypatch.setattr("harness.paths.load_env", lambda project_dir=None: None)
    assert cli.run(["refresh"]) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_proxy_lifecycle.py -q
```
Expected: FAIL — `refresh_config` not defined; cli prints `unknown: dn proxy refresh`.

- [ ] **Step 3: Implement**

Append to `harness/proxy_service/lifecycle.py` after `upgrade()`:

```python
def refresh_config() -> str:
    """Regenerate config.yaml from current machine-global env and restart the
    service — `dn proxy upgrade` minus the binary re-download. Only ever called
    from user-consented paths (the TUI drift prompt, the explicit
    `dn proxy refresh` verb); never run unattended — restarting the
    machine-global proxy under other sessions/cron is the #292 hard constraint.
    """
    try:
        config_gen.ensure_management_password()
        cfg_path = paths.config_path()
        old_text = cfg_path.read_text() if cfg_path.exists() else ""
        new_text = config_gen.generate()
        cfg_path.write_text(new_text)
    except Exception as exc:
        return f"CLIProxyAPI refresh: config write failed — {exc}"

    stop_result = stop()
    start_result = start()
    lines = [f"CLIProxyAPI refresh: complete ({stop_result}; {start_result})",
             config_gen.summarize(new_text)]
    removal = config_gen.removal_note(old_text, new_text)
    if removal:
        lines.append(removal)
    return "\n".join(lines)
```

In `harness/proxy_service/cli.py`, add to the dispatch dict (line ~16):

```python
        "install": lifecycle.install, "uninstall": lifecycle.uninstall,
        "start": lifecycle.start, "stop": lifecycle.stop,
        "status": lifecycle.status, "upgrade": lifecycle.upgrade,
        "refresh": lifecycle.refresh_config,
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_proxy_lifecycle.py -q
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/proxy_service/lifecycle.py harness/proxy_service/cli.py tests/test_proxy_lifecycle.py
git commit -m "feat(proxy): refresh_config() — consented regen+restart, upgrade minus download"
```

---

### Task 4: `ProxyRefreshModal` + drifted→consent wiring in the TUI

**Files:**
- Create: `harness/tui/widgets/proxy_refresh_modal.py`
- Modify: `harness/tui/app.py` (`_check_proxy_config_drift` drifted branch ~line 465; two new methods after it)
- Test: `tests/test_tui_widgets.py` (also UPDATE the existing `test_check_proxy_config_drift_logs_when_drifted` — drifted now prompts instead of logging directly)

**Interfaces:**
- Consumes: `lifecycle.refresh_config()` (Task 3).
- Produces: `ProxyRefreshModal(ModalScreen)` dismissing `True`/`False`; `HarnessTui._show_proxy_refresh_prompt()`; `HarnessTui._do_proxy_refresh()` (async). No CSS needed — mirrors `CronInstallModal`, which uses ModalScreen defaults.

- [ ] **Step 1: Write the modal (mirror of `cron_install_modal.py`)**

Create `harness/tui/widgets/proxy_refresh_modal.py`:

```python
"""ProxyRefreshModal — consent prompt when the proxy config has drifted.

Pushed programmatically from HarnessTui._show_proxy_refresh_prompt during
on_mount (never key-bound). Dismisses True (regenerate + restart now) or False
(not now — fall back to the #292 log line). The refresh side-effect lives in
the app callback so this modal has no lifecycle import and stays trivially
testable. Restart is user-consented by construction — no code path restarts
the machine-global proxy unattended (#292 hard constraint).
"""
from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ProxyRefreshModal(ModalScreen):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="proxy-refresh-box"):
            yield Static(
                "[b]Proxy config is stale[/b]   [$muted]esc = not now[/]",
                id="proxy-refresh-title",
                markup=True,
            )
            yield Static(
                "NEURALWATT_API_KEY (or the served model list) changed since the "
                "last install. Regenerate the proxy config and restart the proxy "
                "now? In-flight requests from other sessions may be dropped.",
                id="proxy-refresh-body",
            )
            yield Button("Restart now", id="proxy-refresh-yes", variant="primary")
            yield Button("Not now", id="proxy-refresh-no")

    @on(Button.Pressed, "#proxy-refresh-yes")
    def _yes(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#proxy-refresh-no")
    def _no(self) -> None:
        self.dismiss(False)

    def action_cancel(self) -> None:
        self.dismiss(False)
```

- [ ] **Step 2: Write the failing app-wiring tests**

In `tests/test_tui_widgets.py`, UPDATE `test_check_proxy_config_drift_logs_when_drifted`: the drifted branch now calls `_show_proxy_refresh_prompt` (stub records it) instead of logging directly. Then append:

```python
def test_drifted_prompts_instead_of_bare_log(monkeypatch):
    import harness.tui.app as app_mod
    monkeypatch.setattr(
        "harness.proxy_service.config_gen.config_drift", lambda env=None: "drifted"
    )
    # If the file has no shared stub helper, construct the stub inline exactly
    # as the existing test_check_proxy_config_drift_logs_when_drifted does.
    stub = _make_drift_stub()            # same stub pattern as the neighbors
    prompted = []
    stub._show_proxy_refresh_prompt = lambda: prompted.append(True)
    app_mod.HarnessTui._check_proxy_config_drift(stub)
    assert prompted == [True]


def test_refresh_prompt_decline_falls_back_to_log(monkeypatch):
    import harness.tui.app as app_mod
    logged = []
    pushed = []

    class Stub:
        log = staticmethod(lambda msg: logged.append(msg))
        run_worker = staticmethod(lambda *a, **k: pushed.append(("worker", a)))
        def push_screen(self, modal, callback):
            pushed.append(("screen", type(modal).__name__))
            callback(False)              # user declines

    app_mod.HarnessTui._show_proxy_refresh_prompt(Stub())
    assert ("screen", "ProxyRefreshModal") in pushed
    assert any("proxy config stale" in m for m in logged)
    assert not any(p[0] == "worker" for p in pushed)


def test_refresh_prompt_accept_runs_refresh_worker(monkeypatch):
    import harness.tui.app as app_mod
    workers = []

    class Stub:
        log = staticmethod(lambda msg: None)
        def run_worker(self, coro, thread=False):
            coro.close()                 # don't actually run it in this unit test
            workers.append(True)
        def push_screen(self, modal, callback):
            callback(True)               # user accepts

    app_mod.HarnessTui._show_proxy_refresh_prompt(Stub())
    assert workers == [True]
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_tui_widgets.py -q
```
Expected: FAIL — `_show_proxy_refresh_prompt` doesn't exist; the old drifted test fails on the changed behavior (update it in this task).

- [ ] **Step 4: Implement the app wiring**

In `harness/tui/app.py` `_check_proxy_config_drift()`, replace the drifted branch:

```python
            if _proxy_config_gen.config_drift(env=machine_global) == "drifted":
                self._show_proxy_refresh_prompt()
```

Add after `_check_proxy_config_drift`:

```python
    def _show_proxy_refresh_prompt(self) -> None:
        """One-keypress consent for a drifted proxy config (spec B). Accept →
        regenerate + restart via lifecycle.refresh_config in a worker thread;
        decline/esc → the #292 log line, unchanged. on_mount runs once, so this
        prompts at most once per TUI session."""
        from harness.tui.widgets.proxy_refresh_modal import ProxyRefreshModal

        def _on_choice(accepted) -> None:
            if accepted:
                self.run_worker(self._do_proxy_refresh(), thread=False)
            else:
                self.log("proxy config stale — run `dn proxy upgrade` to pick up "
                         "NEURALWATT_API_KEY changes")

        self.push_screen(ProxyRefreshModal(), callback=_on_choice)

    async def _do_proxy_refresh(self) -> None:
        import asyncio
        from harness.proxy_service import lifecycle
        result = await asyncio.get_running_loop().run_in_executor(
            None, lifecycle.refresh_config)
        for line in result.splitlines():
            self.log(line)
```

- [ ] **Step 5: Run tests + snapshot baseline**

```bash
/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_tui_widgets.py tests/test_tui_snapshots.py -q
```
Expected: PASS. Snapshot tests must be untouched — they run hermetic (drift is "missing" there, which stays silent by design; #292 comment in `_check_proxy_config_drift` documents this).

- [ ] **Step 6: Commit**

```bash
git add harness/tui/widgets/proxy_refresh_modal.py harness/tui/app.py tests/test_tui_widgets.py
git commit -m "feat(tui): consent prompt on drifted proxy config — restart now / not now"
```

---

### Task 5: `ModelStatus.model_id` + reasoned `resolve_or_warn` warnings

**Files:**
- Modify: `harness/model_availability.py`
- Test: `tests/test_model_availability.py`

**Interfaces:**
- Consumes: `model_ids.matches(a, b)` (existing).
- Produces: `ModelStatus` gains `model_id: str | None = None` (LAST field, defaulted — additive, frozen dataclass, existing constructors unaffected); `reconcile()` fills it with the catalog model id; `resolve_or_warn()` unchanged signature, warning text now names the reason (`login_needed` / `stale_config`) and the fix. Task 6 logs this warning.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_model_availability.py` (reuse the `_CATALOG` fixture at the top of the file):

```python
def test_reconcile_carries_catalog_model_id():
    out = av.reconcile(_CATALOG, proxy_ids=[], keys_present={"neuralwatt": False, "anthropic": False})
    assert all(s.model_id is not None for s in out)
    assert any(s.model_id == "glm-5.2" for s in out)


def test_warning_names_login_needed_reason():
    out = av.reconcile(_CATALOG, proxy_ids=[], keys_present={"neuralwatt": False, "anthropic": False})
    model, warning = av.resolve_or_warn("glm-5.2", out)
    assert model == "glm-5.2"                        # NEVER substituted
    assert "login" in warning.lower() or "key" in warning.lower()
    assert "neuralwatt" in warning


def test_warning_names_stale_config_reason():
    out = av.reconcile(_CATALOG, proxy_ids=[], keys_present={"neuralwatt": True, "anthropic": False})
    model, warning = av.resolve_or_warn("glm-5.2", out)
    assert model == "glm-5.2"
    assert "stale" in warning.lower()
    assert "dn proxy" in warning


def test_warning_generic_for_unknown_model():
    out = av.reconcile(_CATALOG, proxy_ids=[], keys_present={"neuralwatt": False, "anthropic": False})
    model, warning = av.resolve_or_warn("made-up-model", out)
    assert model == "made-up-model" and warning
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_model_availability.py -q
```
Expected: new tests FAIL (`model_id` attribute missing; warning text generic).

- [ ] **Step 3: Implement**

In `harness/model_availability.py`:

```python
@dataclass(frozen=True)
class ModelStatus:
    provider: str
    display_name: str
    bind_id: str | None      # proxy id to send; None until available
    status: str              # "available" | "login_needed" | "stale_config"
    model_id: str | None = None   # catalog id — lets resolve_or_warn name the reason
```

In `reconcile()`, add the field to the append:

```python
            out.append(ModelStatus(prov.id, m.name, bind, status, model_id=m.id))
```

Replace `resolve_or_warn()`:

```python
def resolve_or_warn(configured_model, statuses):
    """Return (model, warning|None). Never substitutes: returns the configured
    model verbatim; if it isn't an available bind_id, returns a warning string
    that names the reason (login/key missing vs stale proxy config) and the fix."""
    for s in statuses:
        if s.status == "available" and s.bind_id is not None and model_ids.matches(s.bind_id, configured_model):
            return configured_model, None
    match = next((s for s in statuses
                  if s.model_id is not None and model_ids.matches(s.model_id, configured_model)), None)
    if match is not None and match.status == "login_needed":
        warning = (f"Configured model '{configured_model}' is not served by the proxy — "
                   f"no key/login for provider '{match.provider}'. Set its key in "
                   f"~/.config/harness/.env or run `dn proxy login {match.provider}`.")
    elif match is not None and match.status == "stale_config":
        warning = (f"Configured model '{configured_model}' is not served by the proxy — "
                   f"proxy config is stale. Accept the refresh prompt or run `dn proxy refresh`.")
    else:
        warning = (f"Configured model '{configured_model}' is not available from the "
                   f"proxy right now — it may need login or a proxy config refresh.")
    return configured_model, warning
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_model_availability.py tests/test_model_picker_render.py -q
```
Expected: PASS (picker render consumes `ModelStatus` positionally-compatibly — the new field is last and defaulted).

- [ ] **Step 5: Commit**

```bash
git add harness/model_availability.py tests/test_model_availability.py
git commit -m "feat(models): resolve_or_warn names the reason — login_needed vs stale_config"
```

---

### Task 6: Session-start availability warning in the TUI (fail-open)

**Files:**
- Modify: `harness/tui/app.py` (`on_mount` after `_check_proxy_config_drift()` ~line 437; one new async method)
- Test: `tests/test_tui_widgets.py`

**Interfaces:**
- Consumes: `model_availability.reconcile`/`resolve_or_warn` (Task 5), existing `self._fetch_models()` (app.py ~line 1028), `model_catalog.providers()`, `model_keys.keys_present`, `management._get`, `config_gen.ensure_management_password`, `self._worker_model_id`, `self._launch_persona`, `self.model` (`"mock"|"vibeproxy"`).
- Produces: `HarnessTui._warn_if_model_unserved()` (async) — logs at most one line; silent on any failure or in mock mode.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tui_widgets.py`:

```python
def test_warn_if_model_unserved_logs_when_missing():
    import asyncio
    import harness.tui.app as app_mod
    logged = []

    class Stub:
        model = "vibeproxy"
        _worker_model_id = "glm-5.2"
        _launch_persona = "default"
        log = staticmethod(lambda msg: logged.append(msg))
        async def _fetch_models(self):
            return ["claude-opus-4-8"]           # configured model NOT served

    asyncio.run(app_mod.HarnessTui._warn_if_model_unserved(Stub()))
    assert logged and "glm-5.2" in logged[0] and "running anyway" in logged[0]


def test_warn_if_model_unserved_silent_when_served():
    import asyncio
    import harness.tui.app as app_mod
    logged = []

    class Stub:
        model = "vibeproxy"
        _worker_model_id = "glm-5.2"
        _launch_persona = "default"
        log = staticmethod(lambda msg: logged.append(msg))
        async def _fetch_models(self):
            return ["glm-5.2"]

    asyncio.run(app_mod.HarnessTui._warn_if_model_unserved(Stub()))
    assert logged == []


def test_warn_if_model_unserved_fail_open_on_fetch_error():
    import asyncio
    import harness.tui.app as app_mod
    logged = []

    class Stub:
        model = "vibeproxy"
        _worker_model_id = "glm-5.2"
        _launch_persona = "default"
        log = staticmethod(lambda msg: logged.append(msg))
        async def _fetch_models(self):
            raise OSError("proxy down")

    asyncio.run(app_mod.HarnessTui._warn_if_model_unserved(Stub()))
    assert logged == []


def test_warn_if_model_unserved_skips_mock_mode():
    import asyncio
    import harness.tui.app as app_mod
    fetched = []

    class Stub:
        model = "mock"
        _worker_model_id = None
        _launch_persona = "default"
        log = staticmethod(lambda msg: fetched.append(("log", msg)))
        async def _fetch_models(self):
            fetched.append(("fetch",))
            return []

    asyncio.run(app_mod.HarnessTui._warn_if_model_unserved(Stub()))
    assert fetched == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_tui_widgets.py -q
```
Expected: FAIL — `_warn_if_model_unserved` not defined.

- [ ] **Step 3: Implement**

In `harness/tui/app.py` `on_mount`, directly after `self._check_proxy_config_drift()`:

```python
        self.run_worker(self._warn_if_model_unserved(), thread=False)
```

Add the method (near `_check_proxy_config_drift`):

```python
    async def _warn_if_model_unserved(self) -> None:
        """Session-start availability warning (#290's warn half): one visible
        line when the configured worker model isn't served by the proxy —
        BEFORE the first turn dies in an opaque BadGatewayError retry loop.
        Never substitutes (resolve_or_warn contract). Fail-open: any error is
        silent; skipped entirely in mock mode (snapshot tests run mock, and
        mock has no proxy to reconcile against)."""
        try:
            if self.model == "mock" or not self._worker_model_id:
                return
            proxy_ids = await self._fetch_models()
            from harness import model_catalog, model_keys, model_availability
            from harness.proxy_service import management, config_gen as _pcg
            try:
                pw = _pcg.ensure_management_password()
                auth = management._get("get-auth-status", pw).json()
            except Exception:
                auth = {}
            keys = model_keys.keys_present(auth_status=auth, environ=os.environ)
            statuses = model_availability.reconcile(
                model_catalog.providers(), proxy_ids, keys)
            _, warning = model_availability.resolve_or_warn(
                self._worker_model_id, statuses)
            if warning:
                self.log(f"{self._launch_persona}: {warning} — running anyway")
        except Exception:
            return
```

Note: the first two stub tests bypass the reconcile path only if the fetch
already decides the outcome — they don't. The stubs above WILL hit
`model_catalog.providers()` etc. with real imports; those are pure/file-backed
and hermetic (`harness/data/models_snapshot.json`). `ensure_management_password`
writes under `paths.secret_path()` — in tests, monkeypatch is NOT needed because
the inner try/except already degrades `auth` to `{}` on any error, and
`keys_present` with empty auth still returns a dict. If the first test proves
flaky on key-presence (warning text differs by whether NEURALWATT_API_KEY is in
the test process env), monkeypatch `model_keys.keys_present` to
`lambda **k: {}` in the test for determinism.

- [ ] **Step 4: Run tests + snapshots to verify green**

```bash
/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_tui_widgets.py tests/test_tui_snapshots.py -q
```
Expected: PASS. Snapshot baseline unchanged (mock mode skips the check).

- [ ] **Step 5: Commit**

```bash
git add harness/tui/app.py tests/test_tui_widgets.py
git commit -m "feat(tui): session-start warning when configured model isn't served (#290 warn half)"
```

---

### Task 7: Full suite + docs touch-up

**Files:**
- Modify: `docs/superpowers/specs/2026-07-01-proxy-config-selfheal-design.md` (mark Status: Implemented)
- Test: everything

- [ ] **Step 1: Run the full suite**

```bash
cd /Users/alberto/Work/Quiubo/harness/.worktrees/proxy-config-selfheal
/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/ -q
```
Expected: PASS (0 failures). If proxy-reachability flakes appear, re-run with `HARNESS_ROUTER_STUB=1` per the repo's established stub seam; only proxy-liveness-dependent tests may vary.

- [ ] **Step 2: Update spec status + commit**

Change the spec header line `**Status:** Revised — pending user re-approval …` to `**Status:** Implemented (this branch)`.

```bash
git add docs/superpowers/specs/2026-07-01-proxy-config-selfheal-design.md
git commit -m "docs: mark proxy-config-selfheal spec implemented"
```
