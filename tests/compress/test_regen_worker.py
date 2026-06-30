from harness.compress import regen_worker


def test_regen_calls_rebuild_one_per_path(tmp_path):
    a = tmp_path / "A.md"; a.write_text("a", encoding="utf-8")
    b = tmp_path / "B.md"; b.write_text("b", encoding="utf-8")
    seen = []

    def fake_call_model(prompt):
        return "compressed"

    # rebuild_one is real here; it writes siblings. Just assert it ran for both.
    res = regen_worker.regen([str(a), str(b)], call_model=fake_call_model, today="2026-06-30")
    assert res["built"] == 2
    assert (tmp_path / "A.compressed.md").is_file()
    assert (tmp_path / "B.compressed.md").is_file()


def test_regen_one_failure_does_not_stop_others(tmp_path, monkeypatch):
    a = tmp_path / "A.md"; a.write_text("a", encoding="utf-8")
    b = tmp_path / "B.md"; b.write_text("b", encoding="utf-8")
    from harness import compress_cli

    calls = []

    def flaky_rebuild(source, *, call_model, today):
        calls.append(source)
        if source.name == "A.md":
            raise RuntimeError("boom")
        return "built"

    monkeypatch.setattr(compress_cli, "rebuild_one", flaky_rebuild)
    res = regen_worker.regen([str(a), str(b)], call_model=lambda p: "x", today="2026-06-30")
    assert res["failed"] == 1 and res["built"] == 1
    assert len(calls) == 2                       # both attempted


def test_main_returns_zero_when_model_unavailable(monkeypatch, tmp_path):
    from harness import compress_cli, paths
    monkeypatch.setattr(paths, "load_env", lambda cwd: None)
    monkeypatch.setattr(compress_cli, "_build_call_model", lambda: None)
    assert regen_worker.main([str(tmp_path / "X.md")]) == 0    # no model → clean exit
