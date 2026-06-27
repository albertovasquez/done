from harness.acp_emit import trace_event, with_meta, message_chunk


def test_trace_event_shape():
    ev = trace_event("tx.prompt", sid="s1", turn=1)
    assert ev == {"type": "tx.prompt", "data": {"sid": "s1", "turn": 1}}


def test_with_meta_carries_trace():
    upd = with_meta(message_chunk(""), {"trace": trace_event("llm.call", n_calls=1)})
    harness_meta = upd.field_meta["harness"]
    assert harness_meta["trace"]["type"] == "llm.call"
    assert harness_meta["trace"]["data"] == {"n_calls": 1}
