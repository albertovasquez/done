# Phase A: System Skills (superpowers import) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Replace the 3 placeholder skills with 4 curated obra/superpowers skills (MIT) bundled as system skills the Router selects per request.

**Architecture:** Pure content + test changes — NO runtime code. Copy 4 SKILL.md files into `harness/skills/<name>/`, surgically strip dangling refs, delete the 3 placeholders, add `NOTICE.md` attribution, repoint test fixtures that named the removed skills, add a system-skills test, update the README. The existing Phase 2/3 router→compose→inject path consumes the new catalog unchanged.

**Tech Stack:** Markdown skill files; pytest; the existing `harness/skills.py` loader.

**Spec:** `docs/superpowers/specs/2026-06-26-system-skills-design.md`

## Global Constraints

- **The 4 skills:** `test-driven-development`, `systematic-debugging`, `verification-before-completion`, `receiving-code-review`. (`requesting-code-review` is deferred to Phase C — needs subagent dispatch.)
- **License:** obra/superpowers is MIT — retain the LICENSE text + attribution in `harness/skills/NOTICE.md`. Do NOT bundle their `scripts/`. Pin to a commit SHA, not `main`.
- **Loader contract:** every shipped `harness/skills/<name>/SKILL.md` must have frontmatter `name` == its dir name; `description` non-empty; body injected verbatim; no `{{ }}` in bodies.
- **Surgical body edits only:** strip dead cross-refs/links; NEVER rewrite the methodology text.
- **No runtime code changes** — `harness/skills.py`, `harness/router.py` etc. are untouched.
- **User-skill override preserved** — `~/.config/harness/skills/<name>` still wins over a bundled skill of the same name (Phase 6 `skills_dirs` ordering; unchanged).
- **Test command (from worktree root):** `/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/ -q` (target `tests/` only). Establish the baseline pass count before Task 1.
- **`requesting-code-review` is OUT** — do not import it.

## File Structure

- Create: `harness/skills/test-driven-development/SKILL.md`
- Create: `harness/skills/systematic-debugging/SKILL.md`
- Create: `harness/skills/verification-before-completion/SKILL.md`
- Create: `harness/skills/receiving-code-review/SKILL.md`
- Create: `harness/skills/NOTICE.md` (attribution + MIT text + pinned SHA)
- Delete: `harness/skills/git-pr-flow/`, `harness/skills/python-testing/`, `harness/skills/poker-domain-rules/`
- Create: `tests/test_system_skills.py`
- Modify: `tests/test_router.py` (the `_CATALOG` + skill-name assertions)
- Modify: `tests/test_run_traced.py` (one skills-list label)
- Modify: `README.md` (system-skills section)

---

### Task 1: Import the 4 skill files (pinned, cleaned)

**Files:**
- Create: the 4 `harness/skills/<name>/SKILL.md` above

**Interfaces:**
- Produces: 4 bundled skills discoverable by `skills.load_catalog(skills_dirs())`, each with frontmatter `name`==dir and a non-empty `description`.

- [ ] **Step 1: Record the pinned commit SHA**

Resolve obra/superpowers' current `main` commit SHA (the import pin):
```bash
curl -s https://api.github.com/repos/obra/superpowers/commits/main | grep '"sha"' | head -1
```
Save the SHA — it goes in NOTICE.md (Task 3) and is the URL base for the fetches below. Use `https://raw.githubusercontent.com/obra/superpowers/<SHA>/skills/<name>/SKILL.md` for each fetch so the import is reproducible.

- [ ] **Step 2: Fetch the 4 SKILL.md files into place**

```bash
cd "$(git rev-parse --show-toplevel)"   # the system-skills worktree root
SHA=<the SHA from step 1>
for s in test-driven-development systematic-debugging verification-before-completion receiving-code-review; do
  mkdir -p "harness/skills/$s"
  curl -s "https://raw.githubusercontent.com/obra/superpowers/$SHA/skills/$s/SKILL.md" -o "harness/skills/$s/SKILL.md"
done
```

