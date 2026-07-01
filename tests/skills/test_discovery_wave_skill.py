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
