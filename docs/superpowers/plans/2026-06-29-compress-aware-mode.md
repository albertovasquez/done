# Compress-Aware Mode Implementation Plan (Phase 1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Load pre-compressed shadow copies (`FOO.compressed.md`) of prose context files into the agent's prompt instead of the originals when fresh, cutting input tokens — without ever changing the agent's response style.

**Architecture:** A pure, offline compression engine (vendored from `caveman-compress`, rewired onto the harness model layer) writes `*.compressed.md` siblings carrying a 3-part freshness header. A read-side loader, inserted at the existing per-file-type read functions (`compose_persona`, `resolve_memory`, `resolve_agents`), swaps in a fresh sibling (header stripped) when compress-aware mode is ON, else loads the original. Regeneration is offline via `dn compress`. Memory is compressed destructively at write time.

**Tech Stack:** Python 3.11+, stdlib `tomllib`/`hashlib`, pytest, Textual (TUI chip), litellm via `harness.vibeproxy` / `harness.models_mock`.

**Spec:** `docs/superpowers/specs/2026-06-29-context-friendly-mode-design.md`
**Issue:** https://github.com/albertovasquez/done/issues/186

## Global Constraints

- Python `>=3.11` (stdlib `tomllib`). Copied verbatim from existing `pyproject`.
- Test command from worktree root: `.venv/bin/python -m pytest tests/ -q`
- LLM-calling code MUST route through the harness model layer: when `VIBEPROXY_MODEL` is unset, tests get `harness.models_mock.build_mock_model()`; never call the `anthropic` SDK or `claude` CLI directly (the vendored engine's original `call_claude` is replaced).
- The read path (loader) does **NO** LLM calls and **NO** network — pure file I/O + hashing.
- Freshness key has **three** parts; a sibling is fresh only if **all** match: `source-sha256` (source bytes) AND `engine-version` + `rules-sha256` (compressor identity) AND `body-sha256` (sibling body unchanged).
- The metadata header is stripped before any compressed body enters a prompt.
- Sibling writes are atomic: temp file → fsync → rename. Never header-then-body in place.
- Default ON. `done.conf` flag name: `compress_aware`. Chip label: `compress-aware`.
- Target files (input compression): `SOUL.md`, `IDENTITY.md`, `USER.md`, `MEMORY.md`, `AGENTS.md`, `CLAUDE.md`.
- Never mutate originals (except memory, which is destructive-at-write by explicit decision).

---

## File Structure

- **Create** `harness/compress/__init__.py` — package marker.
- **Create** `harness/compress/rules.py` — vendored prompt builders + `strip_llm_wrapper` + `RULES_VERSION`/`rules_sha256()`. The "caveman style rules". Pure, no I/O.
- **Create** `harness/compress/validate.py` — vendored validator (exact-preservation checks). Pure.
- **Create** `harness/compress/engine.py` — `compress_text(original, *, model) -> str`: orchestrates compress → validate → fix-retry against the harness model layer. The only LLM-calling unit.
- **Create** `harness/compress/sibling.py` — header build/parse, sha256 helpers, freshness verdict, atomic write, path-safety. Pure file I/O, no LLM.
- **Create** `harness/compress/loader.py` — `load_context_file(source_path, *, mode_on) -> str`: the read-side swap (fresh sibling body vs original). No LLM.
- **Create** `harness/compress_cli.py` — `run(argv) -> int`: `dn compress` (rebuild) + `--status`.
- **Modify** `harness/persona.py` — route `SOUL/IDENTITY/USER.md` reads through `loader.load_context_file`.
- **Modify** `harness/memory.py` — route `MEMORY.md` read through loader; add destructive compress-at-write helper.
- **Modify** `harness/agents.py` — route tier `AGENTS.md`/`CLAUDE.md` reads through loader.
- **Modify** `harness/config.py` — add `compress_aware` pinned flag read/write.
- **Modify** `harness/tui_main.py` — dispatch `dn compress`.
- **Modify** `harness/tui/widgets/status_chip.py` + `harness/tui/app.py` — `compress-aware` chip.
- **Create** `tests/compress/` — test modules per component.

The loader is the linchpin: persona/memory/agents all call into it, so it must be built and tested before the call-site edits.

---

## Task 1: Compression rules (vendored, pure)

**Files:**
- Create: `harness/compress/__init__.py`
- Create: `harness/compress/rules.py`
- Test: `tests/compress/test_rules.py`

**Interfaces:**
- Produces: `RULES_VERSION: str`; `rules_sha256() -> str`; `build_compress_prompt(original: str) -> str`; `build_fix_prompt(original: str, compressed: str, errors: list[str]) -> str`; `strip_llm_wrapper(text: str) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# tests/compress/test_rules.py
from harness.compress import rules


def test_rules_sha256_is_stable_and_changes_with_prompt():
    h1 = rules.rules_sha256()
    assert isinstance(h1, str) and len(h1) == 64  # sha256 hex
    # identical call → identical hash
    assert rules.rules_sha256() == h1


def test_compress_prompt_contains_original_and_rules():
    p = rules.build_compress_prompt("hello world")
    assert "hello world" in p
    assert "code block" in p.lower()  # the preserve-rules are present


def test_strip_wrapper_removes_outer_fence_only():
    wrapped = "```markdown\nbody `inline` here\n```"
    assert rules.strip_llm_wrapper(wrapped) == "body `inline` here"
    assert rules.strip_llm_wrapper("no fence") == "no fence"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/compress/test_rules.py -q`
Expected: FAIL — `ModuleNotFoundError: harness.compress`.

- [ ] **Step 3: Write minimal implementation**

Create empty `harness/compress/__init__.py`. Then `harness/compress/rules.py` (vendored from `/Users/alberto/.agents/skills/caveman-compress/scripts/compress.py` lines 15-25, 65-110, plus a version hash):

```python
import hashlib
import re

RULES_VERSION = "1"  # bump when the prompts below change in spirit

_OUTER_FENCE_RE = re.compile(r"\A\s*(`{3,}|~{3,})[^\n]*\n(.*)\n\1\s*\Z", re.DOTALL)