- [ ] **Step 3: Verify each parses + name==dir BEFORE editing**

```bash
/Users/alberto/Work/Quiubo/harness/.venv/bin/python - <<'PY'
import sys; sys.path.insert(0,'.'); sys.path.insert(0,'upstream/src')
from pathlib import Path
from harness.skills import _parse_skill_md
for d in sorted(Path("harness/skills").iterdir()):
    if not d.is_dir(): continue
    data, body = _parse_skill_md(d/"SKILL.md")
    assert data.get("name")==d.name, (d.name, data.get("name"))
    assert data.get("description"), d.name
    print("OK", d.name, "| desc:", data["description"][:50])
PY
```
Expected: `OK` for all 4 (plus the 3 placeholders still present at this point). If any fails name==dir or empty description, the fetch is wrong — stop and re-check the SHA/path.

- [ ] **Step 4: Surgical cleanup — `test-driven-development`**

Open `harness/skills/test-driven-development/SKILL.md`. Find the line linking the sibling file (it reads like):
```
When adding mocks or test utilities, read [testing-anti-patterns.md](testing-anti-patterns.md) to avoid common pitfalls:
```
We do not bundle that file (loader injects only SKILL.md). Remove the markdown LINK but keep the surrounding guidance readable — replace the link with plain text or drop the sentence if it only points at the file. Do NOT remove the TDD methodology. Leave everything else (including `dot` blocks) verbatim.

- [ ] **Step 5: Surgical cleanup — `systematic-debugging`**

Open `harness/skills/systematic-debugging/SKILL.md`. It has 3 `superpowers:` references, all pointing at skills in OUR set:
```
- Use the `superpowers:test-driven-development` skill for writing proper failing tests
- **superpowers:test-driven-development** - For creating failing test case (Phase 4, Step 1)
- **superpowers:verification-before-completion** - Verify fix worked before claiming success
```
De-namespace each: drop the `superpowers:` prefix so they read as plain skill mentions (e.g. "Use the test-driven-development skill…", "**test-driven-development** - For creating…"). The referenced skills ARE bundled, so the mentions stay meaningful. Change nothing else.

- [ ] **Step 6: Confirm the other 2 are clean (no edit)**

`verification-before-completion` and `receiving-code-review` have no cross-refs or sibling links (verified). Confirm with:
```bash
grep -nE "superpowers:|\]\([a-zA-Z0-9._/-]+\.(md|sh|js|ts)\)" harness/skills/verification-before-completion/SKILL.md harness/skills/receiving-code-review/SKILL.md || echo "CLEAN"
```
Expected: `CLEAN` (or no output). If anything shows, strip it the same surgical way.

- [ ] **Step 7: Re-verify all 4 still parse after edits**

Re-run the Step 3 script. Expected: `OK` for all 4 (name==dir, non-empty desc preserved).

- [ ] **Step 8: Commit**

```bash
git add harness/skills/test-driven-development harness/skills/systematic-debugging harness/skills/verification-before-completion harness/skills/receiving-code-review
git commit -m "feat(skills): import 4 superpowers skills (cleaned, pinned)"
```

---

### Task 2: Remove the 3 placeholder skills

**Files:**
- Delete: `harness/skills/git-pr-flow/`, `harness/skills/python-testing/`, `harness/skills/poker-domain-rules/`

**Interfaces:**
- Produces: a `harness/skills/` containing exactly the 4 imported skills (+ NOTICE.md after Task 3).

- [ ] **Step 1: Delete the three placeholder dirs**

```bash
git rm -r harness/skills/git-pr-flow harness/skills/python-testing harness/skills/poker-domain-rules
```

- [ ] **Step 2: Verify the catalog now has exactly the 4**

