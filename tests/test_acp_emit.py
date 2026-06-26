import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

from harness.acp_emit import tool_call_start, tool_call_done, message_chunk, with_meta


def test_tool_call_start_is_pending_execute():
    tc = tool_call_start("tc1", "ls -la")
    assert tc.tool_call_id == "tc1"
    assert tc.status == "pending"
    assert tc.kind == "execute"
    assert "ls -la" in tc.title


def test_tool_call_done_completed_carries_full_output():
    out = {"output": "hello\nworld", "returncode": 0, "exception_info": ""}
    tc = tool_call_done("tc1", out)
    assert tc.tool_call_id == "tc1"
    assert tc.status == "completed"
    # full output present (not truncated); content[0].content.text holds the raw string
    # (str(tc.content) uses repr which escapes \n, so we go via the model field)
    assert tc.content[0].content.text == "hello\nworld"


def test_tool_call_done_failed_on_nonzero():
    out = {"output": "boom", "returncode": 1, "exception_info": "x"}
    assert tool_call_done("tc1", out).status == "failed"


def test_message_chunk_carries_full_text():
    big = "x" * 5000                      # longer than the 120-char event preview
    chunk = message_chunk(big)
    assert big in str(chunk.content)


def test_with_meta_attaches_under_harness_key():
    chunk = with_meta(message_chunk("hi"), {"task_type": "code_fix"})
    assert chunk.field_meta["harness"]["task_type"] == "code_fix"
