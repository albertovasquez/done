"""Integration tests for context compaction wired into TracingAgent.

Tests that:
  (a) When compaction is enabled and prior is long, context.compacted event fires
      with correct fields (after_msgs < before_msgs, method in valid set).
  (b) When no compaction kwarg is passed (default ON), context.compacted event
      fires when the prior is large enough.
  (c) Explicit enabled:False opts out — no context.compacted event fired.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml
import pytest
from minisweagent.environments.local import LocalEnvironment
from minisweagent.models.test_models import DeterministicToolcallModel, make_toolcall_output

from harness.events import Emitter
from harness.models_mock import build_mock_model
from harness.tracing_agent import TracingAgent


# ---------------------------------------------------------------------------
# Helpers shared with test_tracing_agent.py (copied; no shared fixture module)
# ---------------------------------------------------------------------------

def _agent_config() -> dict:
    cfg = yaml.safe_load(Path("upstream/src/minisweagent/config/mini.yaml").read_text())
    return cfg["agent"]


def _submit_model() -> DeterministicToolcallModel:
    """One-turn model that immediately submits — terminates run() cleanly."""
    out = make_toolcall_output(
        "done",
        [{"id": "call_0", "type": "function",
          "function": {"name": "bash",
                       "arguments": '{"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"}'}}],
        [{"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT", "tool_call_id": "call_0"}],
    )
    out["extra"]["cost"] = 0.0
    return DeterministicToolcallModel(outputs=[out], cost_per_call=0.0)


def _build_agent(tmp_path: Path, model, *, compaction_cfg=None) -> TracingAgent:
    """Construct a TracingAgent, optionally with a compaction config."""
    emitter = Emitter(tmp_path / "events.jsonl", clock=lambda: 0.0, console=False)
    env = LocalEnvironment(cwd=str(tmp_path))
    agent_cfg = _agent_config()
    agent_cfg["output_path"] = str(tmp_path / "traj.json")
    kwargs = dict(**agent_cfg)
    if compaction_cfg is not None:
        kwargs["compaction"] = compaction_cfg
    return TracingAgent(model, env, emitter=emitter, **kwargs)


def _run_agent(agent: TracingAgent, prior: list[dict]) -> list[dict]:
    """Run the agent with the given prior, close emitter, return event records."""
    agent.run("dummy task", prior=prior)
    agent._emitter.close()
    lines = (Path(agent._emitter._fh.name)).read_text().splitlines()
    return [json.loads(l) for l in lines]


# ---------------------------------------------------------------------------
# Long prior: 60 user/assistant pairs → lots of tokens, triggers compaction
# ---------------------------------------------------------------------------

def _long_prior(n: int = 60) -> list[dict]:
    """Return n user/assistant message pairs — enough to exceed a small ctx_window."""
    msgs = []
    for i in range(n):
        msgs.append({"role": "user",
                     "content": f"Turn {i}: " + "x" * 200})
        msgs.append({"role": "assistant",
                     "content": f"Response {i}: " + "y" * 200})
    return msgs


# ---------------------------------------------------------------------------
# Compaction summarize stub: just returns a fixed string without calling an LLM
# ---------------------------------------------------------------------------

class _StubSummarizeModel:
    """Minimal model duck-typed for build_compaction's summarize closure."""
    class config:
        model_name = "stub"

    def query(self, messages):
        return {"content": "[SUMMARY]", "extra": {"cost": 0.0}}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_compaction_enabled_long_prior_emits_event(tmp_path):
    """Compaction enabled + long prior → context.compacted event with correct fields."""
    # Build a compaction config. ctx_window is small so the long prior exceeds
    # the threshold fraction of it.  Use a real build_compaction-compatible model.
    compaction_cfg = {
        "enabled": True,
        "ctx_window": 5000,     # small so our ~24K-char prior exceeds threshold
        "threshold": 0.5,
        "target_ratio": 0.2,
        "protect_head_n": 0,
        "protect_last_n": 2,    # keep tiny tail so middle is large → summary fires
    }

    # build_mock_model() drives the agent turn (scripted tool-call outputs).
    # The compaction adapter is rebuilt inside run() (Fix A) using agent.model —
    # the same DeterministicToolcallModel.  To prevent summarize() from consuming
    # the scripted outputs, we wrap model.query: calls whose first message contains
    # COMPRESS_SYSTEM are intercepted and return a canned summary without touching
    # the scripted output queue; all other calls delegate to the real model.
    from harness.compaction import COMPRESS_SYSTEM
    model = build_mock_model()
    _original_query = model.query

    def _patched_query(messages):
        first_content = (messages[0].get("content") or "") if messages else ""
        if COMPRESS_SYSTEM in first_content:
            return {"role": "assistant", "content": "TEST SUMMARY",
                    "extra": {"cost": 0.0}}
        return _original_query(messages)

    model.query = _patched_query

    agent = _build_agent(tmp_path, model, compaction_cfg=compaction_cfg)
    prior = _long_prior(60)   # 60 pairs × 2 = 120 messages, ~24K chars
    records = _run_agent(agent, prior)

    event_types = [r["type"] for r in records]
    compacted_events = [r for r in records if r["type"] == "context.compacted"]

    assert compacted_events, (
        "Expected a context.compacted event but none were emitted. "
        f"Events seen: {event_types}"
    )

    ev = compacted_events[0]["data"]
    assert ev["after_msgs"] < ev["before_msgs"], (
        f"after_msgs ({ev['after_msgs']}) should be < before_msgs ({ev['before_msgs']})"
    )
    assert ev["method"] == "summary", (
        f"method must be 'summary' (summarize path ran via patched query), got: {ev['method']!r}"
    )
    assert ev["before_tokens"] > 0
    assert ev["after_tokens"] >= 0


