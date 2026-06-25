import ast
import inspect
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

    # Pin the run id so we read THIS run's artifacts, not the "latest global" dir
    # (avoids an ordering hazard if another run exists under trace/runs/).
    monkeypatch.setattr(rt, "_run_id", lambda: "pytest-thin-client")

    rc = rt.main(["--model", "mock", "--cwd", str(dst)])
    assert rc == 0

    # The mock fix was applied (genuine red->green preserved through the runner).
    assert "return a + b" in (dst / "calculator.py").read_text()

    # This run's events.jsonl parses; seq is contiguous and the runner's bookend
    # events frame the stream (run.started ... run.finished).
    events_path = rt.REPO_ROOT / "trace" / "runs" / "pytest-thin-client" / "events.jsonl"
    rec = [json.loads(l) for l in events_path.read_text().splitlines()]
    assert [r["seq"] for r in rec] == list(range(len(rec)))
    assert rec[0]["type"] == "run.started" and rec[-1]["type"] == "run.finished"


def test_4b_client_uses_runner_not_agent_directly():
    """Lock the rewire invariant: run_traced consumes the AgentRunner and does
    NOT import/drive TracingAgent. An AST import check is a zero-cost guard that
    catches any regression reintroducing the direct-agent path (the contiguous-seq
    assertion in test_4 cannot distinguish write_event from emit, since emit calls
    write_event internally)."""
    tree = ast.parse(inspect.getsource(rt))
    imported = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert "MiniSweAgentRunner" in imported, "thin client must import MiniSweAgentRunner"
    assert "TracingAgent" not in imported, "thin client must NOT import TracingAgent"
