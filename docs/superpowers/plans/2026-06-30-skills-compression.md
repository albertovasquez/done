# Skills Compression Implementation Plan (Phase 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Load pre-compressed skill BODIES from a Done-owned side cache when a skill is invoked, cutting input tokens on `load_skill`, without touching the source SKILL.md files or the per-turn menu.

**Architecture:** A side cache under `config_dir()/compress-cache/skills/`, where each entry is named by `sha256(source_body_sha + rules_sha256())[:16].md` — so freshness IS the filename (source edit or rules bump → different key → automatic miss). At skill-body read time (`skills.compose`, the `_parse_skill_md` call inside its loop) the code checks the cache and substitutes the compressed body when present, else uses the original. `dn compress --skills` (re)builds the cache offline. The per-turn menu/catalog scan (`load_catalog_with_skips` → `_parse_skill_md`) is UNTOUCHED — only the body-compose path reads the cache.

**Tech Stack:** Python 3.11+, stdlib `hashlib`, pytest. Reuses `harness/compress/{engine,rules}.py` (compression + rules version) and the `harness/compress_cli.py` model-builder pattern.

**Spec:** `docs/superpowers/specs/2026-06-30-skills-compression-phase2.md`
**Issue:** #188

## Global Constraints

- Python `>=3.11`. Test command from worktree root: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/ -q` (worktree shares the main `.venv`; `tests/conftest.py` resolves imports to this worktree).
- **Compress BODIES only, never the menu.** The hook is in `skills.compose` (body path). `_parse_skill_md` and `load_catalog_with_skips` (the per-turn menu/frontmatter scan) MUST stay reading the original — do not route them through the cache. (Resolved finding in the spec: the menu shares `_parse_skill_md`; hooking it would compress the menu.)
- **Source SKILL.md files are never written or mutated** — anywhere (bundled, ~/.claude, project). The cache is the ONLY thing written, and only under `config_dir()/compress-cache/`.
- **Cache key = `sha256(source_body_sha256 + rules_sha256())[:16]`** — encodes both source content and compression-rules version. A stale entry can never be served (different key → miss).
- **Read path does NO LLM, NO network** — pure file read + hash. Cache miss → load the original body. Never raises on a bad/missing cache entry (degrade to original).
- **Rebuild is offline only** via `dn compress --skills`; it needs a model (reuse compress_cli's resolution: COMPRESS_MODEL env → `[harness] compress_model` → VIBEPROXY_MODEL → default agent). No model → report unavailable, do nothing.
- Tests inject a fake `call_model` for any compression; never call a real model in CI.
- The compressed body must preserve what the engine already preserves (code/URLs/headings); frontmatter is NOT in the body (the body is everything after the `---` fence), so frontmatter is inherently untouched.

## File Structure

- **Create** `harness/compress/skill_cache.py` — the side-cache: key derivation, cache path, read (`cached_body`), write (`store_body`), and `cache_dir()`. Pure file I/O + hashing, no LLM.
- **Modify** `harness/skills.py` — in `compose()`, after parsing a skill's body, substitute the cached compressed body when compress-aware is on and a fresh cache entry exists.
- **Modify** `harness/compress_cli.py` — add a `--skills` mode to `run()` that walks the skill roots and (re)builds cache entries for each skill's body.
- **Test:** `tests/compress/test_skill_cache.py`, `tests/compress/test_skill_compose_cache.py`, append to `tests/compress/test_cli.py`.

Build order: side cache (pure) → wire into compose (read path) → CLI rebuild (write path).

---

## Task 1: Side cache — key, paths, read/write

**Files:**
- Create: `harness/compress/skill_cache.py`
- Test: `tests/compress/test_skill_cache.py`

**Interfaces:**
- Consumes: `harness.compress.rules.rules_sha256()` (existing), `harness.paths.config_dir()` (existing).
- Produces:
  - `cache_dir() -> Path` — `config_dir()/compress-cache/skills/`.
  - `cache_key(source_body: str) -> str` — `sha256(sha256(source_body) + rules_sha256())` hex, first 16 chars.
  - `cache_path(source_body: str) -> Path` — `cache_dir()/<key>.md`.
  - `cached_body(source_body: str) -> str | None` — the compressed body if a fresh cache file exists for this source+rules, else None. Never raises.
  - `store_body(source_body: str, compressed: str) -> Path` — atomically write the compressed body to `cache_path(source_body)` (temp + os.replace), creating the dir. Returns the path.

- [ ] **Step 1: Write the failing test**

```python
# tests/compress/test_skill_cache.py
from harness.compress import skill_cache, rules


def _redirect(monkeypatch, tmp_path):
    from harness import paths
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path)


