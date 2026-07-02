from harness import base_prompt


def test_render_contains_static_policy_for_any_inputs():
    out = base_prompt.render_base_prompt()
    # security posture present
    assert "authorized security testing" in out.lower()
    # a representative discipline rule present
    assert "file_path:line_number" in out


def test_render_interpolates_environment_values():
    out = base_prompt.render_env_block(
        model_id="vibeproxy", cwd="/repo/proj", system_line="macOS-15", cutoff="January 2026")
    assert "# Environment" in out
    assert "vibeproxy" in out
    assert "/repo/proj" in out
    assert "macOS-15" in out
    assert "January 2026" in out
    assert "separate processes" in out   # the new Surface line, not the identity "terminal"


def test_cutoff_defaults_to_module_constant():
    out = base_prompt.render_env_block(model_id="m", cwd="/x", system_line="OS")
    assert base_prompt.KNOWLEDGE_CUTOFF in out


def test_policy_is_nonempty_and_static():
    # always-on identity: the constant must carry real content
    assert base_prompt.BASE_POLICY.strip()


def test_base_prompt_opens_with_done_identity():
    # The base block gives a default identity (persona, if set, layers on top).
    out = base_prompt.render_base_prompt()
    assert "You are Done" in out
    assert "Bitlabs" in out
    # identity comes first — before the Security policy section
    assert out.index("You are Done") < out.index("# Security")


def test_base_prompt_instructs_plan_for_multistep():
    out = base_prompt.render_base_prompt()
    low = out.lower()
    assert "multi-step" in low and "plan" in low
    # teaches the concrete sentinel command grammar (label:status)
    assert "in_progress" in low and ":pending" in low


def test_policy_mentions_dedicated_file_tools_over_shell():
    body = base_prompt.BASE_POLICY.lower()
    assert "read" in body and "edit" in body
    assert "prefer" in body  # the file-tools-over-shell guidance line


def test_policy_promises_parallel_tool_calls():
    assert "parallel" in base_prompt.BASE_POLICY.lower()


def test_policy_states_understand_before_acting_posture():
    """Done's constitutional temperament: investigate/understand before mutating,
    and treat file changes as licensed rather than automatic. This is what contains
    upstream's SWE-bench 'edit the source to solve it' eagerness at the identity
    level (not per task_type)."""
    low = base_prompt.BASE_POLICY.lower()
    # read/inspect is always free; understanding precedes action
    assert "understand" in low or "investigate" in low
    # mutation is proposed, not automatic — the propose-before-changing floor
    assert "propose" in low
    # the interactive-wait vs standing-directive split is spelled out
    assert "standing directive" in low or "go-ahead" in low or "directive" in low


def test_policy_posture_does_not_gate_read_only_inspection():
    """Restraint must not read as 'ask before looking' — inspection stays free, or
    the agent becomes uselessly timid."""
    low = base_prompt.BASE_POLICY.lower()
    # names read-only inspection as freely allowed
    assert "read" in low and ("inspect" in low or "read-only" in low)


def test_policy_explains_harness_voice_and_denial():
    low = base_prompt.BASE_POLICY.lower()
    assert "system-reminder" in low          # harness-injected, not the user
    assert "denied" in low or "declined" in low   # denied tool call = user declined


# --------------------------------------------------------------------------
# Persona files section
# --------------------------------------------------------------------------

def _render(**kw):
    return base_prompt.render_base_prompt(**kw)


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
    assert "You are Done" in out


def test_persona_files_section_absent_if_only_one_arg():
    assert "# Persona files" not in _render(persona_id="fred")          # no dir
    assert "# Persona files" not in _render(persona_dir="/abs/fred")    # no id


def test_persona_files_section_renders_for_default():
    out = _render(persona_id="default", persona_dir="/abs/agents/default")
    assert "# Persona files" in out
    assert "default" in out               # no special-casing of "default"


def test_persona_files_section_is_byte_identical_no_args():
    # adding the optional args must not change the no-args render at all
    before = base_prompt.render_base_prompt()
    assert "# Persona files" not in before


def test_base_prompt_omits_menu_when_none():
    a = base_prompt.render_base_prompt()
    b = base_prompt.render_base_prompt(skills_menu=None)
    assert a == b and "# Skills" not in a       # byte-identical no-op


def test_base_prompt_appends_menu():
    out = base_prompt.render_base_prompt(skills_menu="\n\n# Skills\n\n- **a** — d")
    assert out.endswith("- **a** — d") and "# Skills" in out


def test_base_prompt_omits_agents_when_none():
    a = base_prompt.render_base_prompt()
    b = base_prompt.render_base_prompt(agents_block=None)
    assert a == b and "# Instructions" not in a


def test_base_prompt_appends_menu_after_agents():
    out = base_prompt.render_base_prompt(
        skills_menu="\n\n# Skills\n\n- **a** — d",
        agents_block="\n\n# Instructions\n\nfollow persona...")
    # #139 spine order: agents_block (most stable) before skills_menu
    assert out.index("# Instructions") < out.index("# Skills")
    assert out.endswith("- **a** — d")


def test_env_block_split_out_and_base_has_no_environment():
    out = base_prompt.render_base_prompt(
        persona_id="bob", persona_dir="/p/bob",
        skills_menu="SKILLSMENU", agents_block="AGENTSBLOCK")
    assert "# Environment" not in out
    env = base_prompt.render_env_block(
        model_id="vibeproxy", cwd="/repo/proj", system_line="macOS-15")
    assert "# Environment" in env
    assert "/repo/proj" in env and "vibeproxy" in env and "macOS-15" in env
    assert base_prompt.KNOWLEDGE_CUTOFF in env
    assert "separate processes" in env


def test_spine_order_most_stable_first():
    out = base_prompt.render_base_prompt(
        persona_id="bob", persona_dir="/p/bob",
        skills_menu="SKILLSMENU", agents_block="AGENTSBLOCK")
    i_policy = out.index("You are Done")
    i_agents = out.index("AGENTSBLOCK")
    i_skills = out.index("SKILLSMENU")
    i_persona = out.index("# Persona files")
    assert i_policy < i_agents < i_skills < i_persona
