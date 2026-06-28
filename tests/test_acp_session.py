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


def test_transcript_starts_empty(tmp_path):
    store = SessionStore(); sid = store.new(cwd=str(tmp_path))
    assert store.get(sid).transcript == []


def test_extend_appends_copies(tmp_path):
    store = SessionStore(); sid = store.new(cwd=str(tmp_path))
    msg = {"role": "user", "content": "hi", "origin": "chat"}
    store.extend(sid, [msg])
    msg["content"] = "mutated"                      # mutate the input after storing
    assert store.get(sid).transcript == [{"role": "user", "content": "hi", "origin": "chat"}]


def test_extend_rejects_bad_role_or_origin(tmp_path):
    store = SessionStore(); sid = store.new(cwd=str(tmp_path))
    with pytest.raises(AssertionError):
        store.extend(sid, [{"role": "system", "content": "x", "origin": "chat"}])
    with pytest.raises(AssertionError):
        store.extend(sid, [{"role": "user", "content": "x", "origin": "tool"}])


def test_extend_does_not_touch_history(tmp_path):
    store = SessionStore(); sid = store.new(cwd=str(tmp_path))
    store.extend(sid, [{"role": "user", "content": "hi", "origin": "agent"}])
    assert store.get(sid).history == []


def test_session_state_persona_block_defaults_none():
    from harness.acp_session import SessionState
    s = SessionState(cwd="/tmp")
    assert s.persona_block is None      # sentinel: not-yet-composed (NOT "")


def test_session_state_has_workspace_and_memory_fields():
    from harness.acp_session import SessionState
    s = SessionState(cwd="/tmp")
    assert s.workspace_dir is None
    assert s.memory_block is None        # sentinel: not-yet-composed
    assert s.memory_load is None
    assert s.memory_load_emitted is False


def test_store_new_records_workspace_dir(tmp_path):
    from harness.acp_session import SessionStore
    store = SessionStore()
    sid = store.new(cwd=".", workspace_dir=tmp_path)
    assert store.get(sid).workspace_dir == tmp_path