def strip_llm_wrapper(text: str) -> str:
    m = _OUTER_FENCE_RE.match(text)
    return m.group(2) if m else text


def build_compress_prompt(original: str) -> str:
    return f"""Compress this markdown into caveman format.

STRICT RULES:
- Do NOT modify anything inside ``` code blocks
- Do NOT modify anything inside inline backticks
- Preserve ALL URLs exactly
- Preserve ALL headings exactly
- Preserve file paths and commands
- Return ONLY the compressed markdown body — no outer fence.

Only compress natural language.

TEXT:
{original}
"""


def build_fix_prompt(original: str, compressed: str, errors: list[str]) -> str:
    errors_str = "\n".join(f"- {e}" for e in errors)
    return f"""Fix this caveman-compressed markdown. Only fix the listed errors.

CRITICAL RULES:
- DO NOT recompress or rephrase
- ONLY fix the listed errors — leave everything else exactly as-is

ERRORS TO FIX:
{errors_str}

ORIGINAL (reference only):
{original}

COMPRESSED (fix this):
{compressed}

Return ONLY the fixed compressed file. No explanation.
"""


def rules_sha256() -> str:
    # Hash the rule-bearing strings so any edit invalidates siblings.
    material = "\x00".join([
        RULES_VERSION,
        build_compress_prompt("\x01"),
        build_fix_prompt("\x01", "\x02", ["\x03"]),
    ])
    return hashlib.sha256(material.encode()).hexdigest()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/compress/test_rules.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add harness/compress/__init__.py harness/compress/rules.py tests/compress/test_rules.py
git commit -m "feat(compress): vendored caveman style rules + versioned rules hash"
```

---

## Task 2: Validator (vendored, pure)

**Files:**
- Create: `harness/compress/validate.py`
- Test: `tests/compress/test_validate.py`

**Interfaces:**
- Produces: `validate(original: str, compressed: str) -> ValidationResult` where `ValidationResult` has `.is_valid: bool` and `.errors: list[str]`. Note: takes **strings**, not file paths (the vendored original took paths — we change the signature so it is pure and testable without disk).

- [ ] **Step 1: Write the failing test**

```python
# tests/compress/test_validate.py
from harness.compress.validate import validate


def test_valid_when_urls_and_code_preserved():
    original = "See https://x.io and `code` and\n```\nblock\n```\n"
    compressed = "see https://x.io `code`\n```\nblock\n```\n"
    assert validate(original, compressed).is_valid


def test_invalid_when_url_dropped():
    original = "Read https://important.example/page now"
    compressed = "read now"
    r = validate(original, compressed)
    assert not r.is_valid
    assert any("http" in e.lower() or "url" in e.lower() for e in r.errors)


def test_invalid_when_code_block_changed():
    original = "```\nkeep me exactly\n```"
    compressed = "```\nkeep ME exactly\n```"
    assert not validate(original, compressed).is_valid
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/compress/test_validate.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

Port the checks from `/Users/alberto/.agents/skills/caveman-compress/scripts/validate.py`, converting path inputs to string inputs. Implement at least: URL preservation, fenced-code-block exact preservation, heading preservation.

```python
import re
from dataclasses import dataclass, field

_URL_RE = re.compile(r"https?://[^\s)\]]+")
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_HEADING_RE = re.compile(r"^#{1,6} .+$", re.MULTILINE)


@dataclass
class ValidationResult:
    is_valid: bool
    errors: list[str] = field(default_factory=list)


def _missing(items_a, items_b, label):
    errs = []
    for it in items_a:
        if it not in items_b:
            errs.append(f"{label} not preserved: {it[:60]}")
    return errs


def validate(original: str, compressed: str) -> ValidationResult:
    errors: list[str] = []
    errors += _missing(_URL_RE.findall(original), _URL_RE.findall(compressed), "URL")
    errors += _missing(_FENCE_RE.findall(original), _FENCE_RE.findall(compressed), "code block")
    errors += _missing(_HEADING_RE.findall(original), _HEADING_RE.findall(compressed), "heading")
    return ValidationResult(is_valid=not errors, errors=errors)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/compress/test_validate.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add harness/compress/validate.py tests/compress/test_validate.py
git commit -m "feat(compress): pure string-based validator (exact-preservation)"
```

