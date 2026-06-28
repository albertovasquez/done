"""Tests for the load-on-demand memory layer: MemoryMeta / load_manifest /
compose_memory. The startup-inject path (resolve_memory) is covered by
test_memory.py and must stay unchanged — these tests only cover the NEW recall
layer that mirrors skills.load_catalog / skills.compose."""

from pathlib import Path

from datetime import date

from harness.memory import (
    MemoryMeta,
    load_manifest,
    compose_memory,
    compose_menu,
    has_memory,
    resolve_memory,
    MAX_MEMORY_CHARS,
)

TODAY = date(2026, 6, 26)


def _fact(ws: Path, name: str, body: str, *, type_: str | None = "reference",
          description: str = "a fact") -> None:
    """Write a typed per-fact file under <ws>/memory/<name>.md."""
    p = ws / "memory" / f"{name}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    fm = [f"name: {name}", f"description: {description}"]
    if type_ is not None:
        fm.append(f"type: {type_}")
    p.write_text("---\n" + "\n".join(fm) + "\n---\n\n" + body, encoding="utf-8")


# ---------------------------------------------------------------- load_manifest

def test_manifest_empty_when_no_memory_dir(tmp_path):
    assert load_manifest(tmp_path) == []


def test_manifest_parses_typed_fact(tmp_path):
    _fact(tmp_path, "user-terse", "Prefers terse answers.",
          type_="feedback", description="no trailing summaries")
    metas = load_manifest(tmp_path)
    assert len(metas) == 1
    m = metas[0]
    assert m == MemoryMeta(name="user-terse",
                           description="no trailing summaries",
                           type="feedback")


def test_manifest_type_defaults_to_reference(tmp_path):
    _fact(tmp_path, "some-note", "body", type_=None)   # no type field
    metas = load_manifest(tmp_path)
    assert metas[0].type == "reference"


def test_manifest_unknown_type_kept_verbatim(tmp_path):
    _fact(tmp_path, "weird", "body", type_="banana")
    metas = load_manifest(tmp_path)
    # forward-compat: a bad type is not fatal; kept as-is (caller may flag it)
    assert metas[0].type == "banana"


def test_manifest_skips_blank_and_comment_only(tmp_path):
    (tmp_path / "memory").mkdir(parents=True)
    (tmp_path / "memory" / "blank.md").write_text("", encoding="utf-8")
    (tmp_path / "memory" / "comment.md").write_text("<!-- nothing -->", encoding="utf-8")
    _fact(tmp_path, "real", "real body")
    names = [m.name for m in load_manifest(tmp_path)]
    assert names == ["real"]


def test_manifest_ignores_daily_note_files(tmp_path):
    # YYYY-MM-DD.md daily notes are NOT typed facts; they must not appear as metas.
    (tmp_path / "memory").mkdir(parents=True)
    (tmp_path / "memory" / "2026-06-28.md").write_text("today note", encoding="utf-8")
    _fact(tmp_path, "pref", "a pref")
    names = [m.name for m in load_manifest(tmp_path)]
    assert names == ["pref"]


def test_manifest_never_raises_on_bad_file(tmp_path):
    (tmp_path / "memory").mkdir(parents=True)
    (tmp_path / "memory" / "bad.md").write_bytes(b"\xff\xfe\x00")   # non-utf8
    _fact(tmp_path, "ok", "ok body")
    # must not raise; the good fact still loads
    names = [m.name for m in load_manifest(tmp_path)]
    assert "ok" in names


def test_manifest_nonstring_name_coerced_to_str(tmp_path):
    # YAML parses `name: 123` as an int. Codex finding #4: the unknown-memory path
    # does ", ".join(m.name ...) -> TypeError. name/description MUST be coerced to
    # str (or the file skipped) so a turn never crashes.
    (tmp_path / "memory").mkdir(parents=True)
    (tmp_path / "memory" / "weird.md").write_text(
        "---\nname: 123\ndescription: 456\n---\nbody\n", encoding="utf-8")
    metas = load_manifest(tmp_path)
    for m in metas:
        assert isinstance(m.name, str) and isinstance(m.description, str)
    # the join (the crash site) must not raise
    ", ".join(m.name for m in metas)


# ------------------------------------------------------------------ compose_menu

def test_compose_menu_empty_for_no_facts():
    assert compose_menu([]) == ""


def test_compose_menu_lists_name_desc_type(tmp_path):
    metas = [MemoryMeta("user-terse", "no trailing summaries", "feedback")]
    menu = compose_menu(metas)
    assert "user-terse" in menu and "no trailing summaries" in menu
    assert "feedback" in menu
    # it must teach the agent the recall verb so the manifest is actionable
    assert "load_memory" in menu


