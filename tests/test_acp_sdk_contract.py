import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

import inspect
import acp


def test_acp_api_surface_exists():
    # the symbols the Phase-4 adapter depends on
    for name in ["Agent", "Client", "run_agent", "spawn_agent_process",
                 "start_tool_call", "update_tool_call", "tool_content",
                 "update_agent_message_text", "text_block", "PROTOCOL_VERSION"]:
        assert hasattr(acp, name), f"acp missing {name}"
    assert acp.PROTOCOL_VERSION == 1

    # Agent hooks we override, with the param names we use
    init_sig = inspect.signature(acp.Agent.initialize)
    assert "protocol_version" in init_sig.parameters
    prompt_sig = inspect.signature(acp.Agent.prompt)
    assert {"prompt", "session_id"} <= set(prompt_sig.parameters)
    assert "session_id" in inspect.signature(acp.Agent.cancel).parameters

    # _meta channel: field_meta exists on the chunk model we tag
    from acp.schema import AgentMessageChunk
    assert "field_meta" in AgentMessageChunk.model_fields


def test_not_the_wrong_acp_package():
    # guard the name collision: this must be Zed's ACP, which exposes Agent+run_agent
    assert hasattr(acp, "Agent") and hasattr(acp, "run_agent")
