from pathlib import Path
from harness import paths as _paths


def cron_dir() -> Path:
    return _paths.config_dir() / "cron"


def jobs_file() -> Path:
    return cron_dir() / "jobs.json"


def runs_dir() -> Path:
    return cron_dir() / "runs"


def run_log(job_id: str) -> Path:
    return runs_dir() / f"{job_id}.jsonl"