```bash
/Users/alberto/Work/Quiubo/harness/.venv/bin/python - <<'PY'
import sys; sys.path.insert(0,'.'); sys.path.insert(0,'upstream/src')
from pathlib import Path
from harness.skills import load_catalog
cat = dict(load_catalog([Path("harness/skills")]))
print("catalog:", sorted(cat))
assert set(cat) == {"test-driven-development","systematic-debugging",
                    "verification-before-completion","receiving-code-review"}, sorted(cat)
print("OK — exactly the 4 system skills")
PY
```
Expected: `OK — exactly the 4 system skills`.

- [ ] **Step 3: Commit**

```bash
git add -A harness/skills
git commit -m "chore(skills): remove placeholder test skills (replaced by system skills)"
```

---

### Task 3: Attribution — NOTICE.md

**Files:**
- Create: `harness/skills/NOTICE.md`

**Interfaces:** none (a data file; ships in the wheel via the existing `"harness" = ["skills/**/*"]` package-data glob).

- [ ] **Step 1: Write `harness/skills/NOTICE.md`**

Use the SHA from Task 1 Step 1. Content:
```markdown
# Skill attribution

The following bundled skills are imported from **obra/superpowers**
(https://github.com/obra/superpowers), licensed MIT, at commit `<SHA>`:

- test-driven-development
- systematic-debugging
- verification-before-completion
- receiving-code-review

Bodies were lightly edited (dead cross-references and sibling-file links removed)
to suit this harness's skill loader; the methodology content is unchanged.

---

<paste the full MIT LICENSE text from
https://raw.githubusercontent.com/obra/superpowers/<SHA>/LICENSE here>
```
Fetch the license text to paste:
```bash
curl -s "https://raw.githubusercontent.com/obra/superpowers/<SHA>/LICENSE"
```

- [ ] **Step 2: Confirm NOTICE.md is NOT picked up as a skill**

`load_catalog` only scans subdirectories with a `SKILL.md`; a top-level `NOTICE.md` is ignored. Verify the catalog is still exactly the 4:
```bash
/Users/alberto/Work/Quiubo/harness/.venv/bin/python -c "import sys; sys.path.insert(0,'.'); sys.path.insert(0,'upstream/src'); from pathlib import Path; from harness.skills import load_catalog; print(len(load_catalog([Path('harness/skills')])), 'skills')"
```
Expected: `4 skills`.

- [ ] **Step 3: Commit**

```bash
git add harness/skills/NOTICE.md
git commit -m "docs(skills): NOTICE.md — MIT attribution for imported superpowers skills"
```

---

### Task 4: Repoint test fixtures off the removed skill names

**Files:**
- Modify: `tests/test_router.py`
- Modify: `tests/test_run_traced.py`
- (Do NOT touch `tests/test_skills.py` — it uses synthetic `_write_skill(tmp_path,…)` skills, independent of the bundled set.)

**Interfaces:**
- Consumes: the 4 imported skill names (Task 1).

- [ ] **Step 1: Run the suite to see the expected failures first**

```bash
/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_router.py tests/test_run_traced.py -q
```
Expected: failures in tests asserting `poker-domain-rules`/`python-testing` (the names no longer exist as bundled skills — though these tests use literal strings, so they may still PASS since the router tests use a hardcoded `_CATALOG`; confirm what actually fails and update accordingly). The point of this step is to know the real baseline before editing.

- [ ] **Step 2: Update `tests/test_router.py` `_CATALOG` (lines 8-11)**

Replace:
```python
_CATALOG = [
    ("poker-domain-rules", "Poker rake/rakeback math and PPPoker domain logic"),
    ("python-testing", "Write and run pytest unit/integration tests"),
]
```
with real imported names:
```python
_CATALOG = [
    ("systematic-debugging", "Use when encountering any bug, test failure, or unexpected behavior"),
    ("test-driven-development", "Use when implementing any feature or bugfix, before writing implementation code"),
]
```

- [ ] **Step 3: Update the skill-name assertions in `tests/test_router.py`**

