import json

from harness.debug_trace import DebugTracer, NullTracer
from harness.tui.app import extract_agent_trace


class _FakeUpdate:
    def __init__(self, harness_meta):
        self.field_meta = {"harness": harness_meta}


def test_relayed_agent_event_is_written(tmp_path):
    t = DebugTracer.open(tmp_path)
    upd = _FakeUpdate({"trace": {"type": "llm.call", "data": {"n_calls": 1}}})
    extract_agent_trace(t, upd)
    t.close()
    rows = [json.loads(l) for l in (tmp_path / "trace.jsonl").read_text().splitlines()]
    assert rows[0]["source"] == "agent"
    assert rows[0]["type"] == "llm.call"
    assert rows[0]["data"] == {"n_calls": 1}


def test_no_trace_payload_writes_nothing(tmp_path):
    t = DebugTracer.open(tmp_path)
    upd = _FakeUpdate({"task_classified": {"task_type": "agent"}})  # not a trace payload
    extract_agent_trace(t, upd)
    t.close()
    assert (tmp_path / "trace.jsonl").read_text() == ""


def test_null_tracer_is_safe():
    extract_agent_trace(NullTracer(), _FakeUpdate({"trace": {"type": "x", "data": {}}}))
