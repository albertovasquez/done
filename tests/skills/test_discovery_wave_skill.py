import pathlib

from harness.flows import scope_catalog
from harness.skills import load_catalog


SKILLS_ROOT = pathlib.Path(__file__).resolve().parents[2] / "harness" / "skills"


def _load():
    return {m.name: m for m in load_catalog([SKILLS_ROOT])}


def test_discovery_wave_is_flow_tagged_and_model_invocable():
    meta = _load()["discovery-wave"]
    assert meta.flows == ("discovery",)
    assert meta.model_invocable is True


def test_discovery_wave_hidden_unless_discovery_flow_enabled():
    metas = list(_load().values())
    # Not in scope when a different flow is enabled...
    scoped_other = {m.name for m in scope_catalog(metas, ["seo"])}
    assert "discovery-wave" not in scoped_other
    # ...in scope when the discovery flow is enabled.
    scoped_disc = {m.name for m in scope_catalog(metas, ["discovery"])}
    assert "discovery-wave" in scoped_disc


def test_discovery_wave_protocol_clauses_present():
    # Pin the load-bearing PROTOCOL clauses in the SKILL.md body so they can't
    # silently regress. If a phrase assertion fails, the skill wording changed —
    # fix the assertion to match the actual text, do NOT weaken the skill.
    text = (SKILLS_ROOT / "discovery-wave" / "SKILL.md").read_text(encoding="utf-8")
    for phrase in (
        "Fewer downstream surprises",   # priority ranking
        "default to `unverified`",      # verify-default (default-on)
        "default-on",                   # verify-default marker
        "MUST be `verified`",           # traceability rule
        "unverified — degraded mode",   # fail-open stamp
    ):
        assert phrase in text, phrase

    # Read-only guard must appear in all three worker stages
    # (finders, verifiers, generators). The finder occurrence opens a sentence
    # ("Do NOT ..."); the other two are mid-sentence ("do NOT ..."). Count
    # case-insensitively so all three are pinned.
    guard = "do not pass a `tools` field"
    assert text.lower().count(guard) >= 3, guard
