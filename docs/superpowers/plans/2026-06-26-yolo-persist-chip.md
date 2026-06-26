# YOLO Persistence + Footer Chip Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make YOLO (auto-allow-every-command) mode persist across launches via an opt-in `yolo_pinned` flag in `done.conf`, surfaced by a clickable amber footer chip and a `/yolo` command.

**Architecture:** Mirror the existing `done.conf` model-persistence machinery. Two separate states: the ephemeral live gate (`HarnessAgent._yolo`, already enforced in `_auto_allow`) and a persisted `yolo_pinned` bool. Clicking the chip / `/yolo` toggles the live gate; `/yolo pin|unpin` writes the persisted flag (best-effort). Launch precedence: `--yolo` flag > persisted pin > off. The footer chip reuses `StatusChip`; a new clickable-footer-mode-chip pattern is documented in the component catalog.

**Tech Stack:** Python 3.11+, Textual (TUI), stdlib `tomllib` (read) + hand-rolled TOML writer, pytest. Agent is a separate subprocess from the TUI, spoken to over ACP; persistence happens in the ACP process via an ext-method.

## Global Constraints

- **Work in the worktree.** All paths below are relative to the worktree root `/Users/alberto/Work/Quiubo/harness/.claude/worktrees/yolo-persist-chip`. Never edit the primary checkout. After each task verify `git -C /Users/alberto/Work/Quiubo/harness status --short` is empty.
- **Test command:** `.venv/bin/python -m pytest tests/ -q` (run from the worktree root). For a single test: `.venv/bin/python -m pytest tests/test_x.py::test_y -v`.
- **No new dependencies.** Read TOML with stdlib `tomllib`; write with the existing hand-rolled serializer.
- **Tokens only, no hardcoded hex** (`components.md` principle 3). Colors via `$token`. New glyph goes in `harness/tui/tokens.py`.
- **Persistence is best-effort:** a failed config write must never break the live toggle or raise into the boot path.
- **`_auto_allow()` semantics are unchanged** — it still `return self._yolo`. We only change how `_yolo` is set and shown.
- **Commit message footer:** end every commit body with
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

---

### Task 1: `config.py` — round-trip `yolo_pinned` + `update_default` merge

**Files:**
- Modify: `harness/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: existing `AgentConfig`, `load`, `load_default`, `save_default`, `_serialize`, `_quote`, `RESERVED_KEY`.
- Produces:
  - `AgentConfig` gains field `yolo_pinned: bool = False` (frozen dataclass).
  - `def update_default(*, backend: str | None = None, model: str | None = None, yolo_pinned: bool | None = None) -> None` — loads the existing `[agents.default]` (or a blank base), overlays only the passed kwargs, writes. Preserves untouched fields and all other agent tables.
  - `def yolo_pinned() -> bool` — `load_default().yolo_pinned` if a default exists, else `False`.
  - `save_default(cfg)` keeps replacing `backend`+`model` but now **preserves an existing `yolo_pinned`** (it delegates to the merge).

**Background:** Today `save_default` (`config.py:106-118`) rebuilds the default from scratch: `agents[RESERVED_KEY] = AgentConfig(backend=cfg.backend, model=cfg.model)`, dropping any other field. Adding `yolo_pinned` naively would make `set_model` silently wipe a pin. The fix is a merge helper.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_config.py`)

