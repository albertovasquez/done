from harness import base_prompt


def test_render_contains_static_policy_for_any_inputs():
    out = base_prompt.render_base_prompt(model_id="m", cwd="/x", system_line="OS")
    # security posture present
    assert "authorized security testing" in out.lower()
    # a representative discipline rule present
    assert "file_path:line_number" in out


def test_render_interpolates_environment_values():
    out = base_prompt.render_base_prompt(
        model_id="vibeproxy", cwd="/repo/proj", system_line="macOS-15", cutoff="January 2026")
    assert "# Environment" in out
    assert "vibeproxy" in out
    assert "/repo/proj" in out
    assert "macOS-15" in out
    assert "January 2026" in out


def test_cutoff_defaults_to_module_constant():
    out = base_prompt.render_base_prompt(model_id="m", cwd="/x", system_line="OS")
    assert base_prompt.KNOWLEDGE_CUTOFF in out


def test_policy_is_nonempty_and_static():
    # always-on identity: the constant must carry real content
    assert base_prompt.BASE_POLICY.strip()


def test_base_prompt_opens_with_done_identity():
    # The base block gives a default identity (persona, if set, layers on top).
    out = base_prompt.render_base_prompt(model_id="m", cwd="/x", system_line="OS")
    assert "You are Done" in out
    assert "Bitlabs" in out
    # identity comes first — before the Security policy section
    assert out.index("You are Done") < out.index("# Security")


def test_base_prompt_instructs_plan_for_multistep():
    out = base_prompt.render_base_prompt(model_id="m", cwd="/x", system_line="OS")
    low = out.lower()
    assert "multi-step" in low and "plan" in low
    # teaches the concrete sentinel command grammar (label:status)
    assert "in_progress" in low and ":pending" in low


def test_policy_mentions_dedicated_file_tools_over_shell():
    body = base_prompt.BASE_POLICY.lower()
    assert "read" in body and "edit" in body
    assert "prefer" in body  # the file-tools-over-shell guidance line


def test_policy_does_not_promise_parallel_tool_calls():
    assert "parallel" not in base_prompt.BASE_POLICY.lower()  # deferred follow-up
