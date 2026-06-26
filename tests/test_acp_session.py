import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

import pytest
from harness.acp_session import SessionStore, SessionState


def test_new_and_get_roundtrip(tmp_path):
    store = SessionStore()
    sid = store.new(cwd=str(tmp_path))
    st = store.get(sid)
    assert isinstance(st, SessionState)
    assert st.cwd == str(tmp_path)
    assert st.history == []
    assert not st.cancel_flag.is_set()


def test_unknown_session_raises():
    with pytest.raises(KeyError):
        SessionStore().get("nope")


def test_record_appends_history(tmp_path):
    store = SessionStore(); sid = store.new(cwd=str(tmp_path))
    store.record(sid, {"prompt": "fix it", "stop_reason": "end_turn"})
    assert store.get(sid).history == [{"prompt": "fix it", "stop_reason": "end_turn"}]


def test_ids_are_unique(tmp_path):
    store = SessionStore()
    assert store.new(cwd=str(tmp_path)) != store.new(cwd=str(tmp_path))