In `test_1_parses_validates_skills_and_unknown_type` (lines ~50, 55) replace `"poker-domain-rules"` with `"systematic-debugging"`:
```python
        "skills": ["systematic-debugging", "not-a-real-skill"],
```
```python
    assert c.skills == ["systematic-debugging"]      # hallucinated dropped
```
And in the later malformed-types test (line ~98) replace the scalar `"poker-domain-rules"` with `"systematic-debugging"`:
```python
    c = Router(_stub(json.dumps({"task_type": "code_fix", "skills": "systematic-debugging",
```
(These tests validate skill-filtering against the catalog; they only need names that ARE in `_CATALOG`.)

- [ ] **Step 4: Update `tests/test_run_traced.py` (lines 227, 235)**

The skill name there is a label fed to a STUB `load_skills` (not a real lookup). Replace both `"poker-domain-rules"` occurrences with `"systematic-debugging"`:
```python
        router=_FixedRouter(_cls("code_fix", confidence=0.9, skills=["systematic-debugging"])),
```
```python
    assert len(sl) == 1 and sl[0]["data"]["injected"] == ["systematic-debugging"]
```

- [ ] **Step 5: Run the two test files green**

```bash
/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_router.py tests/test_run_traced.py -q
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add tests/test_router.py tests/test_run_traced.py
git commit -m "test: repoint fixtures off removed placeholder skills"
```

---

### Task 5: System-skills test

**Files:**
- Create: `tests/test_system_skills.py`

**Interfaces:**
- Consumes: `harness.skills.load_catalog`, `harness.skills.compose`, `harness.paths.bundled_skills_dir`.

- [ ] **Step 1: Write the test**

```python
import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

from pathlib import Path
from harness.skills import load_catalog, compose, _parse_skill_md
from harness import paths

EXPECTED = {"test-driven-development", "systematic-debugging",
            "verification-before-completion", "receiving-code-review"}
REMOVED = {"git-pr-flow", "python-testing", "poker-domain-rules"}


def _bundled() -> Path:
    return Path(paths.bundled_skills_dir())


def test_catalog_is_exactly_the_four_system_skills():
    cat = dict(load_catalog([_bundled()]))
    assert set(cat) == EXPECTED, sorted(cat)
    for name, desc in cat.items():
        assert desc.strip(), f"{name} has empty description"


def test_removed_placeholders_are_gone():
    cat = dict(load_catalog([_bundled()]))
    assert REMOVED.isdisjoint(cat), REMOVED & set(cat)


def test_each_skill_composes_to_nonempty_body():
    for name in EXPECTED:
        load = compose([_bundled()], [name])
        assert load.injected == [name], (name, load.skipped)
        assert load.block.strip(), f"{name} composed to empty block"


def test_every_shipped_skill_name_matches_dir():
    for d in sorted(_bundled().iterdir()):
        if not d.is_dir():
            continue
        data, _ = _parse_skill_md(d / "SKILL.md")
        assert data.get("name") == d.name, (d.name, data.get("name"))


def test_no_dangling_superpowers_refs_in_bodies():
    # imported bodies must not tell the agent to use a skill we didn't bundle
    for name in EXPECTED:
        body = (_bundled() / name / "SKILL.md").read_text(encoding="utf-8")
        assert "superpowers:" not in body, f"{name} still has a superpowers: ref"
```

- [ ] **Step 2: Run it**

```bash
/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/test_system_skills.py -q
```
Expected: 5 passed. (If `test_no_dangling_superpowers_refs_in_bodies` fails, Task 1 Step 5 missed a ref — fix the body, not the test.)

- [ ] **Step 3: Commit**

```bash
git add tests/test_system_skills.py
git commit -m "test: assert the 4 system skills load, compose, and have no dangling refs"
```

---

### Task 6: README — document the system skills

**Files:**
- Modify: `README.md`

**Interfaces:** none.

