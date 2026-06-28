from pathlib import Path  # noqa: E402

import yaml  # noqa: E402
from minisweagent.environments.local import LocalEnvironment  # noqa: E402

from harness.events import Emitter  # noqa: E402
from harness.models_mock import build_mock_model  # noqa: E402
from harness.streaming_model import StreamingLitellmModel  # noqa: E402
from harness.tracing_agent import TracingAgent  # noqa: E402


def test_streaming_model_default_registry_has_file_tools():
    m = StreamingLitellmModel(model_name="vibeproxy/x", cost_tracking="ignore_errors")
    assert {"bash", "read", "write", "edit"} <= {t.name for t in m.registry}


def test_tracing_agent_default_registry_has_file_tools(tmp_path):
    cfg = yaml.safe_load(Path("upstream/src/minisweagent/config/mini.yaml").read_text())["agent"]
    a = TracingAgent(build_mock_model(), LocalEnvironment(cwd=str(tmp_path)),
                     emitter=Emitter(tmp_path / "e.jsonl", clock=lambda: 0.0, console=False), **cfg)
    assert {"read", "write", "edit"} <= set(a._tools_by_name)
