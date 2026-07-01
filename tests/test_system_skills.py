from pathlib import Path
from harness.skills import load_catalog, compose, _parse_skill_md
from harness import paths

# imported, unchanged-methodology skills (obra/superpowers)
IMPORTED = {"test-driven-development", "systematic-debugging",
            "verification-before-completion", "receiving-code-review"}
# the curated maturity spine = imported + harness-authored adaptations
EXPECTED = IMPORTED | {"clarify-before-acting", "planning-before-coding", "ask-done",
                       "create-job", "create-persona", "review", "quick-review",
                       "developer"}  # developer: umbrella coding-discipline skill + home for coding lessons; create-*: gate-gated privileged write paths (cron job / persona)
REMOVED = {"git-pr-flow", "python-testing", "poker-domain-rules"}


def _bundled() -> Path:
    return Path(paths.bundled_skills_dir())


def test_catalog_is_exactly_the_maturity_spine():
    cat = {m.name: m.description for m in load_catalog([_bundled()])}
    assert set(cat) == EXPECTED, sorted(cat)
    for name, desc in cat.items():
        assert desc.strip(), f"{name} has empty description"


def test_ask_done_is_user_invoked_only():
    cat = {m.name: m for m in load_catalog([_bundled()])}
    assert cat["ask-done"].model_invocable is False     # router never auto-runs it
    assert cat["ask-done"].user_invocable is True        # but /ask-done works


def test_removed_placeholders_are_gone():
    cat = {m.name: m.description for m in load_catalog([_bundled()])}
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
    for name in IMPORTED:
        body = (_bundled() / name / "SKILL.md").read_text(encoding="utf-8")
        assert "superpowers:" not in body, f"{name} still has a superpowers: ref"


def test_systematic_debugging_has_observe_only_offramp():
    """The skill must NOT force a fix-workflow on read-only checks: an explicit
    precondition gates it on a *reported* failure, and the description no longer
    invites attachment to any 'unexpected behavior' (#177)."""
    from pathlib import Path
    import harness  # to locate the package root

    root = Path(harness.__file__).resolve().parent
    text = (root / "skills" / "systematic-debugging" / "SKILL.md").read_text()
    low = text.lower()

    # off-ramp precondition present in the body
    assert "reported" in low and "observe" in low
    assert "do not run the test suite" in low
    # description tightened: no longer the broad "any ... unexpected behavior" hook
    assert "unexpected behavior" not in low.split("# systematic debugging")[0]
