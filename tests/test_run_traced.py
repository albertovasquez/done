import json
import sys
from pathlib import Path

sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

import trace.run_traced as rt


def test_4_thin_client_mock_red_green(tmp_path, monkeypatch):
    # Copy the sample repo into a temp cwd so the run can edit it freely.
    src = Path("examples/sample-repo")
    dst = tmp_path / "sample-repo"
    dst.mkdir()
    for f in ("calculator.py", "test_calculator.py"):
        (dst / f).write_text((src / f).read_text())

    rc = rt.main(["--model", "mock", "--cwd", str(dst)])
    assert rc == 0

    # The mock fix was applied (genuine red->green preserved through the runner).
    assert "return a + b" in (dst / "calculator.py").read_text()

    # The latest events.jsonl parses and has contiguous seq (proves write_event,
    # not re-emit, was used: re-emit would renumber seq from 0 with the client clock).
    runs = sorted((Path("trace") / "runs").glob("*/events.jsonl"))
    rec = [json.loads(l) for l in runs[-1].read_text().splitlines()]
    assert [r["seq"] for r in rec] == list(range(len(rec)))
    assert rec[0]["type"] == "run.started" and rec[-1]["type"] == "run.finished"