```python
def test_load_reads_yolo_pinned_true(tmp_path):
    _write(tmp_path, (
        '[agents.default]\nbackend = "vibeproxy"\nmodel = "gpt-5.4"\n'
        'yolo_pinned = true\n'
    ))
    assert config.load_default() == config.AgentConfig(
        backend="vibeproxy", model="gpt-5.4", yolo_pinned=True)


def test_load_yolo_pinned_defaults_false_when_absent(tmp_path):
    _write(tmp_path, '[agents.default]\nbackend = "mock"\nmodel = "x"\n')
    assert config.load_default().yolo_pinned is False


def test_load_yolo_pinned_non_bool_is_false(tmp_path):
    _write(tmp_path, (
        '[agents.default]\nbackend = "mock"\nmodel = "x"\n'
        'yolo_pinned = "nope"\n'      # hand-edit error -> treated as False, not fatal
    ))
    assert config.load_default().yolo_pinned is False


def test_yolo_pinned_helper_false_when_no_config(tmp_path):
    assert config.yolo_pinned() is False


def test_yolo_pinned_helper_reads_default(tmp_path):
    config.update_default(backend="mock", model="x", yolo_pinned=True)
    assert config.yolo_pinned() is True


def test_update_default_pin_preserves_backend_and_model(tmp_path):
    config.save_default(config.AgentConfig(backend="vibeproxy", model="gpt-5.4"))
    config.update_default(yolo_pinned=True)
    assert config.load_default() == config.AgentConfig(
        backend="vibeproxy", model="gpt-5.4", yolo_pinned=True)


def test_save_default_preserves_existing_pin(tmp_path):
    # The regression the merge fix exists to prevent: changing the model must
    # NOT clear a pin the user set earlier.
    config.update_default(backend="vibeproxy", model="old", yolo_pinned=True)
    config.save_default(config.AgentConfig(backend="vibeproxy", model="new"))
    got = config.load_default()
    assert got.model == "new"
    assert got.yolo_pinned is True


def test_update_default_unpin_writes_false_and_round_trips(tmp_path):
    config.update_default(backend="mock", model="x", yolo_pinned=True)
    config.update_default(yolo_pinned=False)
    assert config.load_default().yolo_pinned is False


def test_serialize_omits_yolo_pinned_when_false(tmp_path):
    config.save_default(config.AgentConfig(backend="mock", model="x"))
    assert "yolo_pinned" not in config.conf_path().read_text()


def test_serialize_emits_yolo_pinned_true(tmp_path):
    config.update_default(backend="mock", model="x", yolo_pinned=True)
    assert "yolo_pinned = true" in config.conf_path().read_text()


def test_update_default_preserves_other_agents(tmp_path):
    _write(tmp_path, (
        'schema_version = 1\n'
        '[agents.default]\nbackend = "mock"\nmodel = "old"\n'
        '[agents.6f1c-uuid]\nname = "bill"\nbackend = "vibeproxy"\nmodel = "claude-opus-4-8"\n'
    ))
    config.update_default(yolo_pinned=True)
    agents = config.load()
    assert agents["default"] == config.AgentConfig(backend="mock", model="old", yolo_pinned=True)
    assert agents["6f1c-uuid"] == config.AgentConfig(
        backend="vibeproxy", model="claude-opus-4-8", name="bill")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_config.py -q`
Expected: FAIL — `AgentConfig` has no `yolo_pinned`; `config.update_default` / `config.yolo_pinned` don't exist.

- [ ] **Step 3: Implement in `harness/config.py`**

Add the field to the dataclass (`config.py:29-33`):

```python
@dataclass(frozen=True)
class AgentConfig:
    backend: str
    model: str
    name: str | None = None
    yolo_pinned: bool = False   # persisted "always launch in YOLO"
```

In `load()` (inside the per-agent loop, after the `name` line at `config.py:67-72`), read the flag:

```python
        pinned = table.get("yolo_pinned")
        out[key] = AgentConfig(
            backend=backend,
            model=model,
            name=name if isinstance(name, str) else None,
            yolo_pinned=pinned if isinstance(pinned, bool) else False,
        )
```

In `_serialize()` (after the `model` line at `config.py:101`), emit only when True:

```python
        if cfg.yolo_pinned:
            lines.append("yolo_pinned = true")
```

Replace `save_default` and add the merge helper + read helper. Rewrite the bottom of the module (from `def save_default` onward) as:

```python
def update_default(
    *,
    backend: str | None = None,
    model: str | None = None,
    yolo_pinned: bool | None = None,
) -> None:
    """Upsert [agents.default], overlaying ONLY the kwargs passed (None = leave
    unchanged). Preserves untouched default fields and every other agent table.
    Atomic write under a created config dir. Best-effort: callers that must not
    fail on I/O should guard the call."""
    agents = load()
    cur = agents.get(RESERVED_KEY)
    base_backend = cur.backend if cur is not None else ""
    base_model = cur.model if cur is not None else ""
    base_pinned = cur.yolo_pinned if cur is not None else False
    agents[RESERVED_KEY] = AgentConfig(
        backend=base_backend if backend is None else backend,
        model=base_model if model is None else model,
        name=None,                                   # default carries no name
        yolo_pinned=base_pinned if yolo_pinned is None else yolo_pinned,
    )
    text = _serialize(agents)

    path = conf_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def save_default(cfg: AgentConfig) -> None:
    """Upsert the default's backend+model, preserving its yolo_pinned and all
    other agent tables. Thin wrapper over update_default (kept for the existing
    set_model call site + tests)."""
    update_default(backend=cfg.backend, model=cfg.model)


def yolo_pinned() -> bool:
    """Whether the persisted default is pinned to launch in YOLO. False when
    absent/unreadable."""
    cur = load_default()
    return cur.yolo_pinned if cur is not None else False
```

> Note: `save_default` no longer writes the cfg's `yolo_pinned` directly (it ignores any pin on the passed cfg and preserves the persisted one). That's intentional — `set_model` passes a cfg with `yolo_pinned=False` (the dataclass default) and must NOT clear an existing pin.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_config.py -q`
Expected: PASS (new tests + all pre-existing config tests, including `test_save_default_preserves_other_agents` and `test_save_default_round_trips`).

- [ ] **Step 5: Commit**

```bash
git add harness/config.py tests/test_config.py
git commit -m "feat(config): persist yolo_pinned with a merge-safe update_default

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `acp_agent.py` — `harness/set_yolo` ext-method

