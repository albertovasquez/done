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


def _vendored_engine_version() -> str:
    """The version the vendored `upstream/` tree actually is, read from its
    UPSTREAM_VERSION stamp (line 1: `mini-swe-agent <version>`)."""
    text = (REPO / "upstream" / "UPSTREAM_VERSION").read_text(encoding="utf-8")
    m = re.search(r"mini-swe-agent\s+(\d+\.\d+\.\d+)", text)
    assert m, f"could not parse version from UPSTREAM_VERSION: {text!r}"
    return m.group(1)


def test_engine_dependency_pinned_to_vendored_version():
    """`[tool.uv.sources]` redirects mini-swe-agent to `upstream/` for uv only;
    plain pip ignores it and resolves the bare `mini-swe-agent` name from PyPI.
    Pinning the dependency to `==<vendored version>` makes pip resolve the SAME
    engine uv uses locally, instead of whatever floats on PyPI. Regression for
    #104."""
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    version = _vendored_engine_version()
    m = re.search(r'"mini-swe-agent([^"]*)"', text)
    assert m, "mini-swe-agent dependency not found in pyproject.toml"
    spec = m.group(1).strip()
    assert spec == f"=={version}", (
        f"engine dep must be pinned to =={version} (the vendored upstream/ "
        f"version) so pip resolves the same engine as uv; got {spec!r}"
    )


def test_no_hardcoded_upstream_disk_path_for_mini_yaml():
    """`subagent.py` and `executor.py` resolved mini.yaml via a hardcoded
    `<repo>/upstream/src/minisweagent/config/mini.yaml` path relative to
    `__file__`. That path only exists in a source checkout — a pip/uv WHEEL
    install (where `upstream/` is not shipped) has no such file, so those code
    paths break even after the dependency is pinned. They must resolve the
    config through `paths.mini_yaml_path()` (find_spec-based, install-layout
    agnostic). Regression for #104."""
    for rel in ("harness/tools/subagent.py", "harness/jobs/executor.py"):
        src = (REPO / rel).read_text(encoding="utf-8")
        assert "upstream/src/minisweagent" not in src, (
            f"{rel} still hardcodes the upstream/ disk path for mini.yaml; "
            f"use paths.mini_yaml_path() instead"
        )
        assert 'upstream" / "src"' not in src.replace("'", '"'), (
            f"{rel} still hardcodes the upstream/ disk path (split form) for "
            f"mini.yaml; use paths.mini_yaml_path() instead"
        )


def test_wheel_includes_tui_assets_and_skills(tmp_path):
    whl = _build_wheel(tmp_path)
    names = zipfile.ZipFile(whl).namelist()
    assert any(n.endswith("harness/tui/app.tcss") for n in names), names
    assert any(n.endswith("harness/tui/widgets/select_modal.py") for n in names), names
    assert any(n.endswith("/SKILL.md") and "harness/skills/" in n for n in names), names
    assert any(n.endswith("harness/templates/agents/default/SOUL.md") for n in names), names