- [ ] **Step 1: Find where skills are (or aren't) described**

```bash
grep -n "skill\|Skill\|Router\|router\|--yolo" README.md
```
Identify the section that explains routing/skills (or the best place to add one — likely near the architecture/how-it-works section).

- [ ] **Step 2: Add a "System skills" subsection**

Insert (adapt heading level to the surrounding doc):
```markdown
### System skills

DoneDone ships with a curated set of engineering-methodology skills (imported
from [obra/superpowers](https://github.com/obra/superpowers), MIT). The Router
auto-selects the relevant ones per request and injects them into the agent's
context:

- **test-driven-development** — write the failing test first
- **systematic-debugging** — root-cause before fixing
- **verification-before-completion** — prove it works before claiming done
- **receiving-code-review** — how to respond to review feedback

Add your own skills in `~/.config/harness/skills/<name>/SKILL.md`; a user skill
with the same name as a bundled one overrides it.
```

- [ ] **Step 3: Confirm `--yolo` is in the flags table; add if missing**

```bash
grep -n "yolo" README.md
```
If absent, add to the flags table:
```markdown
| `--yolo` | flag | off | auto-allow every command — never prompt for permission |
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: document bundled system skills + user-skill override"
```

---

### Task 7: Full suite + wheel-content check

**Files:** none (verification).

- [ ] **Step 1: Run the full suite**

```bash
/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m pytest tests/ -q
```
Expected: all pass (baseline count from before Task 1, plus the 5 new system-skills tests; minus none — the repointed tests still count).

- [ ] **Step 2: Confirm the new skills + NOTICE ship in the wheel**

The Phase-6 `tests/test_packaging.py` already asserts `harness/skills/*/SKILL.md` is in the wheel. Build and spot-check NOTICE + a new skill:
```bash
/Users/alberto/Work/Quiubo/harness/.venv/bin/python -m build --wheel --outdir /tmp/sk_wheel 2>&1 | tail -1
/Users/alberto/Work/Quiubo/harness/.venv/bin/python - <<'PY'
import zipfile, glob
names = zipfile.ZipFile(glob.glob("/tmp/sk_wheel/*.whl")[0]).namelist()
assert any(n.endswith("harness/skills/systematic-debugging/SKILL.md") for n in names), "skill missing from wheel"
assert any(n.endswith("harness/skills/NOTICE.md") for n in names), "NOTICE missing from wheel"
print("OK — skills + NOTICE ship in the wheel")
PY
rm -rf /tmp/sk_wheel build
```
Expected: `OK — skills + NOTICE ship in the wheel`.

- [ ] **Step 3: Manual smoke (documented, optional, needs live proxy)**

Run a debugging-flavored prompt and confirm the router selects `systematic-debugging`:
```
dn --model vibeproxy   →  "the auth test fails intermittently, why?"
expect the [classified: … · skills: systematic-debugging …] chip
```
Record the result in the PR body. (Selection is a live-model judgment; not unit-tested.)

---

## Self-Review

**Spec coverage:** import 4 skills (Task 1) ✓; per-skill cleanups (Task 1 Steps 4-6) ✓; remove placeholders (Task 2) ✓; NOTICE/attribution + pinned SHA (Tasks 1.1, 3) ✓; test-fixture updates — router + run_traced only, test_skills untouched (Task 4) ✓; system-skills test incl. name==dir + no-dangling-refs (Task 5) ✓; README incl. user-override + --yolo (Task 6) ✓; full suite + wheel ship (Task 7) ✓; no runtime code changes (no task touches harness/*.py) ✓; user-override behavior unchanged (no skills_dirs change) ✓.

**Placeholder scan:** none — every step has concrete commands/code. The two body-edit steps (1.4, 1.5) quote the real upstream lines to find; the exact replacement is "drop the link / drop the `superpowers:` prefix" — surgical and unambiguous.

**Type consistency:** skill names are identical everywhere (`test-driven-development`, `systematic-debugging`, `verification-before-completion`, `receiving-code-review`); `EXPECTED`/`REMOVED` sets in Task 5 match Tasks 1-2; the repointed fixture name (`systematic-debugging`) is one of the 4 imported. `load_catalog`/`compose` take a list of roots (Phase 6 signature) — consistent with Task 5's `[_bundled()]` calls.