---

## Task 3: Engine — compress against the harness model layer

**Files:**
- Create: `harness/compress/engine.py`
- Test: `tests/compress/test_engine.py`

**Interfaces:**
- Consumes: `rules.build_compress_prompt/build_fix_prompt/strip_llm_wrapper` (Task 1), `validate.validate` (Task 2).
- Produces: `compress_text(original: str, *, call_model) -> str`. `call_model` is a callable `(prompt: str) -> str` injected by the caller — this is how we route through the harness model layer AND mock in tests. Raises `CompressionError` if it cannot produce valid output after 2 retries.
- Produces: `CompressionError(Exception)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/compress/test_engine.py
import pytest
from harness.compress.engine import compress_text, CompressionError


def test_compress_text_returns_valid_compression():
    original = "You should really make sure to read https://x.io now."

    def fake_model(prompt: str) -> str:
        # mock model returns a terse version that preserves the URL
        return "read https://x.io now"

    out = compress_text(original, call_model=fake_model)
    assert "https://x.io" in out
    assert len(out) < len(original)


def test_compress_text_raises_after_retries_when_model_keeps_dropping_url():
    original = "Keep https://must-stay.example here."

    def bad_model(prompt: str) -> str:
        return "dropped everything"  # never preserves the URL

    with pytest.raises(CompressionError):
        compress_text(original, call_model=bad_model)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/compress/test_engine.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
from harness.compress import rules
from harness.compress.validate import validate

MAX_RETRIES = 2


class CompressionError(Exception):
    pass


def compress_text(original: str, *, call_model) -> str:
    compressed = rules.strip_llm_wrapper(call_model(rules.build_compress_prompt(original)))
    for attempt in range(MAX_RETRIES):
        result = validate(original, compressed)
        if result.is_valid:
            return compressed
        if attempt == MAX_RETRIES - 1:
            raise CompressionError(f"invalid after {MAX_RETRIES} retries: {result.errors}")
        compressed = rules.strip_llm_wrapper(
            call_model(rules.build_fix_prompt(original, compressed, result.errors))
        )
    return compressed  # unreachable; loop returns or raises
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/compress/test_engine.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add harness/compress/engine.py tests/compress/test_engine.py
git commit -m "feat(compress): engine with injectable call_model (compress+validate+fix-retry)"
```

---

## Task 4: Sibling I/O — header, 3-part freshness, atomic write, path safety

**Files:**
- Create: `harness/compress/sibling.py`
- Test: `tests/compress/test_sibling.py`

**Interfaces:**
- Consumes: `rules.RULES_VERSION`, `rules.rules_sha256()` (Task 1).
- Produces:
  - `sibling_path(source: Path) -> Path` — canonical `<source>.compressed.md` (e.g. `AGENTS.md` → `AGENTS.compressed.md`; uses `.with_suffix` carefully so `MEMORY.md` → `MEMORY.compressed.md`).
  - `sha256_text(s: str) -> str`.
  - `build_header(*, source_sha: str, body_sha: str, date: str) -> str` — HTML-comment header block.
  - `parse_header(text: str) -> dict | None` — returns fields or None if no/corrupt header.
  - `split_header(text: str) -> tuple[str, str]` — (header_block, body).
  - `freshness(source_text: str, sibling_text: str) -> str` — returns `"fresh" | "stale" | "corrupt"`.
  - `write_sibling(source: Path, body: str, *, today: str) -> Path` — atomic temp+fsync+rename.
  - `is_safe_sibling(source: Path, sib: Path) -> bool` — same trusted root, not a symlink, source exists.

- [ ] **Step 1: Write the failing test**

