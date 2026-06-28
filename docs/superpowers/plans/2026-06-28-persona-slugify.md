# Persona Name Slugify + Display Name Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users type any persona name in the create modal; slugify it to a safe id and save the typed name as the display name.

**Architecture:** A new pure `slugify_persona_name` normalizer in front of the unchanged `_VALID_ID` charset. The modal previews the slug live and passes the raw name; the app slugs it and carries `{id, display_name}`; `create_persona` writes the display name to `persona.toml` (which the rail already reads via `read_name`).

**Tech Stack:** Python 3.11, Textual TUI, the ACP lib, pytest. Worktree: `.claude/worktrees/persona-slugify` (branch `persona-slugify`).

**Source spec:** `docs/superpowers/specs/2026-06-28-persona-slugify-design.md` (crux-verified).

## Global Constraints

- **The storage rule is UNCHANGED:** `persona_select._VALID_ID` (`^[a-z0-9_-]+$`) + reserved `"default"` + no-clobber stay as the engine's last-line validation. Slugify is a layer in FRONT, never a replacement.
- **Slugify invariant:** every NON-EMPTY result of `slugify_persona_name` MUST satisfy `_VALID_ID` (verified). Empty result = "no valid id from this input".
- **No new dependency:** the `name = "..."` line is written directly (escaped string), NOT via a TOML-writer lib.
- **No regression:** persona-create WITHOUT a display_name behaves exactly as today (no `persona.toml` `name` written; default seed untouched).
- **Display-name write is NON-FATAL:** create succeeds even if the persona.toml write raises.
- **TOML-escape** the display name: escape `\` then `"`, and strip control chars (`\n`, `\r`, etc.) before writing.
- **Work in this worktree**; run pytest with the worktree as cwd (editable-install shadowing). Test command: `.venv/bin/python -m pytest tests/ -q` from the worktree root.

---

## File Structure

- **Modify** `harness/persona_select.py` — add `slugify_persona_name(raw) -> str` next to `_VALID_ID`.
- **Modify** `harness/persona.py` — `create_persona(id, display_name=None)`; add private `_write_persona_name(workspace_dir, display_name)`.
- **Modify** `harness/acp_agent.py` — the `harness/create_persona` ext-method forwards `display_name`.
- **Modify** `harness/tui/widgets/new_persona_modal.py` — live slug preview; submit passes the raw name (already does — confirm).
- **Modify** `harness/tui/app.py` — `_do_create_persona` slugs the raw name, empty-guard, carries `display_name`.
- **Tests:** `tests/test_persona_select.py` (slugify), `tests/test_persona.py` (display-name write), `tests/test_acp_agent.py` (ext-method), `tests/test_tui_pilot.py` (modal preview + app slug).

---

### Task 1: `slugify_persona_name` pure helper

**Files:**
- Modify: `harness/persona_select.py` (add the function after `_VALID_ID`)
- Test: `tests/test_persona_select.py`

**Interfaces:**
- Produces: `slugify_persona_name(raw: str) -> str` — lowercased, `[^a-z0-9]+`→`-`, collapsed/trimmed `-`; `""` when nothing valid survives. Every non-empty result matches `_VALID_ID`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_persona_select.py — add
import pytest
from harness.persona_select import slugify_persona_name, _VALID_ID


@pytest.mark.parametrize("raw,expected", [
    ("My Persona", "my-persona"),
    ("Alberto", "alberto"),
    ("Fred.Smith", "fred-smith"),
    ("  spaced  ", "spaced"),
    ("a---b__c.d", "a-b-c-d"),
    ("my-persona", "my-persona"),     # already valid → passthrough
    ("ABC123", "abc123"),
    ("--lead", "lead"),
    ("trail--", "trail"),
    ("MiXeD CaSe", "mixed-case"),
    ("!!!", ""),
    ("😀", ""),
    ("", ""),
    ("___", ""),
    ("café", "caf"),                  # accented dropped (lossy, by design)
    ("İstanbul", "i-stanbul"),        # unicode combining mark → separator
])
def test_slugify_persona_name(raw, expected):
    assert slugify_persona_name(raw) == expected