def test_key_changes_with_source_and_rules(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path)
    k1 = skill_cache.cache_key("body A")
    assert len(k1) == 16
    assert skill_cache.cache_key("body B") != k1          # source changes key
    monkeypatch.setattr(rules, "rules_sha256", lambda: "0" * 64)
    assert skill_cache.cache_key("body A") != k1          # rules bump changes key


def test_store_then_cached_roundtrip(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path)
    src = "verbose original body"
    assert skill_cache.cached_body(src) is None            # miss before store
    skill_cache.store_body(src, "terse body")
    assert skill_cache.cached_body(src) == "terse body"    # hit after store


def test_cached_body_misses_when_source_changes(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path)
    skill_cache.store_body("old body", "terse")
    assert skill_cache.cached_body("new body") is None     # different key -> miss


def test_cached_body_never_raises_on_missing_dir(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path / "does-not-exist")
    assert skill_cache.cached_body("anything") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/compress/test_skill_cache.py -q`
Expected: FAIL — `ModuleNotFoundError: harness.compress.skill_cache`.

- [ ] **Step 3: Write minimal implementation**

```python
# harness/compress/skill_cache.py
"""Done-owned side cache for compressed skill BODIES. Keyed by
sha256(source_body_sha + rules_version) so freshness IS the filename: a source
edit or a compression-rules bump yields a new key and an automatic clean miss.
Never writes into skill source dirs (bundled / ~/.claude / project) — only under
config_dir()/compress-cache/skills/. Pure file I/O + hashing; no LLM."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from harness import paths
from harness.compress import rules


def cache_dir() -> Path:
    return paths.config_dir() / "compress-cache" / "skills"


def cache_key(source_body: str) -> str:
    src_sha = hashlib.sha256(source_body.encode()).hexdigest()
    material = (src_sha + rules.rules_sha256()).encode()
    return hashlib.sha256(material).hexdigest()[:16]


def cache_path(source_body: str) -> Path:
    return cache_dir() / f"{cache_key(source_body)}.md"


def cached_body(source_body: str) -> str | None:
    """The compressed body for this source+rules, or None on miss/error."""
    try:
        return cache_path(source_body).read_text(encoding="utf-8")
    except OSError:
        return None


def store_body(source_body: str, compressed: str) -> Path:
    path = cache_path(source_body)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(compressed, encoding="utf-8")
    os.replace(tmp, path)
    return path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/compress/test_skill_cache.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add harness/compress/skill_cache.py tests/compress/test_skill_cache.py
git commit -m "feat(compress): side cache for compressed skill bodies (filename-keyed freshness)"
```

---

## Task 2: Wire the cache into skill body composition

**Files:**
- Modify: `harness/skills.py` (`compose()` — the body read inside its per-name loop)
- Test: `tests/compress/test_skill_compose_cache.py`

**Interfaces:**
- Consumes: `skill_cache.cached_body(source_body)` (Task 1); `config.compress_aware_pinned("default")` (existing — the mode flag; skills aren't persona-keyed, use default like `agents._compress_on_dir`).
- Produces: `compose()` returns the COMPRESSED body for a skill when compress-aware mode is ON and a fresh cache entry exists; otherwise the original body. The menu/catalog path is unchanged.

- [ ] **Step 1: Write the failing test**

```python
# tests/compress/test_skill_compose_cache.py
from harness import skills
from harness.compress import skill_cache


def _redirect(monkeypatch, tmp_path):
    from harness import paths
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path)


def _make_skill(root, name, body):
    d = root / name; d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\n{body}")


def test_compose_uses_cached_body_when_on(tmp_path, monkeypatch):
    _redirect(monkeypatch, tmp_path / "cfg")
    monkeypatch.setattr(skills, "_compress_skills_on", lambda: True, raising=False)
    root = tmp_path / "skills"
    _make_skill(root, "foo", "VERBOSE original body of the foo skill")
    # parse the source body exactly as compose will, then seed the cache
    _, body = skills._parse_skill_md(root / "foo" / "SKILL.md")
    skill_cache.store_body(body, "terse foo")
    load = skills.compose([root], ["foo"])
    assert "terse foo" in load.block
    assert "VERBOSE original" not in load.block


def test_compose_uses_original_when_off(tmp_path, monkeypatch):
    _redirect(monkeypatch, tmp_path / "cfg")
    monkeypatch.setattr(skills, "_compress_skills_on", lambda: False, raising=False)
    root = tmp_path / "skills"
    _make_skill(root, "foo", "VERBOSE original body")
    _, body = skills._parse_skill_md(root / "foo" / "SKILL.md")
    skill_cache.store_body(body, "terse foo")
    load = skills.compose([root], ["foo"])
    assert "VERBOSE original" in load.block        # off -> original
    assert "terse foo" not in load.block


def test_compose_uses_original_on_cache_miss(tmp_path, monkeypatch):
    _redirect(monkeypatch, tmp_path / "cfg")
    monkeypatch.setattr(skills, "_compress_skills_on", lambda: True, raising=False)
    root = tmp_path / "skills"
    _make_skill(root, "foo", "VERBOSE original body")     # no cache entry stored
    load = skills.compose([root], ["foo"])
    assert "VERBOSE original" in load.block        # miss -> original (degrade)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/compress/test_skill_compose_cache.py -q`
Expected: FAIL — `compose` still uses the raw body; cached "terse foo" not substituted.

- [ ] **Step 3: Write minimal implementation**

In `harness/skills.py`, add a module-level helper and substitute in `compose`'s loop. Add near the top (after imports):

```python
from harness.compress import skill_cache as _skill_cache
from harness import config as _config


def _compress_skills_on() -> bool:
    """Skills aren't persona-keyed; use the default compress-aware flag."""
    return _config.compress_aware_pinned("default")
