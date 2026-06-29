import pytest
from pathlib import Path
from harness.persona import PersonaLoad, compose_persona, MAX_FILE_CHARS


@pytest.fixture
def isolated_config(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path


def _write(ws: Path, name: str, body: str):
    ws.mkdir(parents=True, exist_ok=True)
    (ws / name).write_text(body, encoding="utf-8")


def test_all_three_present_in_order(tmp_path):
    _write(tmp_path, "SOUL.md", "I am terse.")
    _write(tmp_path, "IDENTITY.md", "Name: Ada")
    _write(tmp_path, "USER.md", "User is Alberto.")
    load = compose_persona(tmp_path)
    assert load.injected == ["SOUL.md", "IDENTITY.md", "USER.md"]
    assert load.skipped == []
    # ordering: SOUL before IDENTITY before USER
    assert load.block.index("I am terse.") < load.block.index("Name: Ada") < load.block.index("User is Alberto.")
    assert "# Persona" in load.block


def test_partial_injects_present_only(tmp_path):
    _write(tmp_path, "SOUL.md", "Only soul.")
    load = compose_persona(tmp_path)
    assert load.injected == ["SOUL.md"]
    assert "Only soul." in load.block
    assert "IDENTITY.md" not in load.injected and "USER.md" not in load.injected


def test_absent_dir_is_empty_noraise(tmp_path):
    load = compose_persona(tmp_path / "does-not-exist")
    assert load == PersonaLoad()
    assert load.block == "" and load.injected == []


def test_empty_workspace_is_empty(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    load = compose_persona(tmp_path)
    assert load.block == "" and load.injected == []


def test_blank_file_skipped(tmp_path):
    _write(tmp_path, "SOUL.md", "")
    load = compose_persona(tmp_path)
    assert ("SOUL.md", "blank") in load.skipped
    assert load.injected == []
    assert load.block == ""


def test_whitespace_only_file_is_blank(tmp_path):
    _write(tmp_path, "SOUL.md", "   \n\t\n  ")
    load = compose_persona(tmp_path)
    assert ("SOUL.md", "blank") in load.skipped
    assert load.block == ""          # guards the byte-identical no-op


def test_oversized_file_trimmed(tmp_path):
    big = "x" * (MAX_FILE_CHARS + 500)
    _write(tmp_path, "SOUL.md", big)
    load = compose_persona(tmp_path)
    assert load.injected == ["SOUL.md"]
    assert "[truncated]" in load.block
    # body content capped at exactly MAX_FILE_CHARS (marker excluded, no under-trim)
    assert load.block.count("x") == MAX_FILE_CHARS


def test_non_utf8_file_skipped_not_raised(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "SOUL.md").write_bytes(b"\xff\xfe\x00bad")
    load = compose_persona(tmp_path)
    assert load.injected == []
    assert load.skipped and load.skipped[0][0] == "SOUL.md"


from harness.persona import TurnContext, compose_context, resolve_persona
from harness import skills as _skills


def _write_skill(root: Path, name: str, body: str):
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\n{body}", encoding="utf-8")


def test_compose_context_bundles_given_persona_and_skills(tmp_path):
    skroot = tmp_path / "sk"
    _write_skill(skroot, "tdd", "TDD body")
    ctx = compose_context("PERSONA TEXT", "", [skroot], ["tdd"])
    assert ctx.persona_block == "PERSONA TEXT"
    assert "TDD body" in ctx.skill_block
    assert ctx.skills.injected == ["tdd"]


def test_compose_context_empty_persona_still_resolves_skills(tmp_path):
    skroot = tmp_path / "sk"
    _write_skill(skroot, "tdd", "TDD body")
    ctx = compose_context("", "", [skroot], ["tdd"])
    assert ctx.persona_block == ""
    assert "TDD body" in ctx.skill_block


def test_compose_context_all_empty(tmp_path):
    ctx = compose_context("", "", [tmp_path], [])
    assert ctx == TurnContext()


def test_resolve_persona_reads_workspace(tmp_path):
    (tmp_path / "SOUL.md").write_text("Be terse.", encoding="utf-8")
    load = resolve_persona(tmp_path)
    assert "Be terse." in load.block
    assert load.injected == ["SOUL.md"]


def test_resolve_persona_none_is_empty():
    assert resolve_persona(None) == PersonaLoad()


def test_resolve_persona_absent_dir_is_empty(tmp_path):
    assert resolve_persona(tmp_path / "nope") == PersonaLoad()


def test_html_comment_only_file_is_blank(tmp_path):
    # a template file (only an HTML comment) must be treated as blank -> not injected
    (tmp_path / "SOUL.md").write_text(
        "<!-- SOUL.md — describe the agent's tone here. -->\n", encoding="utf-8")
    load = compose_persona(tmp_path)
    assert ("SOUL.md", "blank") in load.skipped
    assert load.injected == []
    assert load.block == ""


def test_comment_plus_real_line_injects_whole_file(tmp_path):
    # once the user adds real content, the file injects (comment included is fine)
    (tmp_path / "SOUL.md").write_text(
        "<!-- hint -->\nYou are terse.", encoding="utf-8")
    load = compose_persona(tmp_path)
    assert load.injected == ["SOUL.md"]
    assert "You are terse." in load.block


def test_markdown_heading_is_not_a_comment(tmp_path):
    # '#' is a markdown heading, NOT a comment marker — it must inject
    (tmp_path / "SOUL.md").write_text("# Persona\nBe concise.", encoding="utf-8")
    load = compose_persona(tmp_path)
    assert load.injected == ["SOUL.md"]
    assert "# Persona" in load.block


from harness.persona import seed_default_workspace
from harness import paths


def test_seed_creates_trio_when_absent(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    seed_default_workspace()
    ws = paths.default_workspace_dir()
    for name in ("SOUL.md", "IDENTITY.md", "USER.md"):
        assert (ws / name).is_file(), name
    # the default ships with a soul -> compose injects its SOUL/IDENTITY;
    # USER.md stays inert (blank) for the user to fill in. The soul names itself
    # "Bob" (deliberate voice) while IDENTITY shows the display name "Done".
    block = compose_persona(ws).block
    assert "You're Bob." in block       # soul body
    assert "Name: Done." in block       # IDENTITY display name


def test_seed_does_not_clobber_existing_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    ws = paths.default_workspace_dir()
    ws.mkdir(parents=True)
    (ws / "SOUL.md").write_text("You are terse.", encoding="utf-8")  # user content
    seed_default_workspace()                                          # must NOT overwrite
    assert (ws / "SOUL.md").read_text(encoding="utf-8") == "You are terse."
    # and it did not drop in the other templates either (dir already existed)
    assert not (ws / "IDENTITY.md").exists()


def test_seed_never_raises_on_oserror(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    def boom(*a, **k):
        raise OSError("read-only home")
    # Force the mkdir inside seed_default_workspace to fail. persona.Path IS
    # pathlib.Path; monkeypatch auto-restores after the test, so the global patch
    # is safe here. (This is intentional — do not "simplify" it away.)
    monkeypatch.setattr("harness.persona.Path.mkdir", boom)
    with caplog.at_level("WARNING", logger="harness.persona"):
        seed_default_workspace()   # must not raise — best-effort startup
    # ...but the failure must be surfaced (else "why is my persona blank?")
    assert any("could not seed default persona" in r.message for r in caplog.records), \
        f"seed failure must warn; got {[r.message for r in caplog.records]}"


def test_meaningful_and_trim_are_importable_helpers():
    # Phase B's memory module reuses these; lock them as a stable import surface.
    from harness.persona import _meaningful, _trim
    assert _meaningful("real text") is True
    assert _meaningful("<!-- only a comment -->") is False
    body, trimmed = _trim("x" * 10, 4)
    assert body == "xxxx" and trimmed is True


def test_compose_context_carries_memory_block(tmp_path):
    from harness.persona import compose_context, TurnContext
    ctx = compose_context("PERSONA", "MEMORY", [tmp_path], [])
    assert ctx.persona_block == "PERSONA"
    assert ctx.memory_block == "MEMORY"


# ---------------------------------------------------------------------------
# create_persona tests (Task 1)
# ---------------------------------------------------------------------------
from harness import persona
from harness.persona_select import InvalidPersonaId


def test_create_persona_makes_dir_and_copies_trio(isolated_config):
    ws = persona.create_persona("fred")
    assert ws == paths.config_dir() / "agents" / "fred"
    assert ws.is_dir()
    for name in persona.PERSONA_FILES:
        assert (ws / name).is_file()


def test_create_persona_copies_bytes_identical(isolated_config):
    ws = persona.create_persona("fred")
    src = paths.bundled_persona_templates_dir()
    for name in persona.PERSONA_FILES:
        assert (ws / name).read_bytes() == (src / name).read_bytes()


def test_create_persona_rejects_default(isolated_config):
    with pytest.raises(InvalidPersonaId):
        persona.create_persona("default")


@pytest.mark.parametrize("bad", ["fred.smith", "Fred", "has space", "a/b"])
def test_create_persona_rejects_bad_charset(isolated_config, bad):
    with pytest.raises(InvalidPersonaId):
        persona.create_persona(bad)


def test_create_persona_rejects_existing_dir(isolated_config):
    persona.create_persona("fred")
    with pytest.raises(persona.PersonaExists):
        persona.create_persona("fred")


def test_create_persona_rejects_existing_file_collision(isolated_config):
    target = paths.config_dir() / "agents" / "fred"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("i am a file, not a dir")
    with pytest.raises(persona.PersonaExists):
        persona.create_persona("fred")


# ---------------------------------------------------------------------------
# seed_default_workspace refactor tests (Task 1)
# ---------------------------------------------------------------------------

def test_seed_default_noop_when_exists_does_not_backfill(isolated_config):
    dest = paths.default_workspace_dir()
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "SOUL.md").write_text("user content")   # only one file present
    persona.seed_default_workspace()
    # existing dir => no-op: missing trio files are NOT backfilled
    assert (dest / "SOUL.md").read_text() == "user content"
    assert not (dest / "IDENTITY.md").exists()


def test_seed_default_seeds_done_soul_on_first_run(isolated_config):
    persona.seed_default_workspace()
    dest = paths.default_workspace_dir()
    # SOUL/IDENTITY carry the shipped "Done" persona; USER.md stays inert.
    assert (dest / "SOUL.md").read_text(encoding="utf-8") == persona.DEFAULT_PERSONA_SOUL
    assert (dest / "IDENTITY.md").read_text(encoding="utf-8") == persona.DEFAULT_PERSONA_IDENTITY
    src = paths.bundled_persona_templates_dir()
    assert (dest / "USER.md").read_bytes() == (src / "USER.md").read_bytes()
    # the display name resolves to "Done"
    from harness import persona_config
    assert persona_config.read_name(dest) == "Done"


def test_seed_default_never_raises_on_oserror(isolated_config, monkeypatch):
    monkeypatch.setattr(persona, "_copy_persona_templates",
                        lambda dest: (_ for _ in ()).throw(OSError("read-only")))
    # must NOT raise into the startup path
    persona.seed_default_workspace()


# ---------------------------------------------------------------------------
# display_name tests (Task 2)
# ---------------------------------------------------------------------------

from harness import persona_config


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
