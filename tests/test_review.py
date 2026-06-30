import pytest
from harness import review


def _isolate(monkeypatch, tmp_path, body=None):
    from harness import config
    d = tmp_path / "cfg"; d.mkdir(exist_ok=True)
    monkeypatch.setattr(config.paths, "config_dir", lambda: d)
    if body is not None:
        (d / "done.conf").write_text(body)


def test_resolve_prefers_done_conf_then_env_then_none(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path,
             'schema_version = 1\n\n[harness]\nreview_model = "from-conf"\n')
    monkeypatch.setenv("REVIEW_MODEL", "from-env")
    assert review.resolve_review_model(quick=False) == "from-conf"   # conf wins
    monkeypatch.delenv("REVIEW_MODEL", raising=False)
    _isolate(monkeypatch, tmp_path, 'schema_version = 1\n')          # empty conf
    monkeypatch.setenv("REVIEW_MODEL", "from-env")
    assert review.resolve_review_model(quick=False) == "from-env"    # env next
    monkeypatch.delenv("REVIEW_MODEL", raising=False)
    assert review.resolve_review_model(quick=False) is None          # then None


def test_resolve_quick_uses_quick_keys(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path,
             'schema_version = 1\n\n[harness]\nquick_review_model = "fast-m"\n')
    assert review.resolve_review_model(quick=True) == "fast-m"
    assert review.resolve_review_model(quick=False) is None          # non-quick key absent


def test_run_review_passes_prompt_and_content():
    seen = {}
    def fake_model(prompt: str) -> str:
        seen["prompt"] = prompt
        return "L42: bug: null deref. guard."
    out = review.run_review("- foo()\n+ foo(x)", quick=False, call_model=fake_model)
    assert out == "L42: bug: null deref. guard."
    assert "one line per finding" in seen["prompt"].lower()   # the caveman prompt is present
    assert "foo(x)" in seen["prompt"]                         # content is included


def test_run_review_rejects_empty_content():
    with pytest.raises(ValueError):
        review.run_review("   ", quick=False, call_model=lambda p: "x")


def test_bundled_review_skills_parse():
    from pathlib import Path
    from harness import skills
    root = Path(__file__).resolve().parent.parent / "harness" / "skills"
    for name in ("review", "quick-review"):
        data, body = skills._parse_skill_md(root / name / "SKILL.md")
        assert data.get("name") == name           # frontmatter name matches dir
        assert data.get("description")
        assert "review" in body.lower()


def test_review_skills_are_model_invocable():
    from pathlib import Path
    from harness import skills, paths
    # Load catalog from bundled skills only
    cat = skills.load_catalog_with_skips([paths.bundled_skills_dir()])
    by = {m.name: m for m in cat.skills}
    assert by["review"].model_invocable is True
    assert by["quick-review"].model_invocable is True
    assert "review" in by["review"].description.lower()
    assert "review" in by["quick-review"].description.lower()