**Files:**
- Modify: `harness/acp_agent.py` (the `ext_method` dispatcher, `acp_agent.py:53-67`)
- Test: `tests/test_acp_agent.py`

**Interfaces:**
- Consumes: `self._yolo` (live gate), `config.update_default`, `config.yolo_pinned` from Task 1.
- Produces: a new branch in `ext_method`:
  - method `"harness/set_yolo"`, params `{active?: bool, pin?: bool}`.
  - `active` (optional): set the live gate. Omitted → unchanged.
  - `pin` (optional): `True`→`update_default(yolo_pinned=True)`, `False`→`update_default(yolo_pinned=False)`, omitted → persistence untouched.
  - Returns `{"ok": True, "active": <bool>, "pinned": <bool>}`. `pinned` is read best-effort (`config.yolo_pinned()`), defaulting to the pre-call value if the read raises.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_acp_agent.py`)

```python
def test_set_yolo_active_true_sets_gate_no_persist():
    agent = _make_agent()
    agent._yolo = False
    result = asyncio.run(agent.ext_method("harness/set_yolo", {"active": True}))
    assert agent._yolo is True
    assert result["ok"] is True and result["active"] is True
    assert config.load_default() is None      # active alone never persists


def test_set_yolo_active_false_turns_gate_off():
    agent = _make_agent()
    agent._yolo = True
    asyncio.run(agent.ext_method("harness/set_yolo", {"active": False}))
    assert agent._yolo is False


def test_set_yolo_pin_true_persists():
    agent = _make_agent()
    asyncio.run(agent.ext_method("harness/set_yolo", {"active": True, "pin": True}))
    assert config.yolo_pinned() is True


def test_set_yolo_pin_false_unpins():
    config.update_default(backend="vibeproxy", model="gpt-5.4", yolo_pinned=True)
    agent = _make_agent()
    asyncio.run(agent.ext_method("harness/set_yolo", {"pin": False}))
    assert config.yolo_pinned() is False


def test_set_yolo_omitted_pin_does_not_touch_persistence():
    config.update_default(backend="vibeproxy", model="gpt-5.4", yolo_pinned=True)
    agent = _make_agent()
    asyncio.run(agent.ext_method("harness/set_yolo", {"active": False}))
    assert config.yolo_pinned() is True       # pin untouched by a live-only toggle


def test_set_yolo_survives_persist_failure(monkeypatch):
    def boom(**kw):
        raise OSError("disk full")
    monkeypatch.setattr(config, "update_default", boom)
    agent = _make_agent()
    result = asyncio.run(agent.ext_method("harness/set_yolo", {"active": True, "pin": True}))
    assert result["ok"] is True and agent._yolo is True   # live toggle still succeeds
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_acp_agent.py -q`
Expected: FAIL — `ext_method` returns `{}` for unknown method, so assertions fail.

- [ ] **Step 3: Implement in `harness/acp_agent.py`**

In `ext_method`, after the `if method == "harness/set_model":` block (before the final `return {}`):

```python
        if method == "harness/set_yolo":
            params = params or {}
            if "active" in params:
                self._yolo = bool(params["active"])
            pin = params.get("pin")
            if pin is not None:
                try:                       # best-effort: a failed write never breaks the toggle
                    config.update_default(yolo_pinned=bool(pin))
                except Exception:
                    pass
            try:
                pinned = config.yolo_pinned()
            except Exception:
                pinned = False
            return {"ok": True, "active": self._yolo, "pinned": pinned}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_acp_agent.py -q`
Expected: PASS (new tests + the existing `set_model` tests untouched).

- [ ] **Step 5: Commit**

```bash
git add harness/acp_agent.py tests/test_acp_agent.py
git commit -m "feat(acp): harness/set_yolo ext-method (live gate + best-effort pin)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `tokens.py` + `StatusChip.for_yolo` — the chip rendering

**Files:**
- Modify: `harness/tui/tokens.py` (add the `bypass` glyph)
- Modify: `harness/tui/widgets/status_chip.py` (add `for_yolo` factory)
- Test: `tests/test_tui_tokens.py`, `tests/test_tui_widgets.py`

**Interfaces:**
- Consumes: `GLYPH` from `tokens.py`, `StatusChip.__init__(label, color_token)`.
- Produces:
  - `GLYPH["bypass"] == "!"`.
  - `StatusChip.for_yolo(active: bool, pinned: bool) -> StatusChip`:
    - off → label `"• ask"` (uses `GLYPH["idle"]`), token `"muted"`.
    - on, not pinned → label `"! YOLO"`, token `"scheduled"`.
    - on, pinned → label `"! YOLO · pin"`, token `"scheduled"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tui_tokens.py`:

```python
def test_bypass_glyph_present():
    from harness.tui.tokens import GLYPH
    assert GLYPH["bypass"] == "!"
```

