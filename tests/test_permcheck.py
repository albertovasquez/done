from pathlib import Path

import pytest

from harness.permcheck import PermissionRequest, classify_path, parent_escapes


def test_request_defaults():
    r = PermissionRequest(kind="file", path=Path("/x"), is_write=True)
    assert r.kind == "file" and r.is_write is True
    assert r.is_exec is False and r.outside_roots is False and r.command is None


def test_relative_path_anchors_to_first_root(tmp_path):
    resolved, outside = classify_path("sub/f.txt", [tmp_path])
    assert resolved == (tmp_path / "sub" / "f.txt").resolve()
    assert outside is False


def test_dotdot_escape_is_outside(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    resolved, outside = classify_path("../secret.txt", [root])
    assert outside is True
    assert ".." not in str(resolved)          # normalized away


def test_absolute_outside_root_is_outside(tmp_path):
    resolved, outside = classify_path("/etc/passwd", [tmp_path])
    assert outside is True


def test_symlink_escape_is_outside(tmp_path):
    root = tmp_path / "proj"; root.mkdir()
    outside_dir = tmp_path / "out"; outside_dir.mkdir()
    (root / "link").symlink_to(outside_dir)   # root/link -> ../out
    resolved, outside = classify_path("link/f.txt", [root])
    assert outside is True


def test_tilde_expands(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    resolved, outside = classify_path("~/f.txt", [tmp_path])
    assert resolved == (tmp_path / "f.txt").resolve()
    assert outside is False


def test_exact_root_is_inside(tmp_path):
    resolved, outside = classify_path(str(tmp_path), [tmp_path])
    assert outside is False


def test_second_root_accepted(tmp_path):
    cwd = tmp_path / "cwd"; cwd.mkdir()
    ws = tmp_path / "ws"; ws.mkdir()
    resolved, outside = classify_path(str(ws / "MEMORY.md"), [cwd, ws])
    assert outside is False


def test_nonexistent_leaf_under_valid_parent(tmp_path):
    # leaf does not exist yet (a fresh write) but parent is inside root
    resolved, outside = classify_path("new.txt", [tmp_path])
    assert outside is False


def test_parent_escapes_true_when_parent_outside(tmp_path):
    root = tmp_path / "proj"; root.mkdir()
    resolved = Path("/etc/x")
    assert parent_escapes(resolved, [root]) is True


def test_parent_escapes_false_when_parent_inside(tmp_path):
    resolved = tmp_path / "a" / "b.txt"
    assert parent_escapes(resolved, [tmp_path]) is False