```python
# tests/compress/test_sibling.py
from pathlib import Path
from harness.compress import sibling, rules


def test_sibling_path_derivation():
    assert sibling.sibling_path(Path("/x/AGENTS.md")) == Path("/x/AGENTS.compressed.md")
    assert sibling.sibling_path(Path("/x/MEMORY.md")) == Path("/x/MEMORY.compressed.md")


def _fresh_sibling_text(source_text: str) -> str:
    body = "compressed body"
    header = sibling.build_header(
        source_sha=sibling.sha256_text(source_text),
        body_sha=sibling.sha256_text(body),
        date="2026-06-29",
    )
    return header + body


def test_freshness_all_match_is_fresh():
    src = "original source"
    assert sibling.freshness(src, _fresh_sibling_text(src)) == "fresh"


def test_freshness_stale_when_source_changed():
    sib = _fresh_sibling_text("old source")
    assert sibling.freshness("new source", sib) == "stale"


def test_freshness_stale_when_body_hand_edited():
    src = "original source"
    sib = _fresh_sibling_text(src).replace("compressed body", "TAMPERED body")
    assert sibling.freshness(src, sib) == "stale"


def test_freshness_corrupt_when_no_header():
    assert sibling.freshness("src", "just a body, no header") == "corrupt"


def test_write_sibling_is_atomic_and_roundtrips(tmp_path):
    source = tmp_path / "AGENTS.md"
    source.write_text("hello source")
    p = sibling.write_sibling(source, "compressed body", today="2026-06-29")
    assert p == tmp_path / "AGENTS.compressed.md"
    assert sibling.freshness(source.read_text(), p.read_text()) == "fresh"


def test_is_safe_sibling_rejects_symlink(tmp_path):
    source = tmp_path / "AGENTS.md"
    source.write_text("x")
    sib = tmp_path / "AGENTS.compressed.md"
    target = tmp_path / "evil.md"
    target.write_text("evil")
    sib.symlink_to(target)
    assert sibling.is_safe_sibling(source, sib) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/compress/test_sibling.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
import hashlib
import os
import re
from pathlib import Path

from harness.compress import rules

_FIELD_RE = re.compile(r"<!--\s*compress-aware\s*(.*?)-->", re.DOTALL)
_KV_RE = re.compile(r"(\S+):\s*(\S+)")


def sibling_path(source: Path) -> Path:
    return source.with_name(source.stem + ".compressed.md")


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def build_header(*, source_sha: str, body_sha: str, date: str) -> str:
    return (
        "<!-- compress-aware "
        f"source-sha256:{source_sha} "
        f"engine-version:{rules.RULES_VERSION} "
        f"rules-sha256:{rules.rules_sha256()} "
        f"body-sha256:{body_sha} "
        f"built:{date} "
        "notice:generated-by-Done-do-not-edit "
        "-->\n"
    )


def parse_header(text: str) -> dict | None:
    m = _FIELD_RE.search(text)
    if not m:
        return None
    fields = dict(_KV_RE.findall(m.group(1)))
    required = {"source-sha256", "engine-version", "rules-sha256", "body-sha256"}
    if not required.issubset(fields):
        return None
    return fields


def split_header(text: str) -> tuple[str, str]:
    m = _FIELD_RE.search(text)
    if not m:
        return "", text
    end = text.index("-->", m.start()) + len("-->")
    # consume one trailing newline
    body = text[end:]
    if body.startswith("\n"):
        body = body[1:]
    return text[:end], body


def freshness(source_text: str, sibling_text: str) -> str:
    fields = parse_header(sibling_text)
    if fields is None:
        return "corrupt"
    _, body = split_header(sibling_text)
    if fields["source-sha256"] != sha256_text(source_text):
        return "stale"
    if fields["engine-version"] != rules.RULES_VERSION:
        return "stale"
    if fields["rules-sha256"] != rules.rules_sha256():
        return "stale"
    if fields["body-sha256"] != sha256_text(body):
        return "stale"
    return "fresh"


def is_safe_sibling(source: Path, sib: Path) -> bool:
    if not source.exists():
        return False
    if sib.is_symlink():
        return False
    try:
        return sib.resolve().parent == source.resolve().parent
    except OSError:
        return False


def write_sibling(source: Path, body: str, *, today: str) -> Path:
    src_text = source.read_text(errors="ignore")
    header = build_header(
        source_sha=sha256_text(src_text),
        body_sha=sha256_text(body),
        date=today,
    )
    out = header + body
    sib = sibling_path(source)
    tmp = sib.with_suffix(sib.suffix + ".tmp")
    with open(tmp, "w") as f:
        f.write(out)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, sib)
    return sib
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/compress/test_sibling.py -q`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add harness/compress/sibling.py tests/compress/test_sibling.py
git commit -m "feat(compress): sibling I/O — 3-part freshness, atomic write, path safety"
```

---

## Task 5: Loader — read-side swap (no LLM)

**Files:**
- Create: `harness/compress/loader.py`
- Test: `tests/compress/test_loader.py`

**Interfaces:**
- Consumes: `sibling.sibling_path/freshness/split_header/is_safe_sibling` (Task 4).
- Produces: `load_context_file(source: Path, *, mode_on: bool) -> str` — returns the compressed **body** (header stripped) when `mode_on` and a safe, fresh sibling exists; otherwise the original source text. Pure file I/O, no LLM, never raises on a missing/corrupt sibling (degrades to original).

- [ ] **Step 1: Write the failing test**

```python
# tests/compress/test_loader.py
from harness.compress import loader, sibling


def _write_fresh(source, body):
    sibling.write_sibling(source, body, today="2026-06-29")


def test_loads_original_when_mode_off(tmp_path):
    src = tmp_path / "AGENTS.md"
    src.write_text("ORIGINAL")
    _write_fresh(src, "compressed")
    assert loader.load_context_file(src, mode_on=False) == "ORIGINAL"


def test_loads_compressed_body_when_fresh_and_on(tmp_path):
    src = tmp_path / "AGENTS.md"
    src.write_text("ORIGINAL")
    _write_fresh(src, "compressed body")
    out = loader.load_context_file(src, mode_on=True)
    assert out == "compressed body"
    assert "compress-aware" not in out  # header stripped