Append to `tests/test_tui_widgets.py` (it already imports `StatusChip`):

```python
def test_status_chip_for_yolo_off_is_muted_ask():
    chip = StatusChip.for_yolo(active=False, pinned=False)
    markup = chip._Static__content
    assert "ask" in chip._label
    assert "$muted" in markup
    assert "YOLO" not in chip._label


def test_status_chip_for_yolo_on_is_amber_yolo():
    chip = StatusChip.for_yolo(active=True, pinned=False)
    assert "YOLO" in chip._label
    assert "pin" not in chip._label
    assert "$scheduled" in chip._Static__content
    assert "!" in chip._label                 # the bypass glyph


def test_status_chip_for_yolo_pinned_shows_pin_marker():
    chip = StatusChip.for_yolo(active=True, pinned=True)
    assert "YOLO" in chip._label and "pin" in chip._label
    assert "$scheduled" in chip._Static__content
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_tui_tokens.py tests/test_tui_widgets.py -q`
Expected: FAIL — `GLYPH["bypass"]` KeyError; `StatusChip.for_yolo` AttributeError.

- [ ] **Step 3: Implement**

In `harness/tui/tokens.py`, add to the `GLYPH` dict (after `"awaiting": "?",`):

```python
    "bypass": "!",        # permission-bypass / YOLO mode (no clock/dot fits)
```

In `harness/tui/widgets/status_chip.py`, import the glyph and add the factory. Change the import at the top (`status_chip.py:10`) to include nothing new (GLYPH already imported). Add inside `class StatusChip`, after `from_state` (`status_chip.py:62-65`):

```python
    @classmethod
    def for_yolo(cls, active: bool, pinned: bool) -> "StatusChip":
        """The permission-mode chip. Off → muted '• ask'; on → amber '! YOLO'
        (+ ' · pin' when persisted). StatusChip has no separate glyph slot, so
        the leading glyph is baked into the label — state survives monochrome
        terminals via color + glyph + weight together."""
        if not active:
            return cls(f"{GLYPH['idle']} ask", "muted")
        suffix = " · pin" if pinned else ""
        return cls(f"{GLYPH['bypass']} YOLO{suffix}", "scheduled")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tui_tokens.py tests/test_tui_widgets.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/tui/tokens.py harness/tui/widgets/status_chip.py tests/test_tui_tokens.py tests/test_tui_widgets.py
git commit -m "feat(tui): StatusChip.for_yolo + bypass glyph

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `tui_main.py` — `_resolve_yolo` + wire `yolo` into the TUI

**Files:**
- Modify: `harness/tui_main.py`
- Test: `tests/test_tui_main.py`

**Interfaces:**
- Consumes: `config.yolo_pinned` (Task 1); existing `_resolve_model`, `main`, `_relaunch_args`.
- Produces:
  - `def _resolve_yolo(flag: bool) -> bool` — `True` if `flag` else `config.yolo_pinned()`.
  - `main()` computes `yolo = _resolve_yolo(args.yolo)`, normalizes `args.yolo = yolo`, gates `agent_cmd.append("--yolo")` on it, and passes `yolo=yolo` to `HarnessTui(...)`.
- `_relaunch_args` is **unchanged** (still reads `args.yolo`); because `main` now normalizes `args.yolo` to the resolved value before constructing the app, a pinned launch re-execs with `--yolo`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_tui_main.py`)

```python
def test_resolve_yolo_flag_forces_on(isolated_config):
    # even with no pin, the flag wins
    assert tui_main._resolve_yolo(True) is True


def test_resolve_yolo_uses_pin_when_flag_absent(isolated_config):
    from harness import config
    config.update_default(backend="vibeproxy", model="x", yolo_pinned=True)
    assert tui_main._resolve_yolo(False) is True


def test_resolve_yolo_off_when_no_flag_no_pin(isolated_config):
    assert tui_main._resolve_yolo(False) is False


def test_main_passes_resolved_yolo_to_app(isolated_config, monkeypatch):
    from harness import config
    config.update_default(backend="mock", model="x", yolo_pinned=True)
    captured = {}

    class _FakeApp:
        def __init__(self, **kw):
            captured.update(kw)
        def run(self):
            pass

    monkeypatch.setattr(tui_main, "HarnessTui", _FakeApp)
    monkeypatch.setattr(tui_main.paths, "load_env", lambda cwd: None)
    tui_main.main(["--model", "mock", "--cwd", str(isolated_config)])  # no --yolo flag
    assert captured["yolo"] is True            # picked up from the pin


def test_main_yolo_flag_overrides_absent_pin(isolated_config, monkeypatch):
    captured = {}

    class _FakeApp:
        def __init__(self, **kw):
            captured.update(kw)
        def run(self):
            pass

    monkeypatch.setattr(tui_main, "HarnessTui", _FakeApp)
    monkeypatch.setattr(tui_main.paths, "load_env", lambda cwd: None)
    tui_main.main(["--model", "mock", "--cwd", str(isolated_config), "--yolo"])
    assert captured["yolo"] is True
```