# --------------------------------------------------------------------- has_memory

def test_has_memory_false_for_empty(tmp_path):
    assert has_memory(tmp_path) is False
    assert has_memory(tmp_path / "nope") is False
    assert has_memory(None) is False


def test_has_memory_true_for_memory_md(tmp_path):
    (tmp_path / "MEMORY.md").write_text("durable fact", encoding="utf-8")
    assert has_memory(tmp_path) is True


def test_has_memory_true_for_typed_fact(tmp_path):
    _fact(tmp_path, "pref", "a pref")
    assert has_memory(tmp_path) is True


def test_has_memory_false_for_comment_only(tmp_path):
    (tmp_path / "MEMORY.md").write_text("<!-- nothing -->", encoding="utf-8")
    assert has_memory(tmp_path) is False


# ----------------------------------------- resolve_memory injects the manifest menu

def test_resolve_memory_appends_typed_fact_menu(tmp_path):
    # Codex finding #2: typed facts must be DISCOVERABLE in the prompt, else the
    # agent never knows to call load_memory for them. resolve_memory's block must
    # carry a menu of typed facts even when MEMORY.md/daily notes are absent.
    _fact(tmp_path, "pr-workflow", "ship via PR", description="never main")
    load = resolve_memory(tmp_path, today=TODAY)
    assert "pr-workflow" in load.block
    assert "never main" in load.block
    assert "load_memory" in load.block


def test_resolve_memory_menu_plus_durable(tmp_path):
    (tmp_path / "MEMORY.md").write_text("Prefers terse.", encoding="utf-8")
    _fact(tmp_path, "pr-workflow", "ship via PR", description="never main")
    load = resolve_memory(tmp_path, today=TODAY)
    assert "Prefers terse." in load.block          # durable still injected
    assert "pr-workflow" in load.block             # menu still present


def test_resolve_memory_still_noop_when_truly_empty(tmp_path):
    # no MEMORY.md, no daily notes, no typed facts -> byte-identical empty block
    tmp_path.mkdir(exist_ok=True)
    load = resolve_memory(tmp_path, today=TODAY)
    assert load.block == "" and load.injected == []


# --------------------------------------------------------------- compose_memory

def test_compose_reads_named_body(tmp_path):
    _fact(tmp_path, "pr-workflow", "Ship via PR, never main.")
    load = compose_memory(tmp_path, ["pr-workflow"])
    assert load.injected == ["pr-workflow"]
    assert "Ship via PR, never main." in load.block


def test_compose_missing_is_skipped_not_raised(tmp_path):
    (tmp_path / "memory").mkdir(parents=True)
    load = compose_memory(tmp_path, ["nope"])
    assert load.injected == []
    assert load.skipped and load.skipped[0][0] == "nope"


def test_compose_multiple_names(tmp_path):
    _fact(tmp_path, "a", "AAA")
    _fact(tmp_path, "b", "BBB")
    load = compose_memory(tmp_path, ["a", "b"])
    assert set(load.injected) == {"a", "b"}
    assert "AAA" in load.block and "BBB" in load.block


def test_compose_trims_oversized_body(tmp_path):
    # _trim caps the FILE text (frontmatter + body) at MAX_MEMORY_CHARS, so the
    # surviving §-run is < MAX (frontmatter eats into the budget) but the trim
    # marker proves the cap fired and the §-count never exceeds the cap.
    _fact(tmp_path, "big", "§" * (MAX_MEMORY_CHARS + 500))
    load = compose_memory(tmp_path, ["big"])
    assert "…[truncated]…" in load.block
    assert 0 < load.block.count("§") <= MAX_MEMORY_CHARS
    # the kept §-run + the (non-§) frontmatter together hit the cap
    assert load.block.count("§") == MAX_MEMORY_CHARS - len("---\nname: big\n"
        "description: a fact\ntype: reference\n---\n\n")


def test_compose_can_load_root_memory_md(tmp_path):
    # MEMORY.md at the workspace root is loadable by the name "MEMORY".
    (tmp_path / "MEMORY.md").write_text("durable index", encoding="utf-8")
    load = compose_memory(tmp_path, ["MEMORY"])
    assert load.injected == ["MEMORY"]
    assert "durable index" in load.block


def test_compose_two_workspaces_isolated(tmp_path):
    a = tmp_path / "a"; _fact(a, "x", "A-only")
    b = tmp_path / "b"; _fact(b, "x", "B-only")
    la = compose_memory(a, ["x"])
    lb = compose_memory(b, ["x"])
    assert "A-only" in la.block and "A-only" not in lb.block
    assert "B-only" in lb.block and "B-only" not in la.block
