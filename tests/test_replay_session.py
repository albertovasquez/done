import asyncio

import pytest

from harness.acp_agent import HarnessAgent
from harness.tui.render import render_update


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path


def _make_agent_with_recording_conn(cwd="/x"):
    """A HarnessAgent with a fake conn that records every session_update call."""

    class _RecordingConn:
        def __init__(self):
            self.updates = []  # list of (session_id, update)

        async def session_update(self, session_id, update, **kw):
            self.updates.append((session_id, update))

    agent = HarnessAgent(
        model_factory=lambda *a, **k: None,
        agent_cfg={},
        skills_dir=[],
        router=object(),
        worker_model_id="mock",
        yolo=False,
        backend="mock",
        cwd=cwd,
    )
    conn = _RecordingConn()
    agent.on_connect(conn)
    return agent, conn


def _render_kind(upd) -> str | None:
    """Return the render kind string for an update, or None if unrenderable."""
    item = render_update(upd)
    return item.kind if item is not None else None


def _meta_of(upd) -> dict:
    return (getattr(upd, "field_meta", None) or {}).get("harness") or {}


def test_replay_session_streams_transcript_then_resumed_seam():
    async def go():
        agent, conn = _make_agent_with_recording_conn()
        resp = agent._activate_seat("default")
        sid = resp["session_id"]
        agent._store.extend(sid, [
            {"role": "user", "content": "remember 42", "origin": "chat"},
            {"role": "assistant", "content": "noted: 42", "origin": "chat"},
        ])
        out = await agent.ext_method("harness/replay_session", {"id": "default"})
        assert out == {"ok": True, "count": 2}
        # conn.updates is a list of (session_id, update) recorded by the fake conn.
        kinds = [_render_kind(u) for (_sid, u) in conn.updates]  # helper: render_update(u).kind
        # first message has NO leading boundary; user then assistant then resumed seam
        assert kinds[:2] == ["user", "message"]
        last_sid, last_upd = conn.updates[-1]
        meta = getattr(last_upd, "field_meta", None) or {}
        assert (meta.get("harness") or {}).get("resumed") is True

    asyncio.run(go())


def test_replay_separates_consecutive_assistant_messages_with_a_boundary():
    """Two back-to-back ASSISTANT messages must be separated by a stream_reset
    boundary, else the client's _stream_message MERGES them into one widget
    (Codex review). The first message has no leading boundary; the second does."""
    async def go():
        agent, conn = _make_agent_with_recording_conn()
        resp = agent._activate_seat("default")
        sid = resp["session_id"]
        agent._store.extend(sid, [
            {"role": "assistant", "content": "first answer", "origin": "chat"},
            {"role": "assistant", "content": "second answer", "origin": "chat"},
        ])
        await agent.ext_method("harness/replay_session", {"id": "default"})
        seq = [(_render_kind(u), _meta_of(u).get("stream_reset"), _meta_of(u).get("resumed"))
               for (_sid, u) in conn.updates]
        # expected order: msg(first), boundary(stream_reset), msg(second), seam(resumed)
        assert seq[0][0] == "message" and seq[0][1] is None        # no leading boundary
        assert seq[1][1] is True                                    # boundary before 2nd
        assert seq[2][0] == "message"
        assert seq[-1][2] is True                                   # resumed seam last

    asyncio.run(go())


def test_replay_session_empty_transcript_emits_no_messages_only_returns_zero():
    async def go():
        agent, conn = _make_agent_with_recording_conn()
        agent._activate_seat("default")
        out = await agent.ext_method("harness/replay_session", {"id": "default"})
        assert out == {"ok": True, "count": 0}
        # no per-message updates (a resumed seam with zero history is pointless);
        # assert no message/user updates were emitted.
        kinds = [_render_kind(u) for (_sid, u) in conn.updates]
        assert "user" not in kinds and "message" not in kinds

    asyncio.run(go())


def test_replay_session_missing_id_returns_error():
    async def go():
        agent, conn = _make_agent_with_recording_conn()
        agent._activate_seat("default")
        out = await agent.ext_method("harness/replay_session", {})
        assert out == {"ok": False, "error": "missing id"}
        out2 = await agent.ext_method("harness/replay_session", {"id": ""})
        assert out2 == {"ok": False, "error": "missing id"}

    asyncio.run(go())


def test_replay_session_unknown_persona_returns_error(caplog):
    async def go():
        agent, conn = _make_agent_with_recording_conn()
        with caplog.at_level("WARNING", logger="harness.acp_agent"):
            out = await agent.ext_method("harness/replay_session", {"id": "no-such-persona"})
        assert out["ok"] is False
        assert "error" in out
        assert any("replay_session rejected" in r.message for r in caplog.records), \
            f"rejection must be logged; got {[r.message for r in caplog.records]}"

    asyncio.run(go())
