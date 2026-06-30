from harness.compress import regen_worker


def test_regen_calls_rebuild_one_per_path(tmp_path):
    from harness.compress import sibling
    a = tmp_path / "A.md"; a.write_text("a", encoding="utf-8")
    b = tmp_path / "B.md"; b.write_text("b", encoding="utf-8")
    # Pre-create siblings (simulates: auto-regen only receives paths whose
    # sibling already exists at discovery time).
    sibling.sibling_path(a).write_text("<!-- old -->", encoding="utf-8")
    sibling.sibling_path(b).write_text("<!-- old -->", encoding="utf-8")

    def fake_call_model(prompt):
        return "compressed"

    # rebuild_one is real here; it writes siblings. Just assert it ran for both.
    res = regen_worker.regen([str(a), str(b)], call_model=fake_call_model, today="2026-06-30")
    assert res["built"] == 2
    assert (tmp_path / "A.compressed.md").is_file()
    assert (tmp_path / "B.compressed.md").is_file()


def test_regen_one_failure_does_not_stop_others(tmp_path, monkeypatch):
    from harness.compress import sibling
    a = tmp_path / "A.md"; a.write_text("a", encoding="utf-8")
    b = tmp_path / "B.md"; b.write_text("b", encoding="utf-8")
    # Pre-create siblings so the TOCTOU guard passes for both paths.
    sibling.sibling_path(a).write_text("<!-- old -->", encoding="utf-8")
    sibling.sibling_path(b).write_text("<!-- old -->", encoding="utf-8")
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


def test_regen_skips_path_whose_sibling_disappeared(monkeypatch, tmp_path):
    """TOCTOU: if sibling is deleted between discovery and rebuild, worker must skip it."""
    from harness import compress_cli
    from harness.compress import sibling

    a = tmp_path / "A.md"; a.write_text("a", encoding="utf-8")
    sib_a = sibling.sibling_path(a)
    # sibling does NOT exist — simulates deletion after discovery
    assert not sib_a.is_file()

    b = tmp_path / "B.md"; b.write_text("b", encoding="utf-8")
    sib_b = sibling.sibling_path(b)
    sib_b.write_text("<!-- compressed -->", encoding="utf-8")  # sibling exists → should rebuild

    calls = []

    def tracking_rebuild(source, *, call_model, today):
        calls.append(source)
        return "built"

    monkeypatch.setattr(compress_cli, "rebuild_one", tracking_rebuild)
    res = regen_worker.regen([str(a), str(b)], call_model=lambda p: "x", today="2026-06-30")

    assert a not in calls, "rebuild_one must NOT be called for a path whose sibling is gone"
    assert b in calls, "rebuild_one MUST be called for a path whose sibling exists"
    assert res["skipped"] == 1
    assert res["built"] == 1


def test_main_returns_zero_when_model_unavailable(monkeypatch, tmp_path):
    from harness import compress_cli, paths
    monkeypatch.setattr(paths, "load_env", lambda cwd: None)
    monkeypatch.setattr(compress_cli, "_build_call_model", lambda: None)
    assert regen_worker.main([str(tmp_path / "X.md")]) == 0    # no model → clean exit
