"""The cron composition closure, actually executed (#245): run the real
_default_deps().run_turn and assert the blocks it hands the runner equal the
shared seam's output for the same inputs. Parity with the interactive path is
structural now — before #245 no test executed this closure at all
(test_executor.py injects fake run_turn lambdas)."""
import platform
from types import SimpleNamespace

import harness.jobs.executor as ex
from harness import prompt as prompt_mod


def _mk_skill(root, name):
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: a test skill\n---\nbody\n",
        encoding="utf-8")


def test_run_turn_composes_via_the_shared_seam(tmp_path, monkeypatch):
    (tmp_path / "home").mkdir()
    (tmp_path / "cfg").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    ws = tmp_path / "agents" / "bob"
    ws.mkdir(parents=True)
    # A workspace project-root skill makes origin labeling observable: the seam
    # classifies it 'project'; the pre-#245 executor (catalog loaded without
    # project_cwd) labeled it 'unknown' — cron menus diverged from run_traced's.
    _mk_skill(ws / ".agents" / "skills", "ws-skill")

    captured = {}

    def fake_build(*, agent_id, model_name, skill_roots, memory_root,
                   agent_cfg, cwd):
        captured["skill_roots"] = skill_roots
        runner = SimpleNamespace(_env=SimpleNamespace())

        def run(message, **kwargs):
            captured["message"] = message
            captured.update(kwargs)
            return iter(())

        runner.run = run
        return runner, None

    import harness.agent_build
    monkeypatch.setattr(harness.agent_build, "build_persona_agent", fake_build)

    deps = ex._default_deps()
    deps.run_turn(model_id=None, workspace=ws, persona_block="P",
                  memory_block="M", message="do the thing")

    expected = prompt_mod.compose_turn(
        workspace_dir=ws, cwd=str(ws), model_id=None,
        system_line=platform.platform(), persona_block="P", memory_block="M")
    assert captured["base_block"] == expected.base_block
    assert captured["env_block"] == expected.env_block
    assert captured["persona_block"] == "P"
    assert captured["memory_block"] == "M"
    assert captured["skill_block"] == ""     # no router-seeded skills on cron
    assert captured["skill_roots"] == expected.skill_roots
    assert "## project" in captured["base_block"]
