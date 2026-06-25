import ast
import inspect
import json
import sys
from pathlib import Path

sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

import trace.run_traced as rt

import json as _json
from trace.router import Router, Classification
from trace.run_traced import route_and_dispatch


class _FixedRouter:
    """Router stand-in returning preset Classifications in sequence."""
    def __init__(self, *classifications):
        self._seq = list(classifications)
        self._i = -1
    def classify(self, prompt):
        self._i += 1
        return self._seq[min(self._i, len(self._seq) - 1)]


def _spy_agent():
    calls = []
    def run_agent(prompt):
        calls.append(prompt)
    run_agent.calls = calls
    return run_agent


def _emitter(tmp_path):
    from trace.events import Emitter
    return Emitter(tmp_path / "events.jsonl", clock=lambda: 0.0, console=False)


def _cls(task_type, **kw):
    return Classification(task_type=task_type, **kw)


def test_4_chat_question_does_not_run_agent(tmp_path):
    spy = _spy_agent()
    out = []
    rc = route_and_dispatch(
        "what is 1+1",
        router=_FixedRouter(_cls("chat_question", confidence=0.97)),
        emitter=_emitter(tmp_path),
        make_chat_handler=lambda: type("H", (), {"answer": lambda s, p: "2"})(),
        run_agent=spy, ask_user=lambda q: "", echo=out.append, worker_model_id="gpt-5.4")
    assert rc == 0
    assert spy.calls == []                 # agent NEVER ran for a chat question
    assert "2" in out

    spy2 = _spy_agent()
    route_and_dispatch(
        "fix the bug",
        router=_FixedRouter(_cls("code_fix", confidence=0.9)),
        emitter=_emitter(tmp_path), make_chat_handler=lambda: None,
        run_agent=spy2, ask_user=lambda q: "", echo=lambda t: None, worker_model_id="gpt-5.4")
    assert spy2.calls == ["fix the bug"]   # agent DID run for code_fix


def test_5_suggested_model_not_applied(tmp_path):
    out = []
    spy = _spy_agent()
    route_and_dispatch(
        "fix it",
        router=_FixedRouter(_cls("code_fix", confidence=0.9, suggested_model="claude-opus-4-8")),
        emitter=_emitter(tmp_path), make_chat_handler=lambda: None,
        run_agent=spy, ask_user=lambda q: "", echo=out.append, worker_model_id="gpt-5.4")
    assert spy.calls == ["fix it"]                       # ran with the worker path
    assert any("claude-opus-4-8" in line for line in out)  # suggestion was printed
    # (the test does not pass the suggested model to run_agent; run_agent uses the
    #  worker model wired in main — here the spy just records the prompt.)


def test_6_mock_mode_chat_is_honest(tmp_path):
    out = []
    spy = _spy_agent()
    from trace.chat_handler import ChatHandler
    route_and_dispatch(
        "what is 1+1",
        router=_FixedRouter(_cls("chat_question", confidence=0.97)),
        emitter=_emitter(tmp_path),
        make_chat_handler=lambda: ChatHandler(None),   # mock mode
        run_agent=spy, ask_user=lambda q: "", echo=out.append, worker_model_id=None)
    assert spy.calls == []
    assert any("mock mode" in line for line in out)


def test_7_ambiguous_after_clarification_does_not_run_agent(tmp_path):
    out = []
    spy = _spy_agent()
    route_and_dispatch(
        "do the thing",
        router=_FixedRouter(_cls("ambiguous", confidence=0.2, needs_clarification=True,
                                 clarifying_question="what?"),
                            _cls("ambiguous", confidence=0.2, needs_clarification=True)),
        emitter=_emitter(tmp_path), make_chat_handler=lambda: None,
        run_agent=spy, ask_user=lambda q: "still do the thing",
        echo=out.append, worker_model_id="gpt-5.4")
    assert spy.calls == []                                # agent NEVER ran
    assert any("still unclear" in line.lower() for line in out)


def test_8_eof_and_empty_clarification_fail_safe(tmp_path):
    for answer in [EOFError(), "   "]:
        out = []
        spy = _spy_agent()
        def ask(q, _a=answer):
            if isinstance(_a, BaseException):
                raise _a
            return _a
        route_and_dispatch(
            "the tests are red",
            router=_FixedRouter(_cls("ambiguous", confidence=0.2, needs_clarification=True,
                                     clarifying_question="which?")),
            emitter=_emitter(tmp_path), make_chat_handler=lambda: None,
            run_agent=spy, ask_user=ask, echo=out.append, worker_model_id="gpt-5.4")
        assert spy.calls == []
        assert any("no clarification" in line.lower() for line in out)


