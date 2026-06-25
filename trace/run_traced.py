#!/usr/bin/env python3
"""Phase-0 entrypoint: run the vendored agent under the live tracer.

  python3 trace/run_traced.py                 # mock (default), zero cost
  python3 trace/run_traced.py --model vibeproxy --task "fix the add bug"

Run via ./run.sh so PYTHONPATH includes upstream/src.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import yaml
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "upstream" / "src"))
sys.path.insert(0, str(REPO_ROOT))

from minisweagent.environments.local import LocalEnvironment  # noqa: E402

from trace.events import Emitter  # noqa: E402
from trace.models_mock import build_mock_model  # noqa: E402
from trace.tracing_agent import TracingAgent  # noqa: E402

DEFAULT_TASK = "Fix the failing test in examples/sample-repo so that add(2, 3) == 5."


def _load_agent_config() -> dict:
    cfg = yaml.safe_load((REPO_ROOT / "upstream/src/minisweagent/config/mini.yaml").read_text())
    return cfg["agent"]


def _build_vibeproxy_model():
    from minisweagent.models.litellm_model import LitellmModel
    return LitellmModel(
        model_name="openai/" + os.getenv("VIBEPROXY_MODEL", "gpt-5.1-codex"),
        model_kwargs={
            "api_base": os.getenv("VIBEPROXY_BASE_URL", "http://localhost:8317/v1"),
            "api_key": os.getenv("VIBEPROXY_API_KEY", "dummy-not-used"),
        },
        cost_tracking="ignore_errors",
    )


def _run_id() -> str:
    # No Date.now in scripts? This is a real process; time is fine here.
    return time.strftime("%Y%m%d-%H%M%S")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase-0 traced mini-swe-agent")
    parser.add_argument("--model", choices=["mock", "vibeproxy"], default="mock")
    parser.add_argument("--task", default=DEFAULT_TASK)
    parser.add_argument("--cwd", default=str(REPO_ROOT / "examples" / "sample-repo"))
    args = parser.parse_args(argv)

    load_dotenv(REPO_ROOT / ".env")  # explicit: mini's own load targets the global dir

    run_dir = REPO_ROOT / "trace" / "runs" / _run_id()
    run_dir.mkdir(parents=True, exist_ok=True)

    emitter = Emitter(run_dir / "events.jsonl", clock=lambda: 0.0, console=True)

    if args.model == "mock":
        model = build_mock_model()
    else:
        model = _build_vibeproxy_model()

    env = LocalEnvironment(cwd=args.cwd)
    agent_cfg = _load_agent_config()
    agent_cfg["output_path"] = str(run_dir / "traj.json")
    agent = TracingAgent(model, env, emitter=emitter, **agent_cfg)

    try:
        agent.run(args.task)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        if args.model == "vibeproxy":
            print(f"\nVibeProxy run failed: {e}\n"
                  f"Is VibeProxy running on {os.getenv('VIBEPROXY_BASE_URL', 'http://localhost:8317/v1')}?",
                  file=sys.stderr)
        else:
            raise
    finally:
        emitter.close()
        print(f"\nevents:     {run_dir / 'events.jsonl'}")
        print(f"trajectory: {run_dir / 'traj.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
