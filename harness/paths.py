"""Single source of truth for runtime asset resolution: the XDG config dir,
.env loading precedence, the bundled+user skills roots, and the engine's
mini.yaml. Replaces the REPO_ROOT/__file__ assumptions so a wheel install works
after the source checkout is deleted."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

from dotenv import load_dotenv


def config_dir() -> Path:
    """$XDG_CONFIG_HOME/harness if set & non-empty, else ~/.config/harness.
    Does NOT create the directory."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "harness"


def load_env(project_dir: str | Path | None = None) -> None:
    """Load .env in precedence (process env always wins — override=False):
    process env -> project_dir/.env -> config_dir()/.env. project_dir is the
    project the harness operates on (the TUI --cwd / the agent session cwd);
    the harness never chdir()s, so we anchor explicitly rather than Path.cwd()."""
    candidates = []
    if project_dir is not None:
        candidates.append(Path(project_dir) / ".env")
    candidates.append(config_dir() / ".env")
    for path in candidates:
        if path.is_file():
            load_dotenv(path, override=False)


def mini_yaml_path() -> Path:
    """Locate the engine's config/mini.yaml WITHOUT importing minisweagent
    (its __init__ runs dotenv/global-config side effects). Uses find_spec, which
    resolves the package location without executing it."""
    spec = importlib.util.find_spec("minisweagent")
    locations = list(spec.submodule_search_locations) if spec else []
    if not locations:
        raise RuntimeError("mini-swe-agent config not found; is the engine installed?")
    p = Path(locations[0]) / "config" / "mini.yaml"
    if not p.is_file():
        raise RuntimeError("mini-swe-agent config not found; is the engine installed?")
    return p
