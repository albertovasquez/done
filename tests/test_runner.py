import sys
import threading
import time

import pytest

sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

import json
import yaml
from pathlib import Path
from minisweagent.environments.local import LocalEnvironment
from minisweagent.models.test_models import DeterministicToolcallModel, make_toolcall_output

from trace.runner import MiniSweAgentRunner, RunResult


def _agent_cfg() -> dict:
    cfg = yaml.safe_load(Path("upstream/src/minisweagent/config/mini.yaml").read_text())
    return cfg["agent"]


def _tc_model(turns):
    outputs = []
    for i, (content, commands) in enumerate(turns):
        tc_actions, tool_calls = [], []
        for j, command in enumerate(commands):
            tcid = f"call_{i}_{j}"
            tc_actions.append({"command": command, "tool_call_id": tcid})
            tool_calls.append({"id": tcid, "type": "function",
                               "function": {"name": "bash", "arguments": json.dumps({"command": command})}})
        outputs.append(make_toolcall_output(content, tool_calls, tc_actions))
    return DeterministicToolcallModel(outputs=outputs, cost_per_call=0.0)


class _RaiseModel:
    """Minimal model that raises exc on the first query() call.

    Using a plain Python class (not Pydantic) avoids the serialization failure
    that occurs when a live exception object is stored inside a Pydantic field
    and save() → serialize() → model_dump(mode='json') is called.
    """

    class _Config:
        model_name = "raise_model"

    def __init__(self, exc: BaseException):
        self._exc = exc
        self.config = self._Config()

    def query(self, messages, **kwargs):
        raise self._exc

    def format_message(self, **kwargs) -> dict:
        return kwargs

    def format_observation_messages(self, message, outputs, template_vars=None):
        return []

    def get_template_vars(self, **kwargs) -> dict:
        return {}

    def serialize(self) -> dict:
        return {"info": {"config": {"model": {"model_name": "raise_model"}, "model_type": "RaiseModel"}}}


def _raise_model(exc: BaseException) -> _RaiseModel:
    return _RaiseModel(exc)


def _runner(model, tmp_path):
    return MiniSweAgentRunner(model, LocalEnvironment(cwd=str(tmp_path)), agent_cfg=_agent_cfg())


def test_1_event_sequence_and_result(tmp_path):
    model = _tc_model([("hi", ["echo hi"]),
                       ("done", ["echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"])])
    runner = _runner(model, tmp_path)
    events = list(runner.run("t"))
    types = [e.type for e in events]
    assert types[0] == "run.started" and types[-1] == "run.finished"
    assert "llm.call" in types and "action.done" in types
    assert [e.seq for e in events] == list(range(len(events)))
    assert isinstance(runner.result, RunResult)
    assert runner.result.exit_status == "Submitted" and runner.result.ok is True
    # submission provenance: comes from the returned dict on the success path.
    # The submit sentinel produces an empty submission body, so "" is expected,
    # but the field must be a str sourced from the returned dict (not None).
    assert runner.result.submission == ""
    assert runner.result.error is None
    assert runner.result.n_calls >= 1


def test_2_terminal_submission_survives_bridge(tmp_path):
    model = _tc_model([("done", ["echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"])])
    runner = _runner(model, tmp_path)
    events = list(runner.run("t"))
    types = [e.type for e in events]
    submit_idx = next(i for i, e in enumerate(events)
                      if e.type == "action" and "COMPLETE_TASK" in e.data["command"])
    assert "action.done" in types[submit_idx + 1:]
    assert types[-1] == "run.finished" and runner.result.ok is True


def test_3_exception_propagation(tmp_path):
    runner = _runner(_raise_model(RuntimeError("kaboom")), tmp_path)
    seen = []
    with pytest.raises(RuntimeError, match="kaboom"):
        for e in runner.run("t"):
            seen.append(e.type)
    assert "run.finished" in seen  # terminal event flowed through before the raise
    # RunResult is populated on the error path (built from the run.finished event,
    # since the returned dict is absent when agent.run() re-raises).
    assert runner.result is not None
    assert runner.result.ok is False
    assert runner.result.error is not None
    assert runner.result.submission == ""  # not recoverable on the error path


def test_5_baseexception_does_not_hang(tmp_path):
    runner = _runner(_raise_model(KeyboardInterrupt()), tmp_path)
    result_box = {}
    def drive():
        seen = []
        try:
            for e in runner.run("t"):
                seen.append(e.type)
        except BaseException as ex:  # noqa: BLE001
            result_box["exc"] = type(ex).__name__
            result_box["seen"] = seen
    th = threading.Thread(target=drive)
    th.start()
    th.join(timeout=10)
    assert not th.is_alive(), "generator hung on a worker-side BaseException"
    assert result_box.get("exc") == "KeyboardInterrupt"
    assert "run.finished" in result_box.get("seen", [])


def test_6_early_close_joins_worker(tmp_path):
    model = _tc_model([("hi", ["echo hi"]),
                       ("done", ["echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"])])
    runner = _runner(model, tmp_path)
    gen = runner.run("t")
    first = next(gen)
    assert first.type == "run.started"
    gen.close()  # must drain-to-_DONE and join the worker (blocking, mock finishes fast)
    # Give the worker a moment; assert no MiniSweAgentRunner worker thread is left alive.
    time.sleep(0.2)
    alive = [t for t in threading.enumerate() if t.name.startswith("agentrunner-")]
    assert alive == [], f"worker thread leaked after gen.close(): {alive}"