> The pre-existing `_FakeApp` definitions in this file accept `**kw`, so adding the `yolo=` kwarg is compatible. `isolated_config` fixture already exists in this file.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_tui_main.py -q`
Expected: FAIL — `_resolve_yolo` undefined; `captured["yolo"]` KeyError (app not yet passed `yolo`).

- [ ] **Step 3: Implement in `harness/tui_main.py`**

Add the resolver after `_resolve_model` (`tui_main.py:31`):

```python
def _resolve_yolo(flag: bool) -> bool:
    """--yolo forces auto-allow on; else the persisted pin; else off. Mirrors
    _resolve_model's precedence (explicit flag > done.conf > default)."""
    if flag:
        return True
    return config.yolo_pinned()
```

In `main()`, after `backend, model_override = _resolve_model(args.model)` (`tui_main.py:81`) add:

```python
    yolo = _resolve_yolo(args.yolo)
    args.yolo = yolo                  # normalize so /reload re-execs with the resolved state
```

Replace the agent-command yolo gate (`tui_main.py:95-96`) — it already reads `args.yolo`, which is now the resolved value, so it is correct as-is. Then pass `yolo` into the app constructor (`tui_main.py:97-98`):

```python
    app = HarnessTui(agent_cmd=agent_cmd, cwd=cwd, model=backend,
                     worker_model_id=worker_model_id, yolo=yolo)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tui_main.py -q`
Expected: PASS. (The existing relaunch tests at lines 13-40 still pass: `_relaunch_args` reads `args.yolo` unchanged.)

> If `test_main_seeds_worker_model_id_from_persisted_model` or the `_run_main_capturing` tests fail because their `_FakeApp.__init__(**kw)` now also receives `yolo`, that is expected to be fine — they swallow `**kw`. No change needed.

- [ ] **Step 5: Commit**

```bash
git add harness/tui_main.py tests/test_tui_main.py
git commit -m "feat(tui): resolve YOLO from flag>pin>off and pass to the app

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: `app.py` + `app.tcss` — mount the clickable chip

**Files:**
- Modify: `harness/tui/app.py`
- Modify: `harness/tui/app.tcss`
- Test: `tests/test_tui_pilot.py` (pilot click test)

**Interfaces:**
- Consumes: `StatusChip.for_yolo` (Task 3); `config.yolo_pinned` (Task 1); existing `_mount_status_contents`, `_refresh_status`, `_conn`, `ext_method` pattern from `_reapply_model`.
- Produces:
  - `HarnessTui.__init__(... , yolo: bool = False)` storing `self._yolo` and `self._yolo_pinned = config.yolo_pinned()`.
  - A `#statusbar-mode` chip mounted in `_mount_status_contents`.
  - `def action_toggle_yolo(self) -> None` (sync wrapper launching a worker) that flips the live state, calls `ext_method("harness/set_yolo", {"active": <new>})` best-effort, and refreshes the chip.
  - `def _refresh_yolo_chip(self) -> None` updating the chip in place.
  - The chip widget posts to `action_toggle_yolo` on click.

**Click mechanism:** No new widget class. Textual auto-dispatches a `Click` to a `def on_click(self, event)` method by name — **no import of `Click`/`events`/`on` is required**. The app's `on_click` guards on the clicked widget's id (`#statusbar-mode`) and calls `action_toggle_yolo()`. Verified: `app.py` has **no** existing `on_click`, and `StatusChip`/`config` are **not** imported there yet — both imports must be added (see Step 3).

- [ ] **Step 1: Write the failing test** (append to `tests/test_tui_pilot.py`)

```python
def test_yolo_chip_click_toggles_state():
    """Clicking the footer mode chip flips the live YOLO state and the chip text."""
    import asyncio as _asyncio
    from harness.tui.app import HarnessTui
    from textual.widgets import Static

    REPO_ = __import__("pathlib").Path(__file__).resolve().parent.parent
    FAKE_ = [__import__("sys").executable, str(REPO_ / "tests/fake_agent.py")]

    async def go():
        app = HarnessTui(agent_cmd=FAKE_, cwd=str(REPO_), model="mock", yolo=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            chip = app.query_one("#statusbar-mode", Static)
            assert "ask" in chip._Static__content      # starts off
            app.action_toggle_yolo()
            await pilot.pause(); await pilot.pause()
            assert app._yolo is True
            assert "YOLO" in app.query_one("#statusbar-mode", Static)._Static__content

    _asyncio.run(go())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tui_pilot.py::test_yolo_chip_click_toggles_state -v`
Expected: FAIL — `HarnessTui.__init__` rejects `yolo=`; no `#statusbar-mode`; no `action_toggle_yolo`.