```

Then in `compose()`, the existing loop sets `chosen_body = body` for the winning root. AFTER the root loop picks `chosen_body` (and it is not None), apply the cache substitution before appending:

```python
        if chosen_body is None:
            load.skipped.append((name, "no valid SKILL.md in any root"))
            continue
        # Compress-aware: swap in the cached compressed body when fresh. Miss or
        # mode-off -> original. Read-only, no LLM (rebuild is `dn compress --skills`).
        if _compress_skills_on():
            cached = _skill_cache.cached_body(chosen_body)
            if cached is not None:
                chosen_body = cached
        bodies.append(f"## {name}\n{chosen_body}")
        load.injected.append(name)
```

> Do NOT touch `_parse_skill_md` or `load_catalog_with_skips` — only `compose`'s body substitution changes. The cache key is computed from `chosen_body` (the parsed original body), the same value `dn compress --skills` will key on.

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/compress/test_skill_compose_cache.py tests/test_system_skills.py tests/test_load_skill.py -q`
Expected: PASS (new tests + existing skills suites unaffected — with no cache entries, compose returns originals exactly as before).

- [ ] **Step 5: Commit**

```bash
git add harness/skills.py tests/compress/test_skill_compose_cache.py
git commit -m "feat(compress): skills.compose serves cached compressed body when fresh"
```

---

## Task 3: `dn compress --skills` rebuild

**Files:**
- Modify: `harness/compress_cli.py` (`run()` — add `--skills` flag + a rebuild path)
- Test: `tests/compress/test_cli.py` (append)

**Interfaces:**
- Consumes: `engine.compress_text(content, *, call_model)` (existing), `skill_cache.store_body` (Task 1), `skills.load_catalog_with_skips` + `skills._parse_skill_md` + `paths.skills_dirs` (existing), `compress_cli._build_call_model` (existing).
- Produces: `dn compress --skills` walks the skill roots, compresses each skill's body, and stores it in the side cache. Helper `rebuild_skill_cache(*, call_model) -> dict` returning `{built: int, skipped: int, failed: int}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/compress/test_cli.py  (append)
def test_rebuild_skill_cache_builds_entries(tmp_path, monkeypatch):
    from harness import compress_cli, skills, paths
    from harness.compress import skill_cache
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path / "cfg")
    root = tmp_path / "skills"
    d = root / "foo"; d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: foo\ndescription: d\n---\nYou should really read https://x.io")
    monkeypatch.setattr(compress_cli, "_skill_roots_for_rebuild", lambda: [root], raising=False)
    res = compress_cli.rebuild_skill_cache(call_model=lambda p: "read https://x.io")
    assert res["built"] == 1
    # the cached body is now retrievable for foo's source body
    _, body = skills._parse_skill_md(d / "SKILL.md")
    assert skill_cache.cached_body(body) == "read https://x.io"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/compress/test_cli.py -q -k rebuild_skill_cache`
Expected: FAIL — `compress_cli` has no `rebuild_skill_cache`.

- [ ] **Step 3: Write minimal implementation**

In `harness/compress_cli.py`, add the rebuild helper, a roots resolver, and a `--skills` branch in `run()`:

```python
def _skill_roots_for_rebuild() -> list[Path]:
    """Skill roots to (re)build cache for. cwd project roots + user/global/bundled."""
    from harness import paths
    return paths.skills_dirs(project_cwd=os.getcwd())


def rebuild_skill_cache(*, call_model) -> dict:
    """Compress each skill's body across the roots into the side cache.
    Returns counts. Later roots override earlier by name (same as compose)."""
    from harness import skills
    from harness.compress import engine, skill_cache
    seen: dict[str, Path] = {}
    for root in _skill_roots_for_rebuild():
        if not Path(root).is_dir():
            continue
        for child in sorted(Path(root).iterdir()):
            md = child / "SKILL.md"
            if md.is_file():
                seen[child.name] = md          # later root wins
    built = skipped = failed = 0
    for name, md in seen.items():
        try:
            _, body = skills._parse_skill_md(md)
        except Exception:
            skipped += 1
            continue
        try:
            compressed = engine.compress_text(body, call_model=call_model)
        except engine.CompressionError:
            failed += 1
            continue
        skill_cache.store_body(body, compressed)
        built += 1
    return {"built": built, "skipped": skipped, "failed": failed}
