import re
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


def _requires_python_floor() -> tuple[int, int]:
    """Parse the lower bound of `requires-python` from pyproject.toml.

    Avoids a tomllib dependency (the very thing under test) by reading the
    line directly."""
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'requires-python\s*=\s*"([^"]+)"', text)
    assert m, "requires-python not found in pyproject.toml"
    floor = re.search(r">=\s*(\d+)\.(\d+)", m.group(1))
    assert floor, f"no >= lower bound in requires-python: {m.group(1)!r}"
    return int(floor.group(1)), int(floor.group(2))


def test_requires_python_floor_supports_tomllib():
    """`tomllib` is stdlib only since 3.11 and is imported unconditionally on
    the boot path (config.py, persona_config.py). The declared floor must not
    promise a Python the code cannot run on. Regression for #103."""
    sources = "\n".join(
        (REPO / "harness" / name).read_text(encoding="utf-8")
        for name in ("config.py", "persona_config.py")
    )
    assert "import tomllib" in sources, (
        "test premise stale: tomllib no longer imported on the boot path"
    )
    assert _requires_python_floor() >= (3, 11), (
        "requires-python floor promises a Python without stdlib tomllib"
    )


def test_wheel_includes_tui_assets_and_skills(tmp_path):
    whl = _build_wheel(tmp_path)
    names = zipfile.ZipFile(whl).namelist()
    assert any(n.endswith("harness/tui/app.tcss") for n in names), names
    assert any(n.endswith("harness/tui/widgets/select_modal.py") for n in names), names
    assert any(n.endswith("/SKILL.md") and "harness/skills/" in n for n in names), names
    assert any(n.endswith("harness/templates/agents/default/SOUL.md") for n in names), names