@pytest.mark.parametrize("raw", [
    "My Persona", "Fred.Smith", "café", "İstanbul", "a.b.c", "  X Y  ",
    "----", "a" * 200, "Ω mega", "tab\tname", "new\nline",
])
def test_slugify_result_always_valid_or_empty(raw):
    """The invariant: a non-empty slug ALWAYS satisfies _VALID_ID."""
    s = slugify_persona_name(raw)
    assert s == "" or _VALID_ID.match(s), f"{raw!r} -> {s!r} violates _VALID_ID"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_persona_select.py -q -k slugify`
Expected: FAIL — `ImportError: cannot import name 'slugify_persona_name'`.

- [ ] **Step 3: Implement**

In `harness/persona_select.py`, after the `_VALID_ID` definition:

```python
def slugify_persona_name(raw: str) -> str:
    """Normalize a free-text persona name to a safe id: lowercase, every run of
    characters outside [a-z0-9] becomes a single hyphen, leading/trailing hyphens
    trimmed. Returns "" when nothing valid survives (e.g. "!!!", emoji, ""). A
    non-empty result ALWAYS satisfies _VALID_ID — slugify is the friendly layer in
    front of the strict storage charset; accented/non-ascii chars are dropped, not
    transliterated (the typed name is kept separately as the display label)."""
    s = raw.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)   # any non-[a-z0-9] run -> one hyphen
    return s.strip("-")
```

(`re` is already imported in this module — it defines `_VALID_ID`. Confirm; don't re-import.)

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_persona_select.py -q -k slugify`
Expected: PASS (both the table and the invariant test).

- [ ] **Step 5: Commit**

```bash
git add harness/persona_select.py tests/test_persona_select.py
git commit -m "feat(persona): slugify_persona_name normalizer (slugify task 1)"
```

---

### Task 2: `create_persona` writes the display name to persona.toml

**Files:**
- Modify: `harness/persona.py` — `create_persona` gains `display_name`; add `_write_persona_name`.
- Test: `tests/test_persona.py`

**Interfaces:**
- Consumes: `harness.persona_config.read_name`, `PERSONA_TOML` (existing reader).
- Produces:
  - `create_persona(persona_id: str, display_name: str | None = None) -> Path` — unchanged validation; when `display_name` is given, writes `name = "<escaped>"` to `<ws>/persona.toml` (non-fatal).
  - `_write_persona_name(workspace_dir: Path, display_name: str) -> None` — escape + write; swallow `OSError`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_persona.py — add (reuse the isolated_config fixture already in this file)
from harness import persona, persona_config


def test_create_persona_writes_display_name(isolated_config):
    ws = persona.create_persona("my-persona", display_name="My Persona")
    assert persona_config.read_name(ws) == "My Persona"


def test_create_persona_no_display_name_writes_no_name(isolated_config):
    ws = persona.create_persona("plain")
    assert persona_config.read_name(ws) is None      # no name key / no persona.toml


def test_create_persona_display_name_escapes_quotes(isolated_config):
    ws = persona.create_persona("quoted", display_name='He said "hi" \\ ok')
    # must round-trip without corrupting the TOML
    assert persona_config.read_name(ws) == 'He said "hi" \\ ok'


def test_create_persona_display_name_strips_control_chars(isolated_config):
    ws = persona.create_persona("ctrl", display_name="line1\nline2")
    name = persona_config.read_name(ws)
    assert name is not None and "\n" not in name      # control chars stripped, file valid


def test_create_persona_display_name_write_failure_is_nonfatal(isolated_config, monkeypatch):
    # the persona is still created even if the name write blows up
    monkeypatch.setattr(persona, "_write_persona_name",
                        lambda ws, dn: (_ for _ in ()).throw(OSError("read-only")))
    ws = persona.create_persona("robust", display_name="Robust")
    assert ws.is_dir()
    for n in persona.PERSONA_FILES:
        assert (ws / n).is_file()
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_persona.py -q -k "display_name"`
Expected: FAIL — `create_persona() got an unexpected keyword argument 'display_name'`.

- [ ] **Step 3: Implement**

In `harness/persona.py`, change `create_persona` and add the writer:

```python
def create_persona(persona_id: str, display_name: str | None = None) -> Path:
    """Create a NEW persona workspace under config_dir()/agents/<id> with the inert
    template trio, and return its path. Validation: charset (^[a-z0-9_-]+$) AND the
    reserved id "default" is rejected. No clobber: existing target -> PersonaExists.
    Explicit creation REPORTS failure (OSError propagates). When display_name is given,
    write it to persona.toml `name` (best-effort, non-fatal) so the rail shows a friendly
    label."""
    if persona_id == persona_select.RESERVED_KEY or not persona_select._VALID_ID.match(persona_id):
        raise persona_select.InvalidPersonaId(persona_id)
    target = paths.config_dir() / "agents" / persona_id
    if target.exists():
        raise PersonaExists(persona_id)
    _copy_persona_templates(target)
    if display_name:
        _write_persona_name(target, display_name)
    return target


