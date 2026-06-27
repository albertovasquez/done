import json
from pathlib import Path

from harness.debug_trace import DebugTracer, NullTracer, make_tracer


def _lines(p: Path):
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def test_tracer_writes_source_and_monotonic_seq(tmp_path):
    t = DebugTracer.open(tmp_path)
    t.emit("dn", "tx.prompt", sid="s1", turn=1, text="hi")
    t.emit("agent", "llm.call", sid="s1", turn=1, n_calls=1)
    t.close()
    rows = _lines(tmp_path / "trace.jsonl")
    assert [r["seq"] for r in rows] == [0, 1]
    assert rows[0]["source"] == "dn"
    assert rows[0]["type"] == "tx.prompt"
    assert rows[0]["data"] == {"sid": "s1", "turn": 1, "text": "hi"}
    assert rows[1]["source"] == "agent"
    assert isinstance(rows[0]["t"], float)


def test_open_creates_missing_dir(tmp_path):
    sub = tmp_path / "runs" / "20260627-000000"
    t = DebugTracer.open(sub)
    t.emit("dn", "x")
    t.close()
    assert (sub / "trace.jsonl").exists()


def test_null_tracer_writes_nothing(tmp_path):
    t = NullTracer()
    t.emit("dn", "tx.prompt", sid="s1")   # must not raise
    t.close()
    assert not (tmp_path / "trace.jsonl").exists()


def test_make_tracer_dispatch(tmp_path):
    assert isinstance(make_tracer(False, tmp_path), NullTracer)
    assert isinstance(make_tracer(True, tmp_path), DebugTracer)