def test_loads_original_when_sibling_missing(tmp_path):
    src = tmp_path / "AGENTS.md"
    src.write_text("ORIGINAL")
    assert loader.load_context_file(src, mode_on=True) == "ORIGINAL"


def test_loads_original_when_stale(tmp_path):
    src = tmp_path / "AGENTS.md"
    src.write_text("ORIGINAL")
    _write_fresh(src, "compressed body")
    src.write_text("CHANGED")  # now stale
    assert loader.load_context_file(src, mode_on=True) == "CHANGED"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/compress/test_loader.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
from pathlib import Path

from harness.compress import sibling


def load_context_file(source: Path, *, mode_on: bool) -> str:
    source = Path(source)
    original = source.read_text(errors="ignore")
    if not mode_on:
        return original
    sib = sibling.sibling_path(source)
    if not sib.exists() or not sibling.is_safe_sibling(source, sib):
        return original
    sib_text = sib.read_text(errors="ignore")
    if sibling.freshness(original, sib_text) != "fresh":
        return original
    _, body = sibling.split_header(sib_text)
    return body
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/compress/test_loader.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add harness/compress/loader.py tests/compress/test_loader.py
git commit -m "feat(compress): read-side loader (fresh sibling vs original, header stripped, no LLM)"
```

---

## Task 6: `compress_aware` config flag

**Files:**
- Modify: `harness/config.py` (add reader + writer near `yolo_pinned` at line 188 / `save_agent` at 177)
- Test: `tests/test_config.py` (append)

**Interfaces:**
- Consumes: existing `config.load()`, `config.AgentConfig`, `config.update_agent` patterns.
- Produces: `config.compress_aware_pinned(persona_id: str = "default") -> bool` (default **True** when unset — opinionated default); `config.set_compress_aware(persona_id: str, on: bool) -> None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py  (append)
from harness import config


def test_compress_aware_defaults_on_when_unset(tmp_path, monkeypatch):
    monkeypatch.setattr(config.paths, "config_dir", lambda: tmp_path)
    assert config.compress_aware_pinned("default") is True


def test_compress_aware_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(config.paths, "config_dir", lambda: tmp_path)
    config.set_compress_aware("default", False)
    assert config.compress_aware_pinned("default") is False
    config.set_compress_aware("default", True)
    assert config.compress_aware_pinned("default") is True
```

> Note: confirm the existing `test_config.py` fixture for redirecting `config_dir`; reuse it rather than the monkeypatch above if one exists (check `tests/test_config.py` setup).

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_config.py -q -k compress_aware`
Expected: FAIL — `AttributeError: module 'harness.config' has no attribute 'compress_aware_pinned'`.

- [ ] **Step 3: Write minimal implementation**

In `harness/config.py`, mirror the `yolo_pinned` reader and the `update_agent` writer. Add a `compress_aware: bool = True` field to `AgentConfig` (default True), and:

```python
def compress_aware_pinned(persona_id: str = "default") -> bool:
    cfg = load().get(persona_id)
    if cfg is None:
        return True  # opinionated default ON
    return cfg.compress_aware


def set_compress_aware(persona_id: str, on: bool) -> None:
    # follow the same read-modify-write-TOML path as update_agent / save_agent,
    # preserving other fields; persist compress_aware=on for persona_id.
    ...  # implement using the existing _write/update_agent helpers in this module
```

Implement `set_compress_aware` using whatever low-level TOML upsert `save_agent`/`update_agent` already use (read it at `config.py:177`), preserving `backend/model/name/yolo_pinned`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_config.py -q`
Expected: PASS (existing + 2 new).

- [ ] **Step 5: Commit**

```bash
git add harness/config.py tests/test_config.py
git commit -m "feat(config): compress_aware pinned flag (default ON)"
```

---

## Task 7: `dn compress` CLI (rebuild + --status)

**Files:**
- Create: `harness/compress_cli.py`
- Modify: `harness/tui_main.py` (add `compress` interception next to the `cron` branch)
- Test: `tests/compress/test_cli.py`

**Interfaces:**
- Consumes: `engine.compress_text` (Task 3), `sibling.*` (Task 4), `loader`/`freshness` for status, a model factory.
- Produces: `compress_cli.run(argv: list[str]) -> int`; helper `rebuild_one(source: Path, *, call_model, today: str) -> str` returning `"built" | "skipped" | "failed"`; helper `status_line(source: Path) -> str`.
- Model wiring: `compress_cli` builds `call_model` from the harness model layer — when `VIBEPROXY_MODEL` unset, use `harness.models_mock.build_mock_model()`; else `harness.vibeproxy`. Expose `call_model` injection so tests pass a fake.

- [ ] **Step 1: Write the failing test**

```python
# tests/compress/test_cli.py
from pathlib import Path
from harness import compress_cli
from harness.compress import sibling


