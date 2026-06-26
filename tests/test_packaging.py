import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _build_wheel(tmp_path) -> Path:
    for cmd in (
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(tmp_path)],
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path)],
    ):
        try:
            r = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True)
        except FileNotFoundError:
            continue
        if r.returncode == 0:
            wheels = list(tmp_path.glob("*.whl"))
            if wheels:
                return wheels[0]
    pytest.skip("no working wheel builder (python -m build / uv build) available")


def test_wheel_includes_tui_assets_and_skills(tmp_path):
    whl = _build_wheel(tmp_path)
    names = zipfile.ZipFile(whl).namelist()
    assert any(n.endswith("harness/tui/app.tcss") for n in names), names
    assert any(n.endswith("harness/tui/widgets/select_modal.py") for n in names), names
    assert any(n.endswith("/SKILL.md") and "harness/skills/" in n for n in names), names
    assert any(n.endswith("harness/templates/agents/default/SOUL.md") for n in names), names