- [ ] **Step 3: Implement in `harness/tui/app.py`**

Add these imports near the other widget imports (after `app.py:47`, the `tool_call_row` import). Verified neither exists in `app.py` today:

```python
from harness.tui.widgets.status_chip import StatusChip
from harness import config as _config
```

Extend `__init__` (`app.py:77-84`) — add the param and two attributes (place after `self._worker_model_id = worker_model_id`):

```python
    def __init__(self, agent_cmd: list[str], cwd: str, model: str,
                 worker_model_id: str | None = None, version: str = "0.5.0",
                 yolo: bool = False) -> None:
        ...
        self._yolo = yolo                          # live gate (TUI mirror of the agent's)
        self._yolo_pinned = _config.yolo_pinned()  # persisted pin, for the chip's '· pin'
```

Mount the chip in `_mount_status_contents` (`app.py:208-211`), after the right static:

```python
        await bar.mount(StatusChip.for_yolo(self._yolo, self._yolo_pinned))
        # give it the id the CSS + click handler target
        self.query_one(StatusChip).id = "statusbar-mode"
```

> Cleaner: build the chip, set `.id` before mount. Replace the two lines above with:
> ```python
>         chip = StatusChip.for_yolo(self._yolo, self._yolo_pinned)
>         chip.id = "statusbar-mode"
>         await bar.mount(chip)
> ```

Add the refresh + toggle methods near `_refresh_status` (`app.py:228-232`):

```python
    def _refresh_yolo_chip(self) -> None:
        try:
            chip = self.query_one("#statusbar-mode", StatusChip)
        except Exception:
            return
        fresh = StatusChip.for_yolo(self._yolo, self._yolo_pinned)
        chip._label = fresh._label
        chip.update(fresh._Static__content)

    def action_toggle_yolo(self) -> None:
        """Flip the live auto-allow gate (chip click / bare /yolo). Persisting is
        a separate gesture (/yolo pin); a click never changes the pin."""
        self._yolo = not self._yolo
        self._refresh_yolo_chip()
        self.run_worker(self._send_set_yolo(active=self._yolo), thread=False)

    async def _send_set_yolo(self, *, active: bool | None = None,
                             pin: bool | None = None) -> None:
        if self._conn is None:
            return
        params: dict = {}
        if active is not None:
            params["active"] = active
        if pin is not None:
            params["pin"] = pin
        try:
            await self._conn.ext_method("harness/set_yolo", params)
        except Exception:
            pass                # older agent / transient error: chip already updated
```

Add the click handler (Textual dispatches `Click` to this method by name; no extra import needed — `app.py` has no existing `on_click`):

```python
    def on_click(self, event) -> None:
        # Footer mode chip: a click anywhere on it toggles YOLO. Guard on the id
        # so other clicks are unaffected.
        widget = getattr(event, "widget", None)
        if widget is not None and getattr(widget, "id", None) == "statusbar-mode":
            self.action_toggle_yolo()
```

In `harness/tui/app.tcss`, after the `#statusbar-right` rule (`app.tcss:105-107`), add:

```css
#statusbar-mode { width: auto; padding: 0 0 0 2; content-align: right middle; }
```

- [ ] **Step 4: Run the test + the full TUI suite**

Run: `.venv/bin/python -m pytest tests/test_tui_pilot.py::test_yolo_chip_click_toggles_state tests/test_tui_app_import.py -v`
Expected: PASS.
Then: `.venv/bin/python -m pytest tests/ -q` — full suite green (no regression in the many pilot tests).

- [ ] **Step 5: Commit**

```bash
git add harness/tui/app.py harness/tui/app.tcss tests/test_tui_pilot.py
git commit -m "feat(tui): clickable YOLO mode chip in the status bar

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: `/yolo` command (toggle | pin | unpin)

**Files:**
- Modify: `harness/tui/commands.py` (handler signature + `/yolo` entry)
- Modify: `harness/tui/app.py` (`_run_slash` passes the trailing arg; app methods for pin/unpin)
- Test: `tests/test_tui_commands.py`

**Background:** Today `_run_slash` (`app.py:378-389`) calls `cmd.handler(self)` with no arg — the text after the command name is discarded. To support `/yolo pin`, handlers gain an optional `arg: str = ""` and `_run_slash` passes the trailing token. Existing handlers ignore it; the existing test `test_reload_clear_handlers_delegate_to_app_actions` calls `handler(app)` and still works because `arg` defaults.

**Interfaces:**
- Consumes: `action_toggle_yolo`, `_send_set_yolo` (Task 5); `Command`, `build_registry`, `resolve_command`.
- Produces:
  - `Command.handler: Callable[[app, str], Awaitable[None]]` — second positional arg `arg` (the text after the command name), defaulting to `""` at every call site.
  - A `/yolo` command whose handler dispatches on `arg`: `""`→toggle, `"pin"`→pin, `"unpin"`→unpin, anything else→a notify line.
  - App methods: `action_yolo_pin()` (sets live on + pins), `action_yolo_unpin()` (unpins, live unchanged).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_tui_commands.py`)

