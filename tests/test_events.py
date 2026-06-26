import json
from pathlib import Path

import pytest

import sys
sys.path.insert(0, "upstream/src")  # for any transitive import; events.py itself has none
sys.path.insert(0, ".")

from harness.events import Event, Emitter


def test_event_to_dict_roundtrips():
    e = Event(seq=3, t=1.5, type="llm.call", data={"n": 1})
    assert e.to_dict() == {"seq": 3, "t": 1.5, "type": "llm.call", "data": {"n": 1}}


def test_emitter_assigns_seq_and_writes_jsonl(tmp_path):
    p = tmp_path / "events.jsonl"
    ticks = iter([0.0, 0.1, 0.2])
    em = Emitter(p, clock=lambda: next(ticks), console=False)
    e0 = em.emit("run.started", task="x")
    e1 = em.emit("llm.call", n=1)
    em.close()

    assert (e0.seq, e1.seq) == (0, 1)
    lines = p.read_text().strip().splitlines()
    assert len(lines) == 2
    rec0 = json.loads(lines[0])
    assert rec0["type"] == "run.started"
    assert rec0["seq"] == 0
    assert rec0["data"] == {"task": "x"}


def test_emitter_raises_loudly_when_path_unopenable(tmp_path):
    bad = tmp_path / "nonexistent_dir" / "events.jsonl"  # parent does not exist
    with pytest.raises(OSError):
        Emitter(bad, clock=lambda: 0.0, console=False)


def test_console_write_failure_does_not_crash(tmp_path, monkeypatch):
    p = tmp_path / "events.jsonl"
    em = Emitter(p, clock=lambda: 0.0, console=True)

    def boom(*a, **k):
        raise RuntimeError("console down")

    monkeypatch.setattr(em, "_print_console", boom)
    # Must not raise:
    em.emit("action", command="ls")
    em.close()
    assert len(p.read_text().strip().splitlines()) == 1


def test_write_event_persists_given_event_without_reassigning(tmp_path):
    p = tmp_path / "events.jsonl"
    em = Emitter(p, clock=lambda: 9.9, console=False)
    # An event built elsewhere (e.g. by a QueueEmitter) with its own seq/t:
    pre_built = Event(seq=42, t=1.23, type="action", data={"command": "ls"})
    em.write_event(pre_built)
    em.close()
    rec = json.loads(p.read_text().strip())
    assert rec["seq"] == 42      # NOT reassigned to the emitter's 0
    assert rec["t"] == 1.23      # NOT replaced by clock() == 9.9
    assert rec["type"] == "action"
