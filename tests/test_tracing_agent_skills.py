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
