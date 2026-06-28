# Worktree test-resolution — make `pytest` test the worktree, for everyone

**Date:** 2026-06-28
**Status:** Design (approved, pre-plan)
**Scope:** developer tooling / test-import resolution. Independent of the
persona-switch UX work that shares this branch.

---

## 1. Problem

`done` mandates worktree-based development (AGENTS.md #1) **and** documents a
single repo-root editable install (README *Development*):

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e ./upstream pytest
.venv/bin/pip install -e .          # absolute-path editable finder
```

`pip install -e .` writes an editable **finder pinned to the checkout's absolute
path** (`__editable___quiubo_harness_..._finder`). So `.venv/bin/python`, from *any*
cwd, imports the **root checkout's** `harness/` — never a worktree's. Anyone who
follows both documented instructions (root venv + worktrees) silently runs the
**wrong source** under test.

The current mitigation is fragile: **58 test files** each begin with

```python
import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")          # relative to CWD
```

`"."` is cwd-relative, so it only shadows the editable finder **when cwd happens to
be the worktree**. Any deviation — a `cd` that reverts, running pytest from the repo
root, an import outside a test module — silently resolves to the root checkout. This
has bitten before (per memory: PR #40 hotfix, PR #83).

**Goal:** one root-venv setup; from *any* worktree, `pytest` reliably tests *that
worktree's* code, with no per-worktree setup and no cwd ceremony. Fix the footgun
structurally so it doesn't reach anyone who clones `done`.

## 2. Root cause (one line)

Absolute-path editable finder (one root venv) × N worktrees → import resolution is
pinned to the root checkout, and the only escape hatch (`sys.path.insert(0, ".")`)
depends on cwd.

## 3. Solution

A single committed **`tests/conftest.py`** that prepends the worktree's **absolute**
source root to `sys.path`, derived from the conftest's *own* location — not cwd:

```python
# tests/conftest.py
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent      # the worktree this test tree lives in
for p in (_ROOT / "upstream" / "src", _ROOT):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)
```

**Why this is correct and robust:**

- pytest auto-loads the `conftest.py` belonging to the tests being collected. Run a
  worktree's tests → that worktree's conftest runs → `Path(__file__)` is *that
  worktree's* path. Resolution keys off **which tree the tests live in**, never cwd,
  never an absolute pin.
- An absolute path inserted at `sys.path[0]` shadows the editable finder. **Verified
  empirically:** with cwd at the *primary* checkout, inserting a worktree's abs path
  first makes `import harness` resolve to the worktree's `__init__.py`.
- conftest is imported before any test module, so it fully replaces the per-file
  `sys.path.insert` lines with one authoritative location.

### 3.1 Components

1. **`tests/conftest.py` (new).** The single source of truth for under-test import
   resolution (code above).
2. **Remove the 58 per-file `sys.path.insert` blocks.** Pure subtraction; conftest
   now owns this. Both lines (`"upstream/src"` and `"."`) come out of every test file
   that has them.
3. **Docs alignment.** README *Development* / *Tests* and AGENTS.md: tests resolve to
   the worktree automatically via conftest; keep the single root-venv setup; drop the
   "must run from worktree cwd" caveat.

### 3.2 Why not the alternatives

- **Per-worktree venv script (uv).** True isolation, but it *is* a per-worktree step —
  contradicts the zero-setup goal. Reserve for if isolated *dependencies* per worktree
  ever become a need.
- **Editable `editable_mode=compat` .pth.** Changes the documented install line and
  leans on setuptools compat behavior; more surface area than a stdlib conftest.

## 4. Testing (TDD)

1. **Failing test first** (`tests/test_worktree_resolution.py`): assert
   `Path(harness.__file__).resolve()` is relative to the test-root
   (`Path(__file__).resolve().parent.parent`). Runnable from any cwd. **Must fail**
   when run from the primary venv with cwd ≠ worktree *before* conftest exists, and
   pass after.
2. **The existing suite is the regression net.** With the per-file hacks removed and
   conftest in place, the full suite must stay green run **both** from cwd-at-worktree
   and cwd-at-primary. Run it both ways as acceptance.

## 5. Edge cases

| Case | Behavior |
|---|---|
| Run pytest from worktree cwd | conftest resolves to worktree (as before, now cwd-independent) |
| Run pytest from primary repo root targeting a worktree's tests | resolves to the worktree (conftest path-derived) |
| `upstream/src` missing in a worktree | insert is a no-op guard; import falls back to the editable `./upstream` install (unchanged behavior) |
| A test that imported via the old relative hack | hack removed; conftest covers it — verified by suite staying green |
| Non-pytest entry (e.g. `python -m harness.tui_main`) | **out of scope** — conftest is pytest-only. Those run via the installed console script / editable install as documented. This spec fixes *tests*, the stated problem. |

## 6. Scope guard / non-goals

- Not changing the install mechanism, the venv strategy, or `pyproject.toml`.
- Not fixing import resolution for non-test runtime entry points (they use the
  installed package as designed).
- Not touching the persona-switch UX work on this branch (separate commits).

## 7. Acceptance

- `tests/conftest.py` exists; 0 remaining `sys.path.insert` lines in `tests/`.
- New resolution test passes from both cwds; fails without conftest.
- Full suite green from both cwds.
- README + AGENTS.md updated; no stale cwd caveat.
```
