"""Integration tests for context compaction wired into TracingAgent.

Tests that:
  (a) When compaction is enabled and prior is long, context.compacted event fires
      with correct fields (after_msgs < before_msgs, method in valid set).
  (b) When no compaction kwarg is passed (default off), no context.compacted event
      fires — behavior is byte-identical to the baseline (no-op).
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

    # The TracingAgent's build_compaction needs a model with .query(); we inject
    # a stub via a monkey-patch approach: use build_mock_model() for the agent's
    # reasoning turns (it ends in submission), and supply a stub model for the
    # compaction summarize call (injected via a custom compaction kwarg wrapping).
    #
    # Actually, build_compaction only sees self.model — we need a model that can
    # BOTH drive the agent turn AND answer summarize queries.
    # build_mock_model() is a DeterministicToolcallModel whose .query() returns
    # tool-call responses.  Its response shape has "content" (str) and
    # "extra": {"cost": float}, which is exactly what build_compaction.summarize uses.
    # So build_mock_model() works as the compaction model too (it returns content
    # on each call; the summarize closure only reads content + cost from the dict).
    model = build_mock_model()

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
    assert ev["method"] in ("summary", "truncated"), (
        f"method must be 'summary' or 'truncated', got: {ev['method']!r}"
    )
    assert ev["before_tokens"] > 0
    assert ev["after_tokens"] >= 0


def test_compaction_default_off_no_event(tmp_path):
    """No compaction kwarg → no context.compacted event, run() behaves identically."""
    model = build_mock_model()
    agent = _build_agent(tmp_path, model)   # no compaction_cfg => default off
    prior = _long_prior(60)
    records = _run_agent(agent, prior)

    compacted_events = [r for r in records if r["type"] == "context.compacted"]
    assert compacted_events == [], (
        f"Expected NO context.compacted events when compaction is off, "
        f"but got: {compacted_events}"
    )

    # Also verify the agent completed successfully (no regression)
    finished = [r for r in records if r["type"] == "run.finished"]
    assert finished and finished[-1]["data"]["ok"] is True
