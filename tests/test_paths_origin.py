from pathlib import Path

from harness.paths import (
    origin_for_root, bundled_skills_dir, config_dir, skills_dirs,
)


def test_bundled_root_is_bundled():
    assert origin_for_root(bundled_skills_dir()) == "bundled"


def test_user_roots_are_user():
    assert origin_for_root(Path.home() / ".claude" / "skills") == "user"
    assert origin_for_root(config_dir() / "skills") == "user"


def test_project_roots_are_project_only_with_cwd():
    cwd = Path("/some/proj")
    assert origin_for_root(cwd / ".claude" / "skills", project_cwd=cwd) == "project"
    assert origin_for_root(cwd / ".agents" / "skills", project_cwd=cwd) == "project"
    # without project_cwd, the same paths are NOT classified as project
    assert origin_for_root(cwd / ".claude" / "skills") == "unknown"


def test_unmatched_root_is_unknown():
    assert origin_for_root(Path("/totally/unrelated/dir")) == "unknown"


def test_every_skills_dir_classifies_without_unknown():
    cwd = Path("/proj")
    for root in skills_dirs(project_cwd=cwd):
        assert origin_for_root(root, project_cwd=cwd) in {"bundled", "user", "project"}
