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


# --------------------------------------------------------------------------
# Persona files section
# --------------------------------------------------------------------------

def _render(**kw):
    base = dict(model_id="m", cwd="/proj", system_line="TestOS")
    base.update(kw)
    return base_prompt.render_base_prompt(**base)


def test_persona_files_section_present_with_args():
    out = _render(persona_id="fred", persona_dir="/abs/agents/fred")
    assert "# Persona files" in out
    assert "fred" in out
    assert "/abs/agents/fred" in out
    assert "SOUL.md" in out and "IDENTITY.md" in out and "USER.md" in out


def test_persona_files_section_absent_without_args():
    out = _render()                       # no persona_id/persona_dir
    assert "# Persona files" not in out
    # the rest of the base block is intact
    assert "# Environment" in out


def test_persona_files_section_absent_if_only_one_arg():
    assert "# Persona files" not in _render(persona_id="fred")          # no dir
    assert "# Persona files" not in _render(persona_dir="/abs/fred")    # no id


def test_persona_files_section_renders_for_default():
    out = _render(persona_id="default", persona_dir="/abs/agents/default")
    assert "# Persona files" in out
    assert "default" in out               # no special-casing of "default"


def test_persona_files_section_is_byte_identical_no_args():
    # adding the optional args must not change the no-args render at all
    before = base_prompt.render_base_prompt(model_id="m", cwd="/p", system_line="OS")
    assert "# Persona files" not in before


def test_base_prompt_omits_menu_when_none():
    a = base_prompt.render_base_prompt(model_id="m", cwd="/x", system_line="os")
    b = base_prompt.render_base_prompt(model_id="m", cwd="/x", system_line="os", skills_menu=None)
    assert a == b and "# Skills" not in a       # byte-identical no-op


def test_base_prompt_appends_menu():
    out = base_prompt.render_base_prompt(model_id="m", cwd="/x", system_line="os",
                                         skills_menu="\n\n# Skills\n\n- **a** — d")
    assert out.endswith("- **a** — d") and "# Skills" in out