def test_compaction_explicit_disable_no_event(tmp_path):
    """Explicit enabled:False → no context.compacted event, run() behaves identically."""
    model = build_mock_model()
    agent = _build_agent(tmp_path, model, compaction_cfg={"enabled": False})
    prior = _long_prior(60)
    records = _run_agent(agent, prior)

    compacted_events = [r for r in records if r["type"] == "context.compacted"]
    assert compacted_events == [], (
        f"Expected NO context.compacted events when compaction is explicitly disabled, "
        f"but got: {compacted_events}"
    )

    # Also verify the agent completed successfully (no regression)
    finished = [r for r in records if r["type"] == "run.finished"]
    assert finished and finished[-1]["data"]["ok"] is True


def _events_collector():
    seen = []
    class E:
        def set_clock(self, *_): pass
        def emit(self, name, **data): seen.append((name, data))
    return E(), seen


def _agent_cfg():
    return {"system_template": "You are a helpful agent.",
            "instance_template": "Task: {{task}}",
            "step_limit": 10, "cost_limit": 5.0}


def test_compaction_default_on_fires_without_config(tmp_path):
    """NO compaction kwarg → ON by default; fires when prior is large enough."""
    emitter, seen = _events_collector()
    model = build_mock_model()
    agent = TracingAgent(model, LocalEnvironment(cwd=str(tmp_path)),
                         emitter=emitter, registry=None, **_agent_cfg())
    # floor ctx_window = 32000 tokens; budget = 0.5 * 32000 = 16000 est tokens.
    # estimate_tokens = len // 4, so we need prior_chars > 64000.
    # 60 messages × "turn-{i} " * 400 ≈ 60 × 3600 chars = 216000 chars ≈ 54000 est tokens → well above floor.
    prior = [{"role": "user", "content": f"turn-{i} " * 400} for i in range(60)]
    agent.run("solve the bug", prior=prior)
    names = [n for n, _ in seen]
    assert "context.compacted" in names, (
        f"Expected context.compacted event but got: {names}"
    )
    assert "context.compaction.eval" in names, (
        f"Expected context.compaction.eval event but got: {names}"
    )


def test_compaction_explicit_disable_is_off(tmp_path):
    """Explicit enabled:False → no compaction events even with a large prior."""
    emitter, seen = _events_collector()
    agent = TracingAgent(build_mock_model(), LocalEnvironment(cwd=str(tmp_path)),
                         emitter=emitter, registry=None,
                         compaction={"enabled": False}, **_agent_cfg())
    agent.run("solve the bug",
              prior=[{"role": "user", "content": "x " * 5000} for _ in range(40)])
    names = [n for n, _ in seen]
    assert "context.compacted" not in names, (
        f"Expected no context.compacted event but got: {names}"
    )
    assert "context.compaction.eval" not in names, (
        f"Expected no context.compaction.eval event but got: {names}"
    )
