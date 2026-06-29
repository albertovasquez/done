# tests/jobs/test_next_run.py
from harness.jobs import model as m
from datetime import datetime, timezone

def test_every_first_and_subsequent():
    s = m.Every(seconds=600)
    assert m.next_run_at(s, now=1000.0, state=m.JobState()) == 1600.0
    assert m.next_run_at(s, now=9999.0, state=m.JobState(last_run_at=1600.0)) == 2200.0

def test_at_future_then_exhausted():
    when = datetime(2030, 1, 1, tzinfo=timezone.utc).timestamp()
    s = m.At(when_iso="2030-01-01T00:00:00+00:00")
    assert m.next_run_at(s, now=0.0, state=m.JobState()) == when
    assert m.next_run_at(s, now=0.0, state=m.JobState(last_run_at=when)) is None  # one-shot done

def test_cron_returns_future():
    s = m.Cron(expr="* * * * *")  # every minute
    nxt = m.next_run_at(s, now=1000.0, state=m.JobState())
    assert nxt is not None and 1000.0 < nxt <= 1060.0
