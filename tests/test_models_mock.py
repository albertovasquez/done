from harness.models_mock import build_mock_model


def test_mock_model_sequence_shape():
    model = build_mock_model()
    assert model.config.cost_per_call == 0.0
    outputs = model.config.outputs
    # Each output is a tool-call assistant turn with at least one action.
    for out in outputs:
        assert out["role"] == "assistant"
        actions = out["extra"]["actions"]
        assert actions, "every mock turn must carry an action"
        for a, tc in zip(actions, out["tool_calls"]):
            assert a["tool_call_id"] == tc["id"]  # ids must match for observation pairing

    # Final command must be the submit sentinel.
    last_cmd = outputs[-1]["extra"]["actions"][-1]["command"]
    assert last_cmd == "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
