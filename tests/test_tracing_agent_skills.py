import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

from harness.events import Emitter
from harness.tracing_agent import TracingAgent
from harness.models_mock import build_mock_model
from minisweagent.environments.local import LocalEnvironment


def _agent(tmp_path, skill_block):
    em = Emitter(tmp_path / "e.jsonl", clock=lambda: 0.0, console=False)
    return TracingAgent(
        build_mock_model(), LocalEnvironment(cwd=str(tmp_path)), emitter=em,
        skill_block=skill_block,
        system_template="SYS BASE", instance_template="INST {{task}}")


def test_skill_block_appended_to_system_template_only(tmp_path):
    a = _agent(tmp_path, "\n\nSKILLDATA")
    a.extra_template_vars = {"task": "t"}                    # so instance renders
    assert a._render_template(a.config.system_template) == "SYS BASE\n\nSKILLDATA"
    # instance template must NOT get the block
    assert "SKILLDATA" not in a._render_template(a.config.instance_template)


def test_empty_skill_block_is_byte_identical(tmp_path):
    a = _agent(tmp_path, "")
    assert a._render_template(a.config.system_template) == "SYS BASE"


def test_block_with_jinja_is_literal_not_evaluated(tmp_path):
    a = _agent(tmp_path, "\n\n{{ undefined_var }}")          # would raise if rendered
    assert a._render_template(a.config.system_template) == "SYS BASE\n\n{{ undefined_var }}"


def _agent_p(tmp_path, *, persona_block="", skill_block=""):
    em = Emitter(tmp_path / "e2.jsonl", clock=lambda: 0.0, console=False)
    return TracingAgent(
        build_mock_model(), LocalEnvironment(cwd=str(tmp_path)), emitter=em,
        persona_block=persona_block, skill_block=skill_block,
        system_template="SYS BASE", instance_template="INST {{task}}")


def test_persona_block_appended_after_base_before_skills(tmp_path):
    a = _agent_p(tmp_path, persona_block="\n\nPERSONA", skill_block="\n\nSKILLS")
    rendered = a._render_template(a.config.system_template)
    assert rendered == "SYS BASE\n\nPERSONA\n\nSKILLS"   # base -> persona -> skills
    # instance template gets neither
    a.extra_template_vars = {"task": "t"}
    inst = a._render_template(a.config.instance_template)
    assert "PERSONA" not in inst and "SKILLS" not in inst


def test_empty_persona_block_is_byte_identical(tmp_path):
    a = _agent_p(tmp_path, persona_block="", skill_block="")
    assert a._render_template(a.config.system_template) == "SYS BASE"


def test_persona_block_jinja_is_literal(tmp_path):
    a = _agent_p(tmp_path, persona_block="\n\n{{ undefined }}")
    assert a._render_template(a.config.system_template) == "SYS BASE\n\n{{ undefined }}"


def test_memory_block_injected_between_persona_and_skills(tmp_path):
    from harness.events import Emitter
    from harness.tracing_agent import TracingAgent
    from harness.models_mock import build_mock_model
    from minisweagent.environments.local import LocalEnvironment
    em = Emitter(tmp_path / "e3.jsonl", clock=lambda: 0.0, console=False)
    a = TracingAgent(build_mock_model(), LocalEnvironment(cwd=str(tmp_path)),
                     emitter=em, persona_block="\n\nP", memory_block="\n\nM",
                     skill_block="\n\nS",
                     system_template="SYS BASE", instance_template="INST {{task}}")
    assert a._render_template(a.config.system_template) == "SYS BASE\n\nP\n\nM\n\nS"


def test_empty_memory_block_is_byte_identical(tmp_path):
    from harness.events import Emitter
    from harness.tracing_agent import TracingAgent
    from harness.models_mock import build_mock_model
    from minisweagent.environments.local import LocalEnvironment
    em = Emitter(tmp_path / "e4.jsonl", clock=lambda: 0.0, console=False)
    a = TracingAgent(build_mock_model(), LocalEnvironment(cwd=str(tmp_path)),
                     emitter=em, persona_block="", memory_block="", skill_block="",
                     system_template="SYS BASE", instance_template="INST {{task}}")
    assert a._render_template(a.config.system_template) == "SYS BASE"
