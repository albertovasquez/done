import sys

sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

import json  # noqa: E402
import types  # noqa: E402

import pytest  # noqa: E402

from minisweagent.exceptions import FormatError  # noqa: E402

from harness.streaming_model import StreamingLitellmModel  # noqa: E402


def _resp(tool_calls, finish_reason="tool_calls"):
    """Minimal object graph matching response.choices[0].message.tool_calls."""
    msg = types.SimpleNamespace(tool_calls=[
        types.SimpleNamespace(id=tc["id"],
                              function=types.SimpleNamespace(name=tc["name"], arguments=tc["args"]))
        for tc in tool_calls])
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg, finish_reason=finish_reason)])


def _model():
    return StreamingLitellmModel(model_name="vibeproxy/x", cost_tracking="ignore_errors")


def test_query_sends_every_registered_schema(monkeypatch):
    captured = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return _resp([{"id": "c0", "name": "bash", "args": json.dumps({"command": "ls"})}])

    import litellm
    monkeypatch.setattr(litellm, "completion", fake_completion)
    _model()._query([{"role": "user", "content": "hi"}])  # on_delta is None -> blocking branch
    assert {"bash", "read", "write", "edit"} <= {t["function"]["name"] for t in captured["tools"]}


def test_parse_bash_action_has_command_and_tool_name():
    actions = _model()._parse_actions(_resp([{"id": "c0", "name": "bash", "args": json.dumps({"command": "ls"})}]))
    assert actions[0]["tool_name"] == "bash"
    assert actions[0]["command"] == "ls"        # env.execute compatibility
    assert actions[0]["tool_call_id"] == "c0"


def test_parse_file_tool_action_has_args_no_command():
    actions = _model()._parse_actions(_resp([{"id": "c1", "name": "read", "args": json.dumps({"path": "a.txt"})}]))
    assert actions[0]["tool_name"] == "read"
    assert actions[0]["args"] == {"path": "a.txt"}
    assert "command" not in actions[0]


def test_parse_unknown_tool_raises_formaterror_naming_it():
    with pytest.raises(FormatError) as ei:
        _model()._parse_actions(_resp([{"id": "c2", "name": "frobnicate", "args": "{}"}]))
    assert "frobnicate" in str(ei.value.messages[0]["content"])


def test_parse_bad_json_args_raises_formaterror():
    with pytest.raises(FormatError):
        _model()._parse_actions(_resp([{"id": "c3", "name": "read", "args": "{not json"}]))