def _write_persona_name(workspace_dir: Path, display_name: str) -> None:
    """Write `name = "<display_name>"` to <workspace_dir>/persona.toml. Strips control
    chars and escapes backslash + double-quote so the value is valid TOML for any input.
    Best-effort: never raises (a failed label must not break create)."""
    # strip control chars (newlines etc. would break the single-line value)
    cleaned = "".join(c for c in display_name if c == "\t" or ord(c) >= 0x20).strip()
    escaped = cleaned.replace("\\", "\\\\").replace('"', '\\"')
    try:
        (workspace_dir / persona_config.PERSONA_TOML).write_text(
            f'name = "{escaped}"\n', encoding="utf-8")
    except OSError:
        pass
```

Add `from harness import persona_config` at the top of `persona.py` if not present (grep first — `persona.py` may already import it; if not, add it near the other `from harness import …` lines).

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_persona.py -q -k "display_name"`
Expected: PASS (5 tests). Note: `test_..._write_failure_is_nonfatal` monkeypatches `_write_persona_name` to raise — the create wraps the call so the exception is swallowed; if your implementation calls `_write_persona_name` OUTSIDE a guard and the monkeypatched version raises, the test will catch it. The writer's own `try/except OSError` covers real failures; the test's injected raise verifies create doesn't propagate. **Belt-and-suspenders: wrap the `_write_persona_name(target, display_name)` call in `create_persona` in its own `try/except Exception: pass`** so ANY write-path failure (not just OSError) is non-fatal.

Revise the call site accordingly:

```python
    if display_name:
        try:
            _write_persona_name(target, display_name)
        except Exception:
            pass                  # a failed label never breaks create
```

Re-run; expected PASS.

- [ ] **Step 5: Run the full persona suite (no regression)**

Run: `.venv/bin/python -m pytest tests/test_persona.py -q`
Expected: PASS (existing create/seed tests + the 5 new).

- [ ] **Step 6: Commit**

```bash
git add harness/persona.py tests/test_persona.py
git commit -m "feat(persona): create_persona writes display name to persona.toml (slugify task 2)"
```

---

### Task 3: ext-method forwards `display_name`

**Files:**
- Modify: `harness/acp_agent.py` (the `harness/create_persona` branch, ~L162-172)
- Test: `tests/test_acp_agent.py`

**Interfaces:**
- Consumes: `persona.create_persona(id, display_name)` (Task 2).
- Produces: `ext_method("harness/create_persona", {id, display_name})` forwards `display_name`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_acp_agent.py — add (reuse _make_agent + isolated_config + asyncio.run)
def test_create_persona_forwards_display_name(agent_default, isolated_config):
    agent_default._cwd = "/x"
    resp = asyncio.run(agent_default.ext_method(
        "harness/create_persona", {"id": "my-persona", "display_name": "My Persona"}))
    assert resp["ok"] is True
    from harness import paths, persona_config
    ws = paths.config_dir() / "agents" / "my-persona"
    assert persona_config.read_name(ws) == "My Persona"


def test_create_persona_without_display_name_still_works(agent_default, isolated_config):
    agent_default._cwd = "/x"
    resp = asyncio.run(agent_default.ext_method(
        "harness/create_persona", {"id": "plain"}))
    assert resp["ok"] is True
```

(`agent_default` is the fixture added in the persona-create task in this file; if absent, use `agent = _make_agent(backend="mock"); agent._cwd = "/x"`.)

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_acp_agent.py -q -k "forwards_display_name"`
Expected: FAIL — `read_name` is None (display_name not forwarded yet).

- [ ] **Step 3: Implement**

In `harness/acp_agent.py`, the `harness/create_persona` branch — pass `display_name`:

```python
        if method == "harness/create_persona":
            pid = (params or {}).get("id")
            if not isinstance(pid, str) or not pid:
                return {"ok": False, "error": "missing id"}
            display_name = (params or {}).get("display_name")
            if not isinstance(display_name, str):
                display_name = None
            from harness import persona, persona_select
            try:
                persona.create_persona(pid, display_name=display_name)
                return self._activate_seat(pid)
            except (persona_select.InvalidPersonaId, persona.PersonaExists,
                    persona_select.UnknownPersona, OSError) as e:
                return {"ok": False, "error": str(e)}
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_acp_agent.py -q -k "create_persona"`
Expected: PASS (the new 2 + the existing create_persona tests).

- [ ] **Step 5: Commit**

```bash
git add harness/acp_agent.py tests/test_acp_agent.py
git commit -m "feat(persona): create_persona ext-method forwards display_name (slugify task 3)"
```

