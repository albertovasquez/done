"""CONTRACT PIN: the live worker card depends on build_persona_agent() returning
a runner whose run() YIELDS lifecycle events (run.started / run.finished). This
is currently true only because build_persona_agent hands back a MiniSweAgentRunner
(the dev/CLI bridge) — an incidental routing, not a documented contract. If a
future refactor makes the worker path drive TracingAgent directly (no yield),
the worker card silently goes dark. This test fails loudly if that happens.

See harness/tools/subagent.py:_run_one_worker and the design spec
docs/superpowers/specs/2026-07-01-worker-card-footer-design.md."""
from __future__ import annotations

import yaml

from harness.agent_build import build_persona_agent
from harness.paths import mini_yaml_path


def _agent_cfg():
    # The real worker cfg (mini.yaml) — has the required system/instance templates.
    cfg = dict(yaml.safe_load(mini_yaml_path().read_text())["agent"])
    cfg["step_limit"] = 1     # one step is enough to see run.started/finished
    return cfg


def test_worker_runner_yields_lifecycle_events(tmp_path):
    runner, _ = build_persona_agent(
        "default", model_name=None, agent_cfg=_agent_cfg(),
        memory_root=tmp_path, toolset={"read", "bash"}, is_worker=True,
    )
    types = [getattr(ev, "type", "") for ev in runner.run("do a trivial thing")]
    assert "run.started" in types
    assert "run.finished" in types
