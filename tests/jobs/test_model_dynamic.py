from harness.jobs import model as m


def test_dynamic_roundtrip():
    d = m.Dynamic()
    assert m.schedule_to_dict(d) == {"kind": "dynamic"}
    assert m.schedule_from_dict({"kind": "dynamic"}) == d


def test_dynamic_fresh_state_arms_now():
    # Never run: arm immediately (first run on next tick).
    st = m.JobState()  # last_run_at is None
    assert m.next_run_at(m.Dynamic(), now=1000.0, state=st) == 1000.0


def test_dynamic_override_arms_now_plus_override():
    st = m.JobState(last_run_at=1000.0)
    got = m.next_run_at(m.Dynamic(), now=1000.0, state=st, override=300)
    assert got == 1300.0


def test_dynamic_override_floored_by_min_cadence():
    st = m.JobState(last_run_at=1000.0)
    got = m.next_run_at(m.Dynamic(), now=1000.0, state=st,
                        override=10, min_cadence_s=60)
    assert got == 1060.0  # 10 floored up to 60


def test_dynamic_no_override_after_run_pauses():
    st = m.JobState(last_run_at=1000.0)
    assert m.next_run_at(m.Dynamic(), now=1000.0, state=st) is None


def test_dynamic_none_min_cadence_does_not_crash():
    # Defense in depth: next_run_at runs OUTSIDE ops.run's try/except, so a None
    # floor must coerce to 0, not raise (a TypeError there = undisableable
    # crash-loop). The create gate also rejects None up front, but this call site
    # must be crash-proof regardless.
    st = m.JobState(last_run_at=1000.0)
    got = m.next_run_at(m.Dynamic(), now=1000.0, state=st,
                        override=30, min_cadence_s=None)
    assert got == 1030.0


def test_existing_every_unaffected_by_new_kwargs():
    st = m.JobState(last_run_at=1000.0)
    # override/min_cadence_s are ignored by Every.
    assert m.next_run_at(m.Every(seconds=50), now=1000.0, state=st,
                         override=999, min_cadence_s=999) == 1050.0
