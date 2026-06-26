"""--yolo mode: auto-allow every command without prompting the client.

The decision lives on HarnessAgent: when yolo is on, the permission gate returns
allow WITHOUT a client round-trip (no modal). We test the small pure predicate
the closure consults, so we don't have to drive a full prompt turn.
"""

import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

from harness.acp_agent import HarnessAgent


def _agent(yolo: bool) -> HarnessAgent:
    return HarnessAgent(model_factory=lambda *_: None, agent_cfg={}, skills_dir=[],
                        router=None, worker_model_id=None, yolo=yolo)


def test_yolo_off_by_default():
    a = HarnessAgent(model_factory=lambda *_: None, agent_cfg={}, skills_dir=[],
                     router=None, worker_model_id=None)
    assert a._yolo is False


def test_yolo_auto_allows_without_client():
    # yolo on -> auto-allow, regardless of client capabilities (no round-trip)
    a = _agent(yolo=True)
    assert a._auto_allow() is True


def test_non_yolo_does_not_auto_allow():
    a = _agent(yolo=False)
    assert a._auto_allow() is False
