from harness.jobs import paths as jp


def test_paths_under_config_cron(tmp_path, monkeypatch):
    monkeypatch.setattr("harness.paths.config_dir", lambda: tmp_path)
    assert jp.cron_dir() == tmp_path / "cron"
    assert jp.jobs_file() == tmp_path / "cron" / "jobs.json"
    assert jp.runs_dir() == tmp_path / "cron" / "runs"
    assert jp.run_log("abc") == tmp_path / "cron" / "runs" / "abc.jsonl"
