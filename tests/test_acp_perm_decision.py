# tests/test_acp_perm_decision.py
from pathlib import Path

from harness.acp_agent import decide_permission
from harness.permcheck import PermissionRequest


def _file(write=False, outside=False):
    return PermissionRequest(kind="file", path=Path("/x"), is_write=write,
                             outside_roots=outside)


def test_yolo_allows_everything():
    assert decide_permission(_file(write=True, outside=True),
                             yolo=True, has_elicitation=False) == "allow"


def test_in_root_read_is_free():
    assert decide_permission(_file(write=False, outside=False),
                             yolo=False, has_elicitation=False) == "allow"


def test_in_root_write_is_free():
    assert decide_permission(_file(write=True, outside=False),
                             yolo=False, has_elicitation=False) == "allow"


def test_outside_root_write_no_channel_denies():
    assert decide_permission(_file(write=True, outside=True),
                             yolo=False, has_elicitation=False) == "deny"


def test_outside_root_write_with_channel_prompts():
    assert decide_permission(_file(write=True, outside=True),
                             yolo=False, has_elicitation=True) == "ask"


def test_bash_no_channel_denies():
    req = PermissionRequest(kind="bash", command="ls", is_exec=True)
    assert decide_permission(req, yolo=False, has_elicitation=False) == "deny"


def test_bash_with_channel_prompts():
    req = PermissionRequest(kind="bash", command="ls", is_exec=True)
    assert decide_permission(req, yolo=False, has_elicitation=True) == "ask"