---

### Task 4: App slugs the raw name + carries display_name; modal live preview

**Files:**
- Modify: `harness/tui/app.py` — `_do_create_persona` slugs + empty-guard + display_name.
- Modify: `harness/tui/widgets/new_persona_modal.py` — live slug preview under the input.
- Test: `tests/test_tui_pilot.py`

**Interfaces:**
- Consumes: `slugify_persona_name` (Task 1); the create ext-method with `{id, display_name}` (Task 3).
- Produces: `_do_create_persona(raw)` slugs → empty `{ok:false}` / else ext call `{id: slug, display_name: raw}`. Modal shows `→ will be created as: <slug>` (or a hint).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_tui_pilot.py — add (mirror the existing create-flow pilot tests' _FakeConn pattern)
def test_do_create_persona_slugs_and_carries_display_name():
    async def go():
        class _FakeConn:
            def __init__(self): self.ext_calls = []
            async def ext_method(self, method, params):
                self.ext_calls.append((method, params))
                return {"ok": True, "id": params["id"], "session_id": "s", "model": None}
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            app._conn = _FakeConn()
            resp = await app._do_create_persona("My Persona")
            assert resp["ok"] is True
            method, params = app._conn.ext_calls[-1]
            assert method == "harness/create_persona"
            assert params == {"id": "my-persona", "display_name": "My Persona"}
    asyncio.run(go())


def test_do_create_persona_empty_slug_rejected_no_ext_call():
    async def go():
        class _FakeConn:
            def __init__(self): self.ext_calls = []
            async def ext_method(self, method, params):
                self.ext_calls.append((method, params)); return {}
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            app._conn = _FakeConn()
            resp = await app._do_create_persona("!!!")
            assert resp["ok"] is False
            assert app._conn.ext_calls == []          # never reached the engine
    asyncio.run(go())


def test_modal_previews_the_slug():
    async def go():
        from harness.tui.widgets.new_persona_modal import NewPersonaModal
        from textual.widgets import Input, Static
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(NewPersonaModal(slugify=lambda s: __import__(
                "harness.persona_select", fromlist=["slugify_persona_name"]
            ).slugify_persona_name(s)))
            await pilot.pause()
            modal = app.screen
            inp = modal.query_one("#new-persona-name", Input)
            inp.value = "My Persona"
            # fire the Input.Changed handler
            await pilot.pause()
            from textual.widgets import Input as _I
            modal._on_changed(_I.Changed(inp, "My Persona"))
            await pilot.pause()
            status = str(modal.query_one("#new-persona-status", Static).content)
            assert "my-persona" in status
    asyncio.run(go())
```

NOTE on the modal preview test: read the existing modal + its tests FIRST; the modal may take `slugify` via constructor injection (cleanest) OR import it directly. Pick whichever the implementation uses and match the test to it. If the modal imports `slugify_persona_name` directly (no injection), drop the `slugify=` kwarg and just set the input value + trigger the change handler. The binding contract is: typing updates `#new-persona-status` to contain the slug.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_tui_pilot.py -q -k "do_create_persona or modal_previews"`
Expected: FAIL — `_do_create_persona` doesn't slug yet; the modal has no preview handler.

- [ ] **Step 3: Implement the app slug + display_name**

In `harness/tui/app.py`, rewrite `_do_create_persona`:

```python
    async def _do_create_persona(self, name: str) -> dict:
        """App-side create callback invoked by NewPersonaModal's worker. Slugs the raw
        typed name to a safe id, keeps the raw name as the display label, and forwards
        both to the engine. Returns the ext_method resp dict (modal interprets ok/error)."""
        from harness.persona_select import slugify_persona_name
        slug = slugify_persona_name(name)
        if not slug:
            return {"ok": False, "error": "enter a name with letters or numbers"}
        if self._conn is None:
            return {}
        return await self._conn.ext_method(
            "harness/create_persona", {"id": slug, "display_name": name.strip()})
```

- [ ] **Step 4: Implement the modal live preview**

In `harness/tui/widgets/new_persona_modal.py`:
- Add a `slugify` callback to `__init__` (inject it so the widget stays dependency-light + testable): `def __init__(self, on_create=None, slugify=None, reduced_motion=False)`; store `self._slugify = slugify`.
- Update the placeholder to friendly text: `Input(placeholder="e.g. My Persona", id="new-persona-name")`.
- Add an `Input.Changed` handler that previews the slug:

```python
    @on(Input.Changed, "#new-persona-name")
    def _on_changed(self, event) -> None:
        if self._slugify is None:
            return
        raw = event.value.strip()
        if not raw:
            self.query_one("#new-persona-status", Static).update("")
            return
        slug = self._slugify(raw)
        status = self.query_one("#new-persona-status", Static)
        if slug:
            status.update(f"[$muted]→ will be created as:[/] [$accent]{slug}[/]")
        else:
            status.update("[$muted]enter a name with letters or numbers[/]")
```

- In `_submit`, keep passing the RAW name to `_on_create` (it already does). But guard empty-slug there too: if `self._slugify` and `not self._slugify(name)`, just return (no-op, leave the hint showing) — defense in depth so Enter on an unslugifiable name does nothing.

```python
    def _submit(self) -> None:
        name = self.query_one("#new-persona-name", Input).value.strip()
        if not name:
            return
        if self._slugify is not None and not self._slugify(name):
            return                                  # unslugifiable -> no-op, hint stays
        if self._on_create is None:
            self.dismiss(name)
            return
        self.set_creating()
        self.run_worker(self._do_create(name), thread=False)
```

- Wire the modal's `slugify` at the call site (`on_new_persona_requested` in `app.py`): `NewPersonaModal(on_create=self._do_create_persona, slugify=slugify_persona_name)` (import `from harness.persona_select import slugify_persona_name` there).

- [ ] **Step 5: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_tui_pilot.py -q -k "do_create_persona or modal_previews"`
Expected: PASS. Then the whole pilot file: `.venv/bin/python -m pytest tests/test_tui_pilot.py -q` (existing create-flow tests must still pass — they call `_do_create_persona` with already-valid lowercase names, which slug to themselves). If an existing test passed a name that slugs differently (e.g. contained an uppercase), update its expected id.

- [ ] **Step 6: Commit**

```bash
git add harness/tui/app.py harness/tui/widgets/new_persona_modal.py tests/test_tui_pilot.py
git commit -m "feat(persona): modal slug preview + app slugs name, carries display_name (slugify task 4)"
```

---

### Task 5: Full-suite green + placeholder update

**Files:**
- Modify: `harness/tui/widgets/new_persona_modal.py` docstring (note the slug preview) if it overclaims — verify only.

- [ ] **Step 1: Run the full suite from the worktree root**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS. If a surprising failure, confirm the import resolves into the worktree: `python -c "import harness.persona_select as m; print(m.__file__)"` → must point into `.claude/worktrees/persona-slugify`. (Known: there is no pre-existing flake expected here, but `test_pilot_streams_deltas_into_one_markdown_widget` is a historical Textual timing flake — re-run alone if it's the only failure.)

- [ ] **Step 2: Confirm primary checkout clean**

Run: `git -C /Users/alberto/Work/quiubo/harness status --short`
Expected: empty.

- [ ] **Step 3: Commit any test fixups**

```bash
git add -A && git commit -m "test(persona): slugify full-suite green (slugify task 5)" || echo "nothing to commit"
```

---

## Self-Review

**1. Spec coverage:**
- §3 `slugify_persona_name` (invariant) → Task 1 (+ the invariant property test). ✓
- §3/§4 `create_persona(display_name)` + `_write_persona_name` (escape, control-char strip, non-fatal) → Task 2. ✓
- §3 ext-method forwards display_name → Task 3. ✓
- §3 app slugs + empty-guard + carries display_name → Task 4 (app). ✓
- §2/§4 modal live preview (slug or hint) + empty-slug Enter no-op → Task 4 (modal). ✓
- §2 rail shows the display name → reuses `read_name`/`persona_rows` (no new code; covered by Task 2's `read_name` round-trip + Task 3's on-disk assertion). ✓
- §5 reserved/collision surfaced → existing engine paths (InvalidPersonaId/PersonaExists) flow through the modal error already shipped; no new task. ✓
- §6 full suite → Task 5. ✓

**2. Placeholder scan:** No TBD/handle-errors. Task 4's modal-preview test says "read the modal + match injection vs direct import" — deliberate: the cleanest impl injects `slugify`, and the test must match the real wiring; both the test and the impl in Task 4 use injection consistently, so it's concrete.

**3. Type consistency:** `slugify_persona_name(raw)->str`, `create_persona(id, display_name=None)->Path`, `_write_persona_name(ws, display_name)`, ext-method `{id, display_name}`, `_do_create_persona(name)->dict` carrying `{id: slug, display_name: raw}`, modal `slugify=` injection + `_on_changed`. Consistent across Tasks 1-4.

**Crux tasks for Codex review during build (per spec §7):** Task 1 (the slugify invariant) and Task 2 (the persona.toml escaped, non-fatal write).