def test_rebuild_one_writes_fresh_sibling(tmp_path):
    src = tmp_path / "AGENTS.md"
    src.write_text("You should really read https://x.io now")
    out = compress_cli.rebuild_one(
        src, call_model=lambda p: "read https://x.io now", today="2026-06-29"
    )
    assert out == "built"
    sib = sibling.sibling_path(src)
    assert sibling.freshness(src.read_text(), sib.read_text()) == "fresh"


def test_status_line_reports_stale(tmp_path):
    src = tmp_path / "AGENTS.md"
    src.write_text("original")
    compress_cli.rebuild_one(src, call_model=lambda p: "orig", today="2026-06-29")
    src.write_text("changed now")
    line = compress_cli.status_line(src)
    assert "stale" in line.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/compress/test_cli.py -q`
Expected: FAIL — `ModuleNotFoundError: harness.compress_cli`.

- [ ] **Step 3: Write minimal implementation**

```python
# harness/compress_cli.py
import argparse
from pathlib import Path

from harness.compress import engine, loader, sibling


def rebuild_one(source: Path, *, call_model, today: str) -> str:
    source = Path(source)
    original = source.read_text(errors="ignore")
    try:
        body = engine.compress_text(original, call_model=call_model)
    except engine.CompressionError:
        return "failed"
    sibling.write_sibling(source, body, today=today)
    return "built"


def status_line(source: Path) -> str:
    source = Path(source)
    sib = sibling.sibling_path(source)
    if not sib.exists():
        return f"{source.name}: no sibling"
    verdict = sibling.freshness(source.read_text(errors="ignore"), sib.read_text(errors="ignore"))
    src_n = len(source.read_text(errors="ignore"))
    _, body = sibling.split_header(sib.read_text(errors="ignore"))
    pct = 100 - round(100 * len(body) / max(src_n, 1))
    return f"{source.name}: {verdict} ({src_n}->{len(body)} chars, -{pct}%)"


def _build_call_model():
    import os
    if not os.environ.get("VIBEPROXY_MODEL"):
        from harness.models_mock import build_mock_model
        m = build_mock_model()
        return lambda prompt: m  # adapt to model_mock's call shape (see note)
    from harness import vibeproxy  # build a real one-shot completion
    ...  # one-shot completion via the harness model layer


def run(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="dn compress")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("paths", nargs="*")
    ns = ap.parse_args(argv)
    targets = [Path(p) for p in ns.paths] if ns.paths else _default_targets()
    if ns.status:
        for t in targets:
            print(status_line(t))
        return 0
    call_model = _build_call_model()
    import datetime  # NOTE: date is fine in CLI (not a workflow script)
    today = datetime.date.today().isoformat()
    for t in targets:
        print(f"{t.name}: {rebuild_one(t, call_model=call_model, today=today)}")
    return 0


def _default_targets() -> list[Path]:
    # resolve SOUL/IDENTITY/USER/MEMORY in persona workspace(s) + AGENTS/CLAUDE in cwd
    ...  # see Open question: per-persona walk (settle here)
```

> **Implementer note (model adapter):** `_build_call_model` must return a `(prompt) -> str` that performs ONE completion through the harness model layer. Read `harness/agent_build.py:18` + `harness/streaming_model.py` for the model object and how to do a single non-streaming completion; `harness/models_mock.py` for the mock's interface. Keep the adapter tiny.

Then in `harness/tui_main.py`, next to the existing `cron` interception:

```python
if raw and raw[0] == "compress":
    from harness import compress_cli
    return compress_cli.run(raw[1:])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/compress/test_cli.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add harness/compress_cli.py harness/tui_main.py tests/compress/test_cli.py
git commit -m "feat(compress): dn compress CLI (rebuild + --status)"
```

---

## Task 8: Wire the loader into persona / memory / agents reads

**Files:**
- Modify: `harness/persona.py` (`compose_persona` at line 243 — read SOUL/IDENTITY/USER via loader)
- Modify: `harness/agents.py` (`_read_tier` at line 33 — read each tier AGENTS.md via loader)
- Modify: `harness/memory.py` (`resolve_memory`/`compose_memory` — read MEMORY.md via loader)
- Test: `tests/compress/test_wiring.py`

**Interfaces:**
- Consumes: `loader.load_context_file` (Task 5), `config.compress_aware_pinned` (Task 6).
- The mode flag is resolved ONCE at each read chokepoint via `config.compress_aware_pinned(persona_id)`. (Live chip override from the TUI is passed down in Task 9; for Phase-1 wiring, read the pinned flag.)

- [ ] **Step 1: Write the failing test**

```python
# tests/compress/test_wiring.py
from pathlib import Path
from harness import persona
from harness.compress import sibling


def test_compose_persona_uses_fresh_sibling_when_on(tmp_path, monkeypatch):
    ws = tmp_path
    (ws / "SOUL.md").write_text("I am a verbose detailed soul with much prose.")
    sibling.write_sibling(ws / "SOUL.md", "terse soul", today="2026-06-29")
    monkeypatch.setattr(persona, "_compress_on", lambda *_a, **_k: True, raising=False)
    load = persona.compose_persona(ws)
    assert "terse soul" in load.block