```

Then in `run()`, add a `--skills` arg and branch (near the existing `--status` handling). Read the real `run()` first; add:

```python
    ap.add_argument("--skills", action="store_true",
                    help="(re)build the compressed-skill-body side cache")
    # ... after ns = ap.parse_args(argv) and the .env load:
    if ns.skills:
        call_model = _build_call_model()
        if call_model is None:
            print("compression unavailable: set [harness] compress_model in done.conf "
                  "(or COMPRESS_MODEL / VIBEPROXY_MODEL)")
            return 0
        res = rebuild_skill_cache(call_model=call_model)
        print(f"skills: built {res['built']}, skipped {res['skipped']}, failed {res['failed']}")
        return 0
```

> Place the `--skills` branch BEFORE the default file-targets logic so `dn compress --skills` doesn't also try to treat skills as file paths.

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/compress/test_cli.py -q`
Expected: PASS (existing CLI tests + the new rebuild test).

- [ ] **Step 5: Commit**

```bash
git add harness/compress_cli.py tests/compress/test_cli.py
git commit -m "feat(compress): dn compress --skills rebuilds the skill-body cache"
```

---

## Task 4: Full suite + docs

**Files:**
- Modify: `docs/compress-aware.md` (add a "Skills" subsection) OR `docs/review.md`-style note — extend the existing compress-aware doc.
- Test: full suite

- [ ] **Step 1: Extend the docs**

In `docs/compress-aware.md`, add a short subsection after "Which files":

> **Skills (opt-in, cached).** Skill *bodies* (the prose after a skill's
> frontmatter) are compressed too, but via a Done-owned side cache under
> `~/.config/harness/compress-cache/skills/` — never as files next to the
> source skills (which may be bundled or shared with other tools). Build the
> cache with `dn compress --skills`. On `load_skill`, a fresh cached body is
> used; a miss loads the original. The skill *menu* is never compressed (it's
> tiny and frontmatter-only).

- [ ] **Step 2: Run the full suite**

Run: `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/ -q`
Expected: PASS except the known pre-existing `tests/jobs/test_service_launchd.py` baseline failure (and the flaky TUI-Pilot cluster if it trips under the broad run — verify in isolation, not a regression).

- [ ] **Step 3: Commit**

```bash
git add docs/compress-aware.md
git commit -m "docs: skills body compression (side cache, dn compress --skills)"
```

---

## Self-Review

**Spec coverage:**
- Compress bodies via side cache, not menu → Task 1 (cache) + Task 2 (compose hook only). ✓
- Filename-encoded freshness key (source + rules version) → Task 1 `cache_key`. ✓
- Never write into source/bundled/cross-tool dirs → cache under `config_dir()` only (Task 1). ✓
- Menu/`_parse_skill_md` untouched → Task 2 hooks `compose` only; Step 3 note + the `test_system_skills`/`test_load_skill` regression gate in Task 2 Step 4. ✓
- Read path no-LLM, degrade-to-original on miss → Task 2 (`cached_body` → None → original). ✓
- Offline rebuild via `dn compress --skills`, model resolution reused → Task 3. ✓
- Mode gated on compress-aware flag (default) → Task 2 `_compress_skills_on`. ✓
- Docs → Task 4. ✓

**Placeholder scan:** Task 3 Step 3 says "read the real `run()` first" before adding the `--skills` branch — that's matching live code the implementer must read, not a lazy placeholder; the branch code itself is complete. No TBD/TODO elsewhere; every code step has real code.

**Type consistency:** `cache_key/cache_path/cached_body/store_body(source_body: str)` consistent across Tasks 1–3. `rebuild_skill_cache(*, call_model) -> dict{built,skipped,failed}` consistent (Task 3). `_compress_skills_on() -> bool` consistent (Task 2). The cache is keyed on the **parsed body** (`_parse_skill_md(...)[1]`) in both the read (Task 2) and write (Task 3) paths — same value, so keys match.

**Known follow-ups (note at handoff):** real-model compression only runs with a model configured (like the rest of compress); a `dn compress --skills --status` / orphan-cleanup pass is not in scope (stale entries are harmless orphans, keyed out automatically). Bundled-skill bodies ARE cached here at runtime via the side cache (no wheel write) — the spec's "build-time only for bundled" musing is moot since the side cache sidesteps the wheel entirely.
