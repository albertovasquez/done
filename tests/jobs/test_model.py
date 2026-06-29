# tests/jobs/test_model.py
from harness.jobs import model as m

def _job():
    return m.Job(
        id="j1", name="Nightly", agent_id="fred",
        schedule=m.Cron(expr="0 9 * * MON", tz=None, stagger_ms=None),
        payload=m.AgentTurn(message="summarize", model=None, agent_options={"thinking": "x"}),
        grant=m.Grant(tools="inherit", paths="workspace", write=False, exec=False, network=False),
        cost=m.CostGate(timeout_s=300, min_cadence_s=3600, max_consecutive_failures=3),
        state=m.JobState(),
    )

def test_job_roundtrip_preserves_unions_and_extbag():
    j = _job()
    back = m.job_from_dict(m.job_to_dict(j))
    assert back == j
    assert isinstance(back.schedule, m.Cron)
    assert isinstance(back.payload, m.AgentTurn)
    assert back.payload.agent_options == {"thinking": "x"}   # lossless ext bag
    assert back.session_target == "isolated"
    assert back.grant.enforced is False

def test_every_and_at_and_reminder_roundtrip():
    for sched in (m.At(when_iso="2026-07-01T09:00:00Z"), m.Every(seconds=600, anchor=None)):
        d = m.schedule_to_dict(sched)
        assert m.schedule_from_dict(d) == sched
    r = m.Reminder(text="standup")
    assert m.payload_from_dict(m.payload_to_dict(r)) == r