def test_9_event_seq_contiguous_with_classified_first(tmp_path):
    from trace.events import Emitter, Event
    em = Emitter(tmp_path / "events.jsonl", clock=lambda: 0.0, console=False)
    def run_agent(prompt):
        # simulate runner events arriving pre-built with their OWN seq 0,1
        for i, t in enumerate(["llm.call", "action"]):
            em.write_renumbered(Event(seq=i, t=0.0, type=t, data={}))
    route_and_dispatch(
        "fix it",
        router=_FixedRouter(_cls("code_fix", confidence=0.9)),
        emitter=em, make_chat_handler=lambda: None,
        run_agent=run_agent, ask_user=lambda q: "", echo=lambda t: None,
        worker_model_id="gpt-5.4")
    em.close()
    rec = [_json.loads(l) for l in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert rec[0]["type"] == "task.classified" and rec[0]["seq"] == 0
    assert [r["seq"] for r in rec] == list(range(len(rec)))   # strictly contiguous


def test_4_thin_client_mock_red_green(tmp_path, monkeypatch):
    # Copy the sample repo into a temp cwd so the run can edit it freely.
    src = Path("examples/sample-repo")
    dst = tmp_path / "sample-repo"
    dst.mkdir()
    for f in ("calculator.py", "test_calculator.py"):
        (dst / f).write_text((src / f).read_text())

    # Pin the run id so we read THIS run's artifacts, not the "latest global" dir
    # (avoids an ordering hazard if another run exists under trace/runs/).
    monkeypatch.setattr(rt, "_run_id", lambda: "pytest-thin-client")

    rc = rt.main(["--model", "mock", "--cwd", str(dst)])
    assert rc == 0

    # The mock fix was applied (genuine red->green preserved through the runner).
    assert "return a + b" in (dst / "calculator.py").read_text()

    # This run's events.jsonl parses; seq is contiguous and the runner's bookend
    # events frame the stream (run.started ... run.finished).
    events_path = rt.REPO_ROOT / "trace" / "runs" / "pytest-thin-client" / "events.jsonl"
    rec = [json.loads(l) for l in events_path.read_text().splitlines()]
    assert [r["seq"] for r in rec] == list(range(len(rec)))
    # task.classified is now first (seq 0); run.started follows; run.finished is last.
    assert rec[0]["type"] == "task.classified"
    assert rec[1]["type"] == "run.started" and rec[-1]["type"] == "run.finished"


def test_4b_client_uses_runner_not_agent_directly():
    """Lock the rewire invariant: run_traced consumes the AgentRunner and does
    NOT import/drive TracingAgent. An AST import check is a zero-cost guard that
    catches any regression reintroducing the direct-agent path (the contiguous-seq
    assertion in test_4 cannot distinguish write_event from emit, since emit calls
    write_event internally)."""
    tree = ast.parse(inspect.getsource(rt))
    imported = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert "MiniSweAgentRunner" in imported, "thin client must import MiniSweAgentRunner"
    assert "TracingAgent" not in imported, "thin client must NOT import TracingAgent"


def test_10_reclassification_is_re_emitted(tmp_path):
    """After a clarification round, a second task.classified must be emitted so the
    trace matches the dispatched decision (not the pre-clarification guess)."""
    from trace.events import Emitter
    import json as _j
    em = Emitter(tmp_path / "events.jsonl", clock=lambda: 0.0, console=False)
    spy = _spy_agent()
    route_and_dispatch(
        "the tests are red",
        router=_FixedRouter(_cls("ambiguous", confidence=0.2, needs_clarification=True,
                                 clarifying_question="which?"),
                            _cls("code_fix", confidence=0.9)),
        emitter=em, make_chat_handler=lambda: None,
        run_agent=spy, ask_user=lambda q: "the pytest suite in examples/",
        echo=lambda t: None, worker_model_id="gpt-5.4")
    em.close()
    rec = [_j.loads(l) for l in (tmp_path / "events.jsonl").read_text().splitlines()]
    classified = [r for r in rec if r["type"] == "task.classified"]
    assert len(classified) == 2                          # initial + re-emit
    assert classified[0]["data"]["task_type"] == "ambiguous"
    assert classified[1]["data"]["task_type"] == "code_fix"   # reflects the dispatch
    assert spy.calls == ["the tests are red"]            # and the agent did run