```python
def test_registry_has_yolo():
    names = {c.name for c in build_registry()}
    assert "yolo" in names


def test_yolo_handler_dispatches_on_arg():
    import asyncio
    from harness.tui.commands import build_registry

    class _App:
        def __init__(self): self.calls = []
        def action_toggle_yolo(self): self.calls.append("toggle")
        async def action_yolo_pin(self): self.calls.append("pin")
        async def action_yolo_unpin(self): self.calls.append("unpin")
        def _notify_line(self, m): self.calls.append(("notify", m))

    reg = {c.name: c for c in build_registry()}
    app = _App()
    asyncio.run(reg["yolo"].handler(app, ""))
    asyncio.run(reg["yolo"].handler(app, "pin"))
    asyncio.run(reg["yolo"].handler(app, "unpin"))
    assert app.calls[:3] == ["toggle", "pin", "unpin"]


def test_existing_handlers_accept_optional_arg():
    """Adding the arg param must not break the no-arg call convention."""
    import asyncio
    from harness.tui.commands import build_registry

    class _App:
        def __init__(self): self.called = []
        async def action_reload(self): self.called.append("reload")
        async def action_clear(self): self.called.append("clear")

    reg = {c.name: c for c in build_registry()}
    app = _App()
    asyncio.run(reg["reload"].handler(app))        # no arg — still valid
    asyncio.run(reg["reload"].handler(app, ""))    # with arg — also valid
    assert app.called == ["reload", "reload"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_tui_commands.py -q`
Expected: FAIL — no `yolo` command; handlers don't accept `arg`.

- [ ] **Step 3: Implement**

In `harness/tui/commands.py`: update the type and every handler to accept `arg: str = ""`, and add the yolo handler + entry.

```python
@dataclass(frozen=True)
class Command:
    name: str
    description: str
    handler: Callable[["HarnessTui", str], Awaitable[None]]  # noqa: F821  (arg defaults to "")
    aliases: tuple[str, ...] = ()


async def _models(app, arg: str = "") -> None:
    await app.action_select_model()


async def _reload(app, arg: str = "") -> None:
    await app.action_reload()


async def _clear(app, arg: str = "") -> None:
    await app.action_clear()


async def _exit(app, arg: str = "") -> None:
    app.exit()


async def _help(app, arg: str = "") -> None:
    app.show_help()


async def _yolo(app, arg: str = "") -> None:
    sub = arg.strip().lower()
    if sub == "":
        app.action_toggle_yolo()
    elif sub == "pin":
        await app.action_yolo_pin()
    elif sub == "unpin":
        await app.action_yolo_unpin()
    else:
        app._notify_line(f"usage: /yolo [pin|unpin]")


def build_registry() -> list[Command]:
    return [
        Command("models", "Select the active model", _models),
        Command("yolo", "Toggle auto-allow (pin/unpin to persist)", _yolo),
        Command("reload", "Reload everything (restart the app)", _reload),
        Command("clear", "Fresh conversation (restart the agent)", _clear),
        Command("help", "Show available commands", _help),
        Command("exit", "Exit the app", _exit, aliases=("quit",)),
    ]
```

In `harness/tui/app.py`, change `_run_slash` (`app.py:378-389`) to extract and pass the arg:

```python
    async def _run_slash(self, text: str) -> None:
        cmd = self._slash.highlighted_command() if self._slash is not None else None
        # the text after the command name (e.g. "pin" in "/yolo pin")
        parts = text[1:].split() if len(text) > 1 else []
        arg = " ".join(parts[1:]) if len(parts) > 1 else ""
        if cmd is None:
            name = parts[0] if parts else ""
            cmd = resolve_command(self._commands, name)
        self._active_input().value = ""
        await self._close_slash()
        if cmd is None:
            self._notify_line(f"unknown command: {text}")
            return
        await cmd.handler(self, arg)
```

Add the two app methods near `action_toggle_yolo` (Task 5):

```python
    async def action_yolo_pin(self) -> None:
        """Persist 'always launch in YOLO' (and turn it on now — pinning a mode
        you're not in is incoherent)."""
        self._yolo = True
        self._yolo_pinned = True
        self._refresh_yolo_chip()
        await self._send_set_yolo(active=True, pin=True)

    async def action_yolo_unpin(self) -> None:
        """Stop auto-launching in YOLO. Leaves the live state alone."""
        self._yolo_pinned = False
        self._refresh_yolo_chip()
        await self._send_set_yolo(pin=False)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tui_commands.py -q`
Expected: PASS (new tests + all existing command tests, incl. the `_run_slash` pilot tests and `test_reload_clear_handlers_delegate_to_app_actions`).

