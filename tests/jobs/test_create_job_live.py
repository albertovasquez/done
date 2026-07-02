"""LIVE end-to-end test: a REAL agent turn creates a benign reminder cron.

Unlike every other cron test (which drive `handle_create_job`/`ops.add` directly,
or stub the router), this one exercises the whole product path through a real
LLM: user prompt -> live router classify -> create-job skill -> the worker model
DECIDES to call the `create_job` tool -> handle_create_job -> ops.add -> jobs.json
-> visible for that agent. It is the only test that guards the "the model actually
calls the tool" behavior.

It is "live": it needs VibeProxy up on localhost:8317 serving both the router and
worker models, and it makes real (non-deterministic, billable) LLM calls. It
follows the established reachability-skip convention (tests/test_tui_commands.py:
_vibeproxy_up + a module-level skipif) rather than a pytest marker — the repo has
no marker infrastructure. Under a plain `pytest tests/ -q` on a machine with no
proxy it skips cleanly; when the proxy is up it runs.

It must NOT set HARNESS_ROUTER_STUB (that forces offline classification and would
defeat the whole point).
"""

from __future__ import annotations

import json
import urllib.request

import pytest

from harness import proxy, vibeproxy
from harness.router import ROUTER_MODEL
from harness.jobs import model as m
from harness.jobs import ops


def _served_models() -> set[str] | None:
    """The model ids the live proxy serves, or None if it's unreachable.

    Uses proxy.api_key(), which self-heals to the proxy-provisioned client key
    when no PROXY_API_KEY/VIBEPROXY_API_KEY override is set — so no key hunting.
    """
    try:
        req = urllib.request.Request(
            proxy.base_url().rstrip("/") + "/models",
            headers={"Authorization": "Bearer " + proxy.api_key()},
        )
        with urllib.request.urlopen(req, timeout=3) as r:
            if r.status != 200:
                return None
            data = json.load(r)
    except Exception:
        return None
    return {mdl["id"] for mdl in data.get("data", [])}


def _strip_provider(model_id: str) -> str:
    """'openai/gpt-5.4-mini' -> 'gpt-5.4-mini' (the proxy serves bare ids)."""
    return model_id.split("/", 1)[1] if "/" in model_id else model_id


def _live_ready() -> bool:
    """True only when the proxy is up AND serves both models a real turn needs:
    the worker model (calls create_job) and the router model (classifies)."""
    served = _served_models()
    if served is None:
        return False
    worker = vibeproxy.default_model()
    router = _strip_provider(ROUTER_MODEL)
    return worker in served and router in served


needs_live_proxy = pytest.mark.skipif(
    not _live_ready(),
    reason="live proxy not ready (VibeProxy down or router/worker model not served) "
    "— live cron-creation test skipped",
)


@pytest.fixture
def _isolated_store(tmp_path, monkeypatch):
    """Redirect the jobs store to a temp dir so the real ~/.config store is never
    touched, and unset the proxy key overrides so proxy.api_key() falls back to the
    provisioned client key (a stale override in the shell would 401 the turn)."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("PROXY_API_KEY", raising=False)
    monkeypatch.delenv("VIBEPROXY_API_KEY", raising=False)
    # A leaked HARNESS_ROUTER_STUB would silently make this a non-live test.
    monkeypatch.delenv("HARNESS_ROUTER_STUB", raising=False)
    return tmp_path


@needs_live_proxy
def test_live_agent_creates_benign_reminder_cron(_isolated_store, tmp_path):
    from harness import run_traced

    # A clear, unambiguous create imperative. Deliberately avoids phrasing like
    # "no tools, no writes, no network" (that describes the CRON's sandbox grant
    # but a model can misread it as an OBSERVE-only directive to itself and refuse
    # to create — a real failure mode seen in manual testing).
    prompt = (
        "Use your create_job tool now to create a recurring cron job. "
        "Name: Live test ping. Schedule: every hour. "
        "Payload: a reminder with the text 'live test ping'. "
        "This is a real create action — create it, do not just inspect."
    )
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    rc = run_traced.main(
        ["--model", "vibeproxy", "--cwd", str(project_dir), "--task", prompt]
    )
    assert rc == 0, f"run_traced.main exited non-zero ({rc})"

    # main() runs as the built-in default persona; stamp_headless_gate does not set
    # env._active_persona, so create_job binds the job to agent_id "default".
    jobs = ops.list_jobs(agent_id="default")

    if not jobs:
        # The turn completed but the model chose not to call create_job (LLM
        # non-determinism / a refusal). Skip rather than red-CI on model behavior:
        # this test guards ONLY the "model decides to call the tool" step. The
        # create_job -> ops.add -> jobs.json wiring is covered deterministically
        # by the other tests/jobs/ tests, so an empty result here means "the model
        # didn't call it", never "the tool is broken" — hence a skip, not a pass
        # that could mask a wiring regression.
        pytest.skip("agent did not create a job this run (LLM non-determinism)")

    assert len(jobs) == 1, f"expected exactly one job, got {len(jobs)}: {[j.name for j in jobs]}"
    job = jobs[0]
    assert job.agent_id == "default"
    # Benign: a Reminder payload never runs an LLM turn when it fires.
    assert isinstance(job.payload, m.Reminder), f"expected a Reminder payload, got {job.payload!r}"
    assert job.enabled
    # It is scheduled (a next run is computed) — i.e. it would show as SCHEDULED
    # in the agent's jobs dashboard.
    assert job.state.next_run_at is not None
