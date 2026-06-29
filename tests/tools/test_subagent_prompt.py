from harness.tools.subagent_prompt import build_worker_task


def test_includes_goal_context_and_four_fields():
    out = build_worker_task("Survey X", "Files at /a, /b. Use ripgrep.")
    assert "Survey X" in out
    assert "/a, /b" in out
    # The four structured-summary fields are instructed.
    for token in ("did", "found", "modified", "issues"):
        assert token in out.lower()