- [ ] **Step 5: Commit**

```bash
git add harness/tui/commands.py harness/tui/app.py tests/test_tui_commands.py
git commit -m "feat(tui): /yolo command — toggle, pin, unpin

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Document the clickable footer mode chip in the catalog

**Files:**
- Modify: `harness/tui/styles/components.md`
- Modify: `docs/superpowers/specs/2026-06-26-tui-design-system-design.md`

**Interfaces:** none (docs). This satisfies the goal's "if we need an extra pattern, add it to the documented design patterns so other areas can use it."

- [ ] **Step 1: Add the catalog entry**

In `harness/tui/styles/components.md`, under **## A. Primitives**, after the `StateDot / ActivityGlyph` entry, add:

````markdown
### `StatusChip.for_yolo` — clickable footer mode chip
A `StatusChip` mounted in the status bar that toggles a **session mode** on click.
First use: YOLO (permission auto-allow). The pattern generalizes to any binary
session mode (backend, fleet-mode, …).
- **In:** `(active: bool, pinned: bool)` → `StatusChip.for_yolo(...)`.
- **Look:** off = `• ask` (muted); on = `! YOLO` (amber/`$scheduled`, bold);
  pinned adds ` · pin`. Glyph `!` = `GLYPH["bypass"]`. Amber signals a
  security-sensitive on-state without a per-command banner (restraint, p.4).
- **Click → action:** the app's `on_click` (guarded on `#statusbar-mode`) calls
  `action_toggle_yolo()`, which flips the live state, refreshes the chip in place
  (`_refresh_yolo_chip`), and fires `ext_method("harness/set_yolo", {active})`.
- **Persisting is a SEPARATE gesture.** A click only flips the *live* mode (loud,
  reversible). Making a mode *survive launches* is the deliberate `/yolo pin`
  (writes `yolo_pinned` to `done.conf`) — never the click. This split is the
  pattern's safety contract; reuse it for any persisted mode.

```
· ask          ! YOLO          ! YOLO · pin
 muted          amber           amber
```
````

Also add it to the "Catalog at a glance" block (the `A primitives` line):

```
A primitives   StatusChip · StatusChip.for_yolo(footer mode chip) · StateDot/ActivityGlyph · Hairline/SectionLabel
```

- [ ] **Step 2: Cross-reference in the design-system spec**

In `docs/superpowers/specs/2026-06-26-tui-design-system-design.md`, the catalog
is **§6 "The component catalog (summary)"** (heading at line ~281). Update the
`A primitives` line inside its fenced "at a glance" block to add the new chip:

Change:
```
A primitives   StatusChip · StateDot/ActivityGlyph · Hairline/SectionLabel
```
to:
```
A primitives   StatusChip (+ for_yolo footer mode chip) · StateDot/ActivityGlyph · Hairline/SectionLabel
```

- [ ] **Step 3: Verify no code/tests touched**

Run: `git status --short` — only the two `.md` files staged-able. No test run needed (docs only).

- [ ] **Step 4: Commit**

```bash
git add harness/tui/styles/components.md docs/superpowers/specs/2026-06-26-tui-design-system-design.md
git commit -m "docs(design-system): document the clickable footer mode chip

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Full-suite regression gate + UX review

**Files:** none (verification).

- [ ] **Step 1: Run the full suite from the worktree root**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all pass (the suite that was green at branch point, plus all new tests). Note any `needs_vibeproxy`-skipped tests are expected skips, not failures.

- [ ] **Step 2: Confirm the primary checkout stayed clean**

Run: `git -C /Users/alberto/Work/Quiubo/harness status --short`
Expected: empty output.

- [ ] **Step 3: UX/regression review** (codex:codex-rescue or a focused subagent) — see the "Review" section of the parent task. Address findings, re-run the suite.

- [ ] **Step 4: Push branch + open PR against `main`** — see parent task.

---

## Notes on cross-impacts (read before starting)

- **`test_set_model_survives_save_failure`** (`test_acp_agent.py:47-53`) monkeypatches `config.save_default`. After Task 1, `set_model` still calls `save_default` (the thin wrapper), so this test stays valid — do NOT change it. (Task 2's failure test patches `update_default` separately.)
- **`save_default` must keep replacing `backend`+`model`** so `test_save_default_round_trips` / `test_save_default_preserves_other_agents` stay green; only the pin-preservation is new.
- **`_relaunch_args` is intentionally unchanged.** The reload-carries-live-state behavior comes from `main()` normalizing `args.yolo`; the relaunch unit tests pass `NS(yolo=...)` directly and must keep their current expected output.
- **`on_click` may already exist in `app.py`** — check before adding; fold the guard in rather than defining a duplicate handler.
- **`StatusChip` / `Click` imports in `app.py`** — verify before adding to avoid duplicate-import errors.
