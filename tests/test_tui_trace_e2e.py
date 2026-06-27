"""End-to-end --debug trace: drive HarnessTui(debug=True) against the fake agent
over a real subprocess, send a prompt, and assert the unified trace.jsonl
contains BOTH a dn-side event and a relayed agent-side event. Proves the relay
round-trips across the process boundary (the unit tests cover each half)."""
import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

import asyncio
import json
from pathlib import Path

import pytest

from harness.tui.app import HarnessTui
from harness.tui.widgets.prompt_area import PromptArea

REPO = Path(__file__).resolve().parent.parent
FAKE_CMD = [sys.executable, str(REPO / "tests/fake_agent.py")]


def _trace_rows(runs_root: Path):
    files = list(runs_root.rglob("trace.jsonl"))
    assert files, f"no trace.jsonl under {runs_root}"
    return [json.loads(l) for l in files[0].read_text().splitlines() if l.strip()]


def test_debug_trace_captures_both_sources(tmp_path, monkeypatch):
    # redirect the trace file into a tmp runs dir
    monkeypatch.setattr("harness.paths.runs_dir", lambda: tmp_path / "runs")

    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock", debug=True)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#landing-input", PromptArea).focus()
            app.query_one("#landing-input", PromptArea).value = "please TRACE this"
            await pilot.press("enter")
            for _ in range(50):
                await pilot.pause()
                if app._tracer is not None and (tmp_path / "runs").exists():
                    # give the agent's relayed update time to arrive
                    pass
                if app._started:
                    break
            for _ in range(50):
                await pilot.pause()
        # app unmounted → tracer closed/flushed
        rows = _trace_rows(tmp_path / "runs")
        sources = {r["source"] for r in rows}
        types = {r["type"] for r in rows}
        assert "dn" in sources, f"no dn-side events: {rows}"
        assert "agent" in sources, f"relayed agent event missing: {rows}"
        assert "tx.prompt" in types, f"tx.prompt missing: {types}"
        assert "llm.call" in types, f"relayed llm.call missing: {types}"
        # the agent event must be the relayed one, with its sid carried through
        agent_rows = [r for r in rows if r["source"] == "agent"]
        assert any(r["type"] == "llm.call" and r["data"].get("n") == 1
                   for r in agent_rows), f"relayed payload wrong: {agent_rows}"

    asyncio.run(go())


def test_spawn_failure_is_traced(tmp_path, monkeypatch):
    """A failed agent spawn at startup must land in the trace file (spawn.failed),
    not just flash a fatal UI line. The tracer opens before the spawn, so it's
    available to record the failure."""
    monkeypatch.setattr("harness.paths.runs_dir", lambda: tmp_path / "runs")
    bad_cmd = [sys.executable, "-c", "import sys; sys.exit(3)"]  # dies immediately

    async def go():
        app = HarnessTui(agent_cmd=bad_cmd, cwd=str(REPO), model="mock", debug=True)
        async with app.run_test() as pilot:
            for _ in range(50):
                await pilot.pause()
                if app._conn is None and app._tracer is not None:
                    break
        rows = _trace_rows(tmp_path / "runs")
        assert any(r["type"] == "spawn.failed" and r["source"] == "dn" for r in rows), \
            f"spawn failure must be traced; got {[(r['source'], r['type']) for r in rows]}"

    asyncio.run(go())


def test_debug_off_writes_no_trace_file(tmp_path, monkeypatch):
    monkeypatch.setattr("harness.paths.runs_dir", lambda: tmp_path / "runs")

    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")  # debug=False
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#landing-input", PromptArea).focus()
            app.query_one("#landing-input", PromptArea).value = "hello"
            await pilot.press("enter")
            for _ in range(50):
                await pilot.pause()
                if app._started:
                    break
        assert not (tmp_path / "runs").exists(), "no run dir should be created when --debug is off"

    asyncio.run(go())