def test_compose_persona_uses_original_when_off(tmp_path, monkeypatch):
    ws = tmp_path
    (ws / "SOUL.md").write_text("verbose soul")
    sibling.write_sibling(ws / "SOUL.md", "terse soul", today="2026-06-29")
    monkeypatch.setattr(persona, "_compress_on", lambda *_a, **_k: False, raising=False)
    load = persona.compose_persona(ws)
    assert "verbose soul" in load.block
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/compress/test_wiring.py -q`
Expected: FAIL — `compose_persona` still reads files directly; sibling ignored.

- [ ] **Step 3: Write minimal implementation**

In `harness/persona.py`, add a small helper and route the trio reads through it:

```python
from harness.compress import loader as _compress_loader
from harness import config as _config


def _compress_on(workspace_dir) -> bool:
    persona_id = workspace_dir.name if workspace_dir else "default"
    return _config.compress_aware_pinned(persona_id)


# inside compose_persona, replace direct `(workspace_dir / name).read_text()`
# with:
#     text = _compress_loader.load_context_file(workspace_dir / name,
#                                               mode_on=_compress_on(workspace_dir))
```

Apply the same pattern in `harness/agents.py:_read_tier` (mode resolved from the persona dir / default) and `harness/memory.py` MEMORY.md read. Keep each edit minimal — only the read call changes; the compose/format logic stays.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/compress/test_wiring.py tests/test_persona.py tests/test_agents.py tests/test_memory.py -q`
Expected: PASS (new + existing unaffected).

- [ ] **Step 5: Commit**

```bash
git add harness/persona.py harness/agents.py harness/memory.py tests/compress/test_wiring.py
git commit -m "feat(compress): route persona/memory/agents reads through the loader"
```

---

## Task 9: Compress-aware footer chip (live-vs-pin)

**Files:**
- Modify: `harness/tui/widgets/status_chip.py` (add `for_compress_aware(active, pinned)` near `for_yolo` at line 55)
- Modify: `harness/tui/app.py` (init `_compress_aware`/`_compress_aware_pinned` near line 113; render near 413; refresh like `_refresh_yolo_chip` at 477; pin gesture like `action_yolo_pin` at 496)
- Test: `tests/test_status_chip.py` (or wherever `for_yolo` is tested — grep)

**Interfaces:**
- Consumes: `config.compress_aware_pinned` / `config.set_compress_aware` (Task 6), the `StatusChip` pattern.
- Produces: `StatusChip.for_compress_aware(active: bool, pinned: bool) -> StatusChip`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_status_chip.py  (mirror the existing for_yolo test)
from harness.tui.widgets.status_chip import StatusChip


def test_compress_aware_chip_states():
    off = StatusChip.for_compress_aware(active=False, pinned=False)
    assert "compress" in off.render_label().lower()  # match the chip's accessor
    on_pinned = StatusChip.for_compress_aware(active=True, pinned=True)
    assert "pinned" in on_pinned.render_label().lower()
```

> Grep `tests/` for the existing `for_yolo` test to copy its exact assertion accessor (e.g. `.label`, `.render()`), then mirror it.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_status_chip.py -q -k compress_aware`
Expected: FAIL — no `for_compress_aware`.

- [ ] **Step 3: Write minimal implementation**

Mirror `StatusChip.for_yolo` (status_chip.py:55): label "context compression on/off" + " · pinned" when pinned. Then in `app.py`: initialize `self._compress_aware = _config.compress_aware_pinned(self._launch_persona)`, render the chip, add `_refresh_compress_aware_chip`, and a pin gesture calling `_config.set_compress_aware`. **Honor the YOLO contract: a click toggles live only; pin is a separate deliberate gesture** (memory: `yolo-persist-chip-merged`, "click never persists").

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_status_chip.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/tui/widgets/status_chip.py harness/tui/app.py tests/test_status_chip.py
git commit -m "feat(tui): compress-aware footer chip (live-vs-pin)"
```

---

## Task 10: Destructive memory compress-at-write

**Files:**
- Modify: `harness/memory.py` (add a compress-on-write helper used by the memory write path)
- Test: `tests/compress/test_memory_write.py`

**Interfaces:**
- Consumes: `engine.compress_text` (Task 3), `config.compress_aware_pinned` (Task 6).
- Produces: `memory.compress_on_write(path: Path, text: str, *, call_model) -> None` — writes the **compressed** form to `path` when compress-aware is ON; on `CompressionError`, **falls back to writing the verbose `text`** (no content loss on failure). When mode OFF, writes `text` verbatim.

- [ ] **Step 1: Write the failing test**

```python
# tests/compress/test_memory_write.py
from pathlib import Path
from harness import memory


def test_compress_on_write_persists_compressed_when_on(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "_compress_on", lambda *_a, **_k: True, raising=False)
    p = tmp_path / "MEMORY.md"
    memory.compress_on_write(p, "verbose https://x.io fact", call_model=lambda _p: "terse https://x.io")
    assert p.read_text() == "terse https://x.io"


