# Phase 6: Full Distributability — Design

**Date:** 2026-06-26
**Status:** Revised after 2nd Codex review (NO-GO → fixes folded in). Ready for
writing-plans.
**Branch:** `phase6-distributability`

> **Revision note (2026-06-26):** 2nd Codex review (against current `main`, which
> gained streamed chat + a landing-header `harness/tui/header.py`) found: (1) the
> distribution name is **`quiubo-done`**, not `quiubo-harness`; (2)
> `importlib.resources.files("minisweagent")` DOES import the package — switched
> `mini_yaml_path` to `find_spec`; (3) `.env` must anchor to an explicit
> `project_dir` (the harness never `chdir`s), and `load_env` must run before any
> `minisweagent` import; (4) the vendoring fallback must ship the engine as REAL
> discovered packages (package-data Python isn't importable); (5) no
> `harness/tui/assets/` dir exists — dropped that package-data glob + test
> assertion; (6) skills roots are Traversables (use `.is_dir()`, not `.exists()`).

## Goal

A **non-editable wheel** of the harness (distribution `quiubo-done`) installs and
runs `dn` from any directory **after the source checkout is deleted**. Editable mode
(`uv tool install --editable .`) remains the always-latest dev workflow and must
not regress.

Not in scope: publishing to PyPI (the `upstream/` path dependency can't be
published as-is — that is a later phase).

## Why this is more than "fix REPO_ROOT"

The original framing was "stop resolving assets via the source checkout." An
adversarial Codex review (NO-GO verdict) built actual wheels and proved two
**additional** blockers that the framing missed, both independently verified:

1. The wheel manifest (`packages = ["harness", "harness.tui"]`) is an explicit
   list that **drops** `harness.tui.widgets/*`, `app.tcss`, and `assets/*`. A
   wheel literally cannot run `dn` today — the app imports
   `harness.tui.widgets.*` and loads `app.tcss`, neither of which ships.
2. The TUI spawns the agent via `acp.spawn_agent_process(...)` with **no `env`**.
   ACP's transport trims the child environment to a 6-var allowlist
   (`HOME/LOGNAME/PATH/SHELL/TERM/USER` — see
   `acp/transports.py:DEFAULT_INHERITED_ENV_VARS`), so shell `VIBEPROXY_*` vars
   **never reach the agent** — broken even in editable mode today.

A third claim — that a non-editable install of the `upstream/` path source
**copies** the engine code into the tool venv (the linchpin of "survives
checkout deletion") — could NOT be verified: Codex's sandboxed `uv` panicked
(`system-configuration ... Attempted to create a NULL object` / `Tokio executor
failed`) before any install completed. It therefore remains **unproven** and is
gated by the mandatory smoke test below, with a named fallback.

Phase 6 is therefore five workstreams, all required for the one guarantee.

## Architecture

Replace `REPO_ROOT = Path(__file__).resolve().parent.parent` (the source
checkout — the single assumption that breaks a wheel) with two location
strategies, neither of which assumes a source tree:

- **Bundled assets** (skills, engine config) → resolved via the *installed
  package* using `importlib.resources`, found wherever the package lives
  (editable checkout OR copied-into-venv).
- **User assets** (`.env`, custom skills) → resolved via an **XDG config dir**
  (`$XDG_CONFIG_HOME/harness`, else `~/.config/harness`), independent of install
  mode.

A single new module, `harness/paths.py`, is the source of truth for asset
location. The three entrypoints stop computing paths themselves and call it.

## Asset categories

| Asset | Today (breaks in wheel) | Phase 6 resolution |
|---|---|---|
| `mini.yaml` | `REPO_ROOT/upstream/src/minisweagent/config/mini.yaml` | `find_spec("minisweagent").submodule_search_locations[0]/"config"/"mini.yaml"` (no import) |
| `skills/` | `REPO_ROOT/skills` | `harness/skills/` (package-data) **merged with** `~/.config/harness/skills/` |
| `.env` | `load_dotenv(REPO_ROOT/.env)` | process env → `./.env` → `~/.config/harness/.env` |
| engine code | `upstream/` path source, `editable=true` | path source `editable=false` (copied into venv) |

## Components

### `harness/paths.py` (new — single source of truth)

```
config_dir() -> Path
    $XDG_CONFIG_HOME/harness if XDG_CONFIG_HOME set and non-empty,
    else ~/.config/harness.  Does NOT create the directory.

load_env(project_dir: str | Path | None = None) -> None
    Idempotent. project_dir is the PROJECT the client/agent operates on (the
    TUI's --cwd / the agent's session cwd) — NOT Path.cwd(). The harness never
    chdir()s (tui_main.py supports --cwd without chdir), so anchoring to
    Path.cwd() would read the wrong .env. Apply in precedence with python-dotenv's
    override=False (already-set keys are never overwritten):
      1. process env             (already present — untouched)
      2. project_dir/".env"      (the project being worked on; skip if None)
      3. config_dir()/".env"
    For each existing path in [project_dir/.env, config_dir/.env], in that order,
    call load_dotenv(path, override=False). Missing files skipped silently.

bundled_skills_dir() -> Traversable
    importlib.resources.files("harness") / "skills"
    A Traversable. For a setuptools-installed (unzipped) wheel and an editable
    install it is backed by a real path, but callers MUST use the Traversable API
    (is_dir(), iterdir(), joinpath(), read_text()) — NOT Path-only ops like
    .exists() — to stay correct under any namespace/zip edge case.

skills_dirs() -> list[Traversable]
    Ordered LOWEST precedence first, omitting roots that don't exist (probe via
    .is_dir() on the Traversable, not .exists()):
      [bundled_skills_dir(), Path(config_dir())/"skills"]
    (config_dir()/skills is a plain Path — user dir on the real filesystem — and
    is wrapped so skills.py sees a uniform Traversable-ish interface; see §skills.)

mini_yaml_path() -> Path
    Resolve WITHOUT importing minisweagent (its __init__ runs dotenv +
    global-config side effects — upstream/src/minisweagent/__init__.py:26-36).
    Use importlib.util.find_spec("minisweagent"); take its
    submodule_search_locations[0] / "config" / "mini.yaml". If the spec or file
    is absent, raise a clear error: "mini-swe-agent config not found; is the
    engine installed?" (not a raw FileNotFoundError). NOTE: importlib.resources
    .files("minisweagent") is NOT acceptable here — it DOES import the package
    (verified: `minisweagent` enters sys.modules + prints the greeting).
```

### `harness/skills.py` (modified)

Today `load_catalog(skills_dir: Path)` and `compose(skills_dir: Path, names)`
take **one** directory and scan with `iterdir()`. Generalize both to accept the
**ordered list** from `skills_dirs()` and merge by skill name:

- Iterate roots in order; later roots override earlier ones **by skill name**
  (user skills in `config_dir()/skills` override bundled skills of the same name).
- Override is by name resolved from valid frontmatter. A user skill dir that is
  **invalid** (missing/malformed `SKILL.md`) must NOT shadow a valid bundled
  skill of the same name — it is skipped-and-shown via the existing `skill.load`
  event, and the bundled skill remains active.
- Absent roots contribute nothing (already the single-dir behavior).

Generalize `load_catalog`/`compose` to take the ordered `skills_dirs()` list and
consume each root via the **Traversable API** (`is_dir()`/`iterdir()`/
`joinpath()`/`read_text()`), not `Path.iterdir()` — so a bundled root resolved
through `importlib.resources` works under any install layout. Update all callers
to pass the ordered list (no single-`Path` back-compat hedge — it invites a
caller to keep passing one dir and silently lose the user-skills merge).

### Entrypoints — `harness/acp_main.py`, `harness/tui_main.py`

- Delete `REPO_ROOT`, the `sys.path.insert(REPO_ROOT/...)` hacks, and
  `load_dotenv(REPO_ROOT/.env)`. (The editable/non-editable install already makes
  `import minisweagent` and `import harness` resolve — that was Phase 5's
  `[tool.uv.sources]` purpose.)
- **`paths.load_env(project_dir)` must run BEFORE any import that triggers
  `minisweagent`'s package init** — `acp_env.py:12` (`from
  minisweagent.environments.local import LocalEnvironment`) and `acp_main.py:66`
  (`from minisweagent.models.litellm_model import LitellmModel`) import upstream,
  whose `__init__` calls dotenv/global-config side effects. So either call
  `load_env()` at the very top of the entrypoint `main()` BEFORE importing
  `HarnessAgent`/engine modules, or make those engine imports lazy (inside the
  functions that use them). The implementation must pick one and the plan names
  the exact ordering. `project_dir` = the agent's session cwd / the TUI's --cwd.
- Build the Router/catalog from `paths.skills_dirs()`.
- Read the agent config via `paths.mini_yaml_path()`.

### TUI subprocess spawn — `harness/tui/app.py`

`on_mount` currently calls:
```python
self._cm = acp.spawn_agent_process(self._client, self.agent_cmd[0], *self.agent_cmd[1:])
```
Change to pass env + cwd explicitly (both params exist on
`spawn_agent_process`; the transport does `merged_env = default_environment();
merged_env.update(env)`, so passed keys are added on top of the 6-var allowlist):
```python
self._cm = acp.spawn_agent_process(
    self._client, self.agent_cmd[0], *self.agent_cmd[1:],
    env=dict(os.environ),     # VIBEPROXY_* resolved by paths.load_env() at TUI startup
    cwd=self.cwd,             # agent runs in the project dir (anchors ./.env)
)
```
`tui_main.py` must call `paths.load_env(args.cwd)` (the resolved `--cwd`, which
defaults to `os.getcwd()`) before the app spawns the agent so `os.environ` already
holds the resolved `VIBEPROXY_*`, and pass that SAME cwd as the spawn `cwd=`. Both
processes thus anchor `.env` to the same project dir.

### `pyproject.toml`

```toml
[tool.setuptools.packages.find]
include = ["harness*"]          # discovers harness.tui, harness.tui.widgets, etc.

[tool.setuptools.package-data]
"harness" = ["skills/**/*"]
"harness.tui" = ["*.tcss"]      # app.tcss. (No assets/ dir exists on main; add a
                                #  glob only if/when an asset is actually added.)

[tool.uv.sources]
mini-swe-agent = { path = "upstream", editable = false }   # was editable = true
```

Replace the explicit `[tool.setuptools] packages = [...]` list with the `find`
directive above. Physical move: `skills/` → `harness/skills/`.

**Distribution name.** The current `[project].name` is **`quiubo-done`** (NOT
`quiubo-harness` — verify before writing the plan: `grep '^name' pyproject.toml`).
The package is `harness`; the console scripts are `dn`/`dn-agent`. The smoke
script and any docs must use the real distribution name `quiubo-done` (or this
phase explicitly renames `[project].name` — a one-line change — and updates every
reference; pick one in the plan, default = keep `quiubo-done`).

Replace the explicit `[tool.setuptools] packages = [...]` with the `find`
directive above. Physical move: `skills/` → `harness/skills/`.

## Data flow — the two-process env handoff

Two processes; both anchor to `self.cwd` (the project) so there is no
"which CWD?" ambiguity:

1. **TUI process** (`harness.tui_main`): `paths.load_env()` resolves
   process env → `./.env` → `config_dir()/.env` into its own `os.environ`,
   then spawns the agent with `env=dict(os.environ)`, `cwd=self.cwd`.
2. **Agent process** (`harness.acp_main`): re-runs `paths.load_env()`. The
   parent already populated `VIBEPROXY_*`; `override=False` means those win, and
   because the parent passed `cwd=self.cwd`, the child's `./.env` is the *same*
   file the parent saw. Idempotent — no drift.
3. **`dn-agent` launched standalone by an editor** (Zed, no TUI parent): step 2
   is the only `load_env()`; `./.env` = the cwd the editor launched it in. This
   is correct and intended (an editor-launched agent reads the project it points
   at). No special-casing — the same `paths.load_env()` covers both. Documented
   property, not an accident.

## Error handling — degrade, don't crash

Matches the existing skill-load philosophy (bad input is skipped-and-shown,
never fatal). The ONE genuinely-fatal case gets an explicit message.

| Situation | Behavior |
|---|---|
| No `.env` anywhere | Not an error. `load_env()` is a no-op; defaults apply (`VIBEPROXY_BASE_URL`=localhost:8317, model=gpt-5.4). `--model mock` needs no env. |
| `config_dir()` doesn't exist | Fine. `skills_dirs()` omits the missing skills root; `load_env()` skips the missing file. **Never auto-create** the dir. |
| Malformed user skill in `config_dir()/skills` | Skipped-and-shown via `skill.load`; does NOT shadow a valid bundled skill of the same name. |
| `mini_yaml_path()` can't find engine config | Genuinely fatal (engine install broken). Surface "mini-swe-agent config not found; is the engine installed?" — not a raw traceback. |
| Agent subprocess fails to spawn | Already caught at `app.py` `except Exception → self._fatal(...)`. Unchanged. |
| Unreachable VIBEPROXY at runtime | Out of scope — runtime connection error, not distributability. Unchanged. |

## Testing

### Tier 1 — unit tests (fast, hermetic, CI; no install, no live proxy)

`tests/test_paths.py` (new) — each monkeypatches `HOME`, `XDG_CONFIG_HOME`,
`cwd`, `os.environ`:

- `config_dir` honors `$XDG_CONFIG_HOME` when set; falls back to
  `~/.config/harness` when unset/empty.
- `config_dir` does NOT create the directory (returned path `.exists()` is False
  in a clean temp HOME).
- `load_env` precedence: process env beats `./.env` beats `config_dir()/.env`;
  files fill gaps only (`override=False`).
- `load_env` with no files present: no-op, no exception.
- `skills_dirs` ordering: `[bundled, config]`; missing roots omitted (probed via
  `.is_dir()` on the Traversable, not `.exists()`).
- `mini_yaml_path` returns an existing file AND does not trigger
  `import minisweagent`: run it in a SUBPROCESS that asserts `"minisweagent" not
  in sys.modules` right after the call (a subprocess is required for a clean
  import state — an in-process test is polluted by other tests importing the
  engine). The subprocess test is the authoritative check; no source-grep
  fallback.

`tests/test_skills.py` (update existing) — ordered-list (Traversable) signature;
user skill overrides bundled by name; invalid user skill does NOT shadow valid
bundled.

`tests/test_packaging.py` (new) — build the wheel and assert it contains:
`harness/tui/app.tcss`, `harness/tui/widgets/select_modal.py`, and
`harness/skills/*/SKILL.md` (at least one). Do NOT assert an `assets/` entry —
no `harness/tui/assets/` dir exists on main (verified); add such an assertion
only if the phase adds a real asset. Build with whatever is available
(`python -m build --wheel` or `uv build --wheel`); skip with a clear message if
neither is installable in the environment. Catches manifest regressions cheaply
(build only, no install).

### Tier 2 — manual smoke gate (`scripts/smoke-wheel.sh`, documented, NOT pytest)

The mandatory delete-checkout proof. Run from an **unrelated temp cwd** (never
the repo root — `sys.path[0]` would mask the bug):

```
1. python -m build  (or uv build)          # -> dist/quiubo_done-*.whl  (name = quiubo-done)
2. cp -r <checkout> /tmp/harness-src       # install from a COPY
3. uv tool install --force /tmp/harness-src   (NON-editable)
4. rm -rf /tmp/harness-src                  # delete the source it installed from
5. mkdir -p ~/.config/harness && cp .env.example ~/.config/harness/.env
6. cd /tmp/empty && dn --model mock
7. Assert: TUI launches, lands, send a prompt, see the task.classified chip +
   reply. (mock model needs no proxy; this proves assets + manifest + spawn.)
8. Then assert engine reachability explicitly: `dn-agent --help` runs from
   /tmp/empty (proves `import minisweagent` resolves post-checkout-deletion —
   the linchpin). Optionally with live proxy: `dn` (default model), confirm
   VIBEPROXY_* resolved from ~/.config/harness/.env reaches the agent.
```

### Linchpin caveat + EXECUTABLE fallback

The engine-copy behavior (step 3 → does `upstream/` code land in the tool venv as
an importable package?) is **unproven** until Tier 2 runs (uv panicked in two
review sandboxes; could not be verified by reasoning). If a non-editable install
does NOT make `import minisweagent` resolve after the checkout is deleted, that is
a NO-GO discovered at smoke time.

**Executable fallback (not package-data — Python source under package-data is NOT
importable as a top-level package):** drop the `[tool.uv.sources]` path entry and
instead vendor the engine as **real, discovered packages** in OUR build:
- add `minisweagent*` to `[tool.setuptools.packages.find] include` and point
  `[tool.setuptools.package-dir]` at `upstream/src` for that package (so
  setuptools compiles `minisweagent` INTO our wheel as importable modules);
- carry its config via `[tool.setuptools.package-data] "minisweagent" =
  ["config/**/*"]`;
- keep `mini-swe-agent` out of `[project.dependencies]` in this fallback (the
  engine now ships inside our wheel, not as a separate dependency).
This stays within zero-upstream-edits (we only read `upstream/src`, never modify
it). It is a larger pyproject change, so it is gated behind the smoke result.

The implementer MUST run Tier 2 BEFORE the final review and report the observed
copy behavior. If the primary (non-editable path source) works, the fallback is
not built. If it fails, the fallback is implemented as part of THIS phase (not a
silent deferral) and re-smoke-tested.

## Global Constraints

- **Zero upstream edits.** `upstream/` is vendored unmodified; nothing in Phase 6
  may modify files under `upstream/`. (Changing the *source declaration* in our
  `pyproject.toml` from `editable=true` to `editable=false` is OUR config, not an
  upstream edit.)
- **Editable mode must not regress.** `uv tool install --editable .` keeps `dn`
  pointing at live source; all assets resolve to the checkout in editable mode.
- **`run_traced.py` is OUT of the distributability success bar.** It is the
  Phase-0 dev CLI; neither console script (`dn`, `dn-agent`) points at it. It may
  consume `paths.py` helpers where trivial, but its `REPO_ROOT/examples` default
  cwd and `REPO_ROOT/harness/runs` output are not required to work from a wheel.
- **No new runtime dependency** for config-dir resolution (XDG is ~6 lines; do
  not add `platformdirs`).
- **Command is `dn`**, import package is `harness`, distribution name is
  **`quiubo-done`** (current `pyproject.toml:6` — NOT `quiubo-harness`).
- **STDOUT is the ACP wire** for the agent — no stray prints to stdout in
  agent-side code paths (existing `MSWEA_SILENT_STARTUP=1` discipline).

## Out of scope (deferred)

- Publishing to PyPI / a private index.
- DRY-ing the 4 litellm env-wiring sites (separate carry-over).
- `run_traced.py` becoming wheel-portable.
- True mid-LLM-call cancel.
