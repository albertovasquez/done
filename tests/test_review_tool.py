from harness.tools.review import ReviewTool


class _Env:
    pass


def test_tool_runs_review_with_explicit_model(monkeypatch):
    # explicit model arg -> _build_call_model is used; stub it to avoid litellm
    import harness.tools.review as rt
    monkeypatch.setattr(rt, "_build_call_model", lambda name: (lambda p: f"[{name}] L1: nit: x."))
    tool = ReviewTool()
    out = tool.execute({"content": "- a\n+ b", "model": "sonnet"}, _Env())
    assert out["returncode"] == 0
    assert "[sonnet]" in out["output"]


def test_tool_resolves_model_from_config_when_no_arg(monkeypatch, tmp_path):
    from harness import config
    import harness.tools.review as rt
    d = tmp_path / "cfg"; d.mkdir()
    monkeypatch.setattr(config.paths, "config_dir", lambda: d)
    (d / "done.conf").write_text('schema_version = 1\n\n[harness]\nreview_model = "conf-m"\n')
    monkeypatch.setattr(rt, "_build_call_model", lambda name: (lambda p: f"[{name}] ok"))
    out = ReviewTool().execute({"content": "x"}, _Env())
    assert "[conf-m]" in out["output"]


def test_tool_no_model_returns_message_not_crash(monkeypatch, tmp_path):
    from harness import config
    d = tmp_path / "cfg"; d.mkdir()
    monkeypatch.setattr(config.paths, "config_dir", lambda: d)
    (d / "done.conf").write_text("schema_version = 1\n")
    monkeypatch.delenv("REVIEW_MODEL", raising=False)
    out = ReviewTool().execute({"content": "x"}, _Env())
    assert out["returncode"] == 1
    assert "review model" in out["output"].lower()