def test_compress_on_write_falls_back_to_verbose_on_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "_compress_on", lambda *_a, **_k: True, raising=False)
    p = tmp_path / "MEMORY.md"
    # model drops the URL -> CompressionError -> fallback to verbose
    memory.compress_on_write(p, "keep https://x.io", call_model=lambda _p: "dropped")
    assert "https://x.io" in p.read_text()


def test_compress_on_write_verbatim_when_off(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "_compress_on", lambda *_a, **_k: False, raising=False)
    p = tmp_path / "MEMORY.md"
    memory.compress_on_write(p, "verbose", call_model=lambda _p: "terse")
    assert p.read_text() == "verbose"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/compress/test_memory_write.py -q`
Expected: FAIL — no `compress_on_write`.

- [ ] **Step 3: Write minimal implementation**

```python
# harness/memory.py
from pathlib import Path
from harness.compress import engine
from harness import config as _config


def _compress_on(path: Path) -> bool:
    return _config.compress_aware_pinned("default")


def compress_on_write(path: Path, text: str, *, call_model) -> None:
    path = Path(path)
    if not _compress_on(path):
        path.write_text(text)
        return
    try:
        out = engine.compress_text(text, call_model=call_model)
    except engine.CompressionError:
        out = text  # fallback: never lose content on failure
    path.write_text(out)
```

> **Implementer note:** wire `compress_on_write` into the actual memory-write site only if one exists as a single chokepoint. Per the integration map, memory is written via the generic Write/Edit tools (no dedicated save function). So Phase-1 ships `compress_on_write` as the **sanctioned helper**; routing the agent's memory writes through it is a follow-up (the agent must call it). Document this in the issue. Do NOT silently intercept all Write-tool calls.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/compress/test_memory_write.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add harness/memory.py tests/compress/test_memory_write.py
git commit -m "feat(compress): destructive memory compress-at-write with verbose fallback"
```

---

## Task 11: Full suite + conftest for the new test package

**Files:**
- Create: `tests/compress/__init__.py` (if the suite needs it — check existing `tests/` layout)
- Test: run everything

- [ ] **Step 1: Ensure the new test dir is collected**

If `tests/` uses packages, add `tests/compress/__init__.py`. Otherwise rely on pytest rootdir collection (check how `tests/jobs/` is structured — mirror it).

- [ ] **Step 2: Run the full suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS — all new compress tests + no regressions in existing suites.

- [ ] **Step 3: Typecheck (if the repo runs one)**

Check for a typecheck command (e.g. `mypy`, `pyright`) in `pyproject`/CI; run it on `harness/compress/`. If none exists, skip.

- [ ] **Step 4: Commit**

```bash
git add tests/compress/__init__.py
git commit -m "test(compress): collect compress test package; full suite green"
```

---

## Self-Review

**Spec coverage:**
- Shadow-file pattern + 3-part freshness → Tasks 4, 5. ✓
- Vendored engine on harness model layer → Tasks 1-3, 7 (model adapter). ✓
- Header stripped before prompt → Task 5 (loader). ✓
- Atomic writes + path safety → Task 4. ✓
- `dn compress` + `--status` → Task 7. ✓
- `compress_aware` flag default ON + YOLO-style chip → Tasks 6, 9. ✓
- Loader wired into all read chokepoints → Task 8. ✓
- Destructive memory write + verbose fallback → Task 10. ✓
- Voice-file stricter profile + pre-ship regression prompts → **NOT a separate task.** The stricter profile is an engine-prompt refinement; Phase-1 ships the single profile. Flagged as an open implementation question (profile selection) — acceptable for Phase 1, but note it in the issue so voice files use the standard profile until the stricter one lands. **Gap acknowledged, deferred deliberately.**
- Comparison tool, SKILL.md bodies, sub-agent caveman returns → Phase 2/3, out of scope. ✓
- Skill cleanup → post-implementation, out of plan. ✓

**Placeholder scan:** Two intentional `...` exist — `config.set_compress_aware` (Task 6 Step 3) and `compress_cli._build_call_model`/`_default_targets` (Task 7 Step 3). These are NOT lazy placeholders: each has an explicit implementer note pointing at the exact existing function to mirror (`save_agent`/`update_agent` for config; `agent_build.py`+`models_mock` for the model adapter). They depend on private module internals an implementer must read in-place. Every other step has complete code.

**Type consistency:** `call_model: (str) -> str` is consistent across engine (Task 3), CLI (Task 7), memory (Task 10). `freshness() -> "fresh"|"stale"|"corrupt"` consistent across sibling (4), loader (5), CLI (7). `load_context_file(source, *, mode_on)` consistent across loader (5) and wiring (8). `sibling_path/write_sibling/split_header` names consistent throughout.

**Known deferrals (call out in the issue):** (a) per-persona target walk in `_default_targets`; (b) profile selection / stricter voice profile; (c) routing agent memory writes through `compress_on_write`; (d) live chip override threading into read chokepoints (Phase-1 reads the pinned flag).
