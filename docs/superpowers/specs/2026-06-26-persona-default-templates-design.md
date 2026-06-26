# Persona default templates — design

**Status:** design / spec (ready for writing-plans)
**Date:** 2026-06-26
**Author:** Alberto Vasquez (with Claude Opus 4.8)
**Builds on:** Phase A persona contract (merged, PR #20) —
`2026-06-26-phaseA-persona-contract-design.md`

---

## 1. Purpose & the central constraint

A fresh install ships **inert persona templates** so a new user discovers
editable scaffolding at `~/.config/harness/agents/default/` instead of an empty
directory they don't know to create.

**The load-bearing constraint:** Phase A's safety guarantee is the
**byte-identical no-op** — an absent or empty default workspace produces zero
behavior change, locked by tests. Shipping *populated* files normally breaks that.
This design preserves the no-op by making the shipped templates **inert**: they
are read by the engine but inject nothing, until the user replaces them with real
text.

**In scope:** an inert-template rule in `compose_persona`; three bundled template
files; a create-if-absent seeding step at startup; tests.

**Out of scope (deferred to Phase D onboarding):** a `BOOTSTRAP.md` first-run
ritual; a state-dir attestation marker; multi-persona selection. We use the
simplest safe seeding (create-if-absent, never overwrite), not full attestation.

---

## 2. The inertness mechanism (Section 1, approved)

A template must be **visible/editable to the user** but **silent to the model**.
Because the engine injects whatever non-blank content it finds, inertness must be
an engine rule.

**Rule:** a persona file is treated as blank (skipped, not injected) if, after
removing all HTML comment blocks (`<!-- … -->`), nothing but whitespace remains.

**Why HTML comments, not `#`:** in Markdown, `# SOUL` is a *heading* a user may
well want injected. `#` is therefore ambiguous and must NOT be treated as a
comment marker. `<!-- … -->` is unambiguously inert in Markdown — no user writes
one intending it to reach the model. So the comment marker is HTML comments only.

**All-or-nothing:** the rule decides whether to skip the *whole file*. It does not
strip comments out of otherwise-real content. A file with a real line plus a stray
`<!-- note -->` injects verbatim (the stray comment is harmless). Only a file that
is *entirely* comments + whitespace is skipped.

Implementation: a helper `_meaningful(raw: str) -> bool` returns False when the
comment-stripped remainder is empty-after-strip. `compose_persona`'s blank check
becomes `if not _meaningful(raw):` (currently `if not raw.strip():`). The skip
reason stays `"blank"` (a template IS blank for injection purposes; no new reason
needed). The existing whitespace-only behavior is a strict subset and still holds.

```python
import re
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)

def _meaningful(raw: str) -> bool:
    """True if the file has injectable content — i.e. anything but whitespace
    remains after HTML comments are removed. A comment-only template => False
    (skipped, never injected) so shipped templates preserve the no-op."""
    return bool(_HTML_COMMENT.sub("", raw).strip())
```

---

## 3. Seeding from bundled package-data (Section 2, approved)

The engine READS persona files from `~/.config/harness/agents/default/` (a
user-writable location) — you cannot edit files inside an installed wheel. So
shipping templates means **copying them into that dir on first run.**

- **Where template content lives:** bundled as package-data at
  `harness/templates/agents/default/{SOUL,IDENTITY,USER}.md`, resolved via
  `importlib.resources` (same mechanism as `bundled_skills_dir()`), so it survives
  a non-editable wheel install with no source tree.
- **When seeding happens:** a `seed_default_workspace()` function, called once at
  entrypoint startup (`acp_main`, `run_traced`). It is **idempotent and
  non-clobbering**:
  - Seed only when `~/.config/harness/agents/default/` **does not exist**. If the
    user has the directory (even with files deleted), never re-seed — this is the
    simplest safe version of OpenClaw's "don't clobber a wiped-but-real workspace"
    rule (full attestation is Phase D).
  - Even on a fresh seed, **never overwrite an existing file** (defensive:
    `if not dest.exists()` per file).
  - Best-effort: a failed copy (e.g. read-only home) must never break startup —
    wrap in try/except, like `config.save_default`.

**New side effect:** the engine now WRITES to `~/.config` at startup (create-if-
absent). The engine previously only read there. This is benign (idempotent
create) and has precedent — `config.save_default` already writes `done.conf` to
the same config dir.

```python
def seed_default_workspace() -> None:
    """Copy the bundled inert templates into ~/.config/harness/agents/default/
    on first run. No-op if the dir already exists. Never overwrites a file.
    Best-effort: never raises into the startup path."""
    dest = paths.default_workspace_dir()
    if dest.exists():
        return                                  # user has a workspace; never clobber
    try:
        src = paths.bundled_persona_templates_dir()
        dest.mkdir(parents=True, exist_ok=True)
        for name in PERSONA_FILES:               # SOUL.md, IDENTITY.md, USER.md
            s, d = src / name, dest / name
            if s.is_file() and not d.exists():
                d.write_text(s.read_text(encoding="utf-8"), encoding="utf-8")
    except OSError:
        pass                                     # read-only home etc. — never break startup
```

`paths.bundled_persona_templates_dir()` mirrors `bundled_skills_dir()`:
`Path(importlib.resources.files("harness")) / "templates" / "agents" / "default"`.

---

## 4. Template content (Section 3, approved)

Three files, each a single self-documenting HTML comment — inert until edited.

`harness/templates/agents/default/SOUL.md`:
```markdown
<!-- SOUL.md — the agent's persona: tone, boundaries, how it behaves.
     Replace this comment with a sentence or two. Anything you write here is
     read into the agent's context. Example:
     "You are concise and pragmatic. You explain only when asked." -->
```

`harness/templates/agents/default/IDENTITY.md`:
```markdown
<!-- IDENTITY.md — the agent's name, vibe, emoji.
     Replace this comment. Example: "Name: Ada. Dry wit. 🛠️" -->
```

`harness/templates/agents/default/USER.md`:
```markdown
<!-- USER.md — who you are and how you want to be addressed.
     Replace this comment. Example:
     "I'm Alberto; prefer terse, code-first answers." -->
```

No files beyond the trio Phase A reads (YAGNI).

---

## 5. Testing (Section 4, approved)

Project discipline: `tests/` only, `.venv/bin/python -m pytest tests/ -q`.

**Inertness rule (`_meaningful` / blank check) — `tests/test_persona.py`:**
- HTML-comment-only file → skipped (`"blank"`), `block == ""`, not in `injected`.
- Comment + a real line → injected; the whole file body appears (comment included
  is acceptable).
- Markdown `#` heading (e.g. `# SOUL`) → injected, NOT mistaken for a comment.
- Whitespace-only file → still skipped (existing test must continue to pass).

**Seeding (no-clobber) — `tests/test_persona.py` or `tests/test_paths.py`,
using `monkeypatch.setenv("XDG_CONFIG_HOME", tmp_path)`:**
- Absent dir → `seed_default_workspace()` creates it and copies all three
  templates; contents match the bundled files.
- Existing dir → **not** re-seeded (a sentinel file left in the dir is untouched;
  templates are NOT copied over it).
- Existing dir with one file present → that file is never overwritten.
- Read-only/again-failing copy → no raise (best-effort).

**The no-op regression (the critical test):**
- A freshly *seeded* default workspace (templates only, unedited) → byte-identical
  agent/chat behavior AND **no `persona_load` event** — seeding changed nothing
  observable. This proves the Phase A guarantee survives. (Drive via the
  `test_acp_session_context.py` harness: point `_workspace_dir` at a seeded dir,
  assert the chat message list has no system message and no `persona_load` meta.)

**Packaging — `tests/test_packaging.py`:**
- `paths.bundled_persona_templates_dir()` resolves and the three template files
  are present (package-data is included), the same way the skills package-data is
  asserted today.

---

## 6. Files touched

| File | Change |
|---|---|
| `harness/persona.py` | add `_meaningful(raw)` + `_HTML_COMMENT`; blank check uses `_meaningful`; add `seed_default_workspace()` |
| `harness/templates/agents/default/SOUL.md` | **new** — inert template |
| `harness/templates/agents/default/IDENTITY.md` | **new** — inert template |
| `harness/templates/agents/default/USER.md` | **new** — inert template |
| `harness/paths.py` | add `bundled_persona_templates_dir()` |
| `harness/acp_main.py` | call `persona.seed_default_workspace()` once at `_main` startup |
| `harness/run_traced.py` | call `persona.seed_default_workspace()` once at startup |
| `pyproject.toml` | add `templates/**/*` to `[tool.setuptools.package-data]` `"harness"` |
| `tests/` | inertness rule; seeding no-clobber; no-op regression; packaging |
| `docs/personas.md` | one line: a fresh install seeds inert templates you can edit |

---

## 7. Success criteria

1. A comment-only persona file injects nothing and is skipped — unit test.
2. `seed_default_workspace()` copies templates only when the dir is absent, never
   overwrites a file, never raises into startup — unit tests.
3. A freshly seeded (unedited) default workspace is **byte-identical** to no
   workspace, including **no `persona_load` event** — the no-op regression passes.
4. The bundled templates resolve via `importlib.resources` after a wheel install
   (package-data) — packaging test.
5. `.venv/bin/python -m pytest tests/ -q` green; the existing Phase A persona
   tests still pass unchanged (the inertness rule is a superset of the old blank
   rule).
6. No `BOOTSTRAP.md`, no attestation marker, no selection logic introduced (scope
   held — that's Phase D).
