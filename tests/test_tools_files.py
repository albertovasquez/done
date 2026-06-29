from pathlib import Path

from minisweagent.environments.local import LocalEnvironment  # noqa: E402

from harness.tools.edit import EditTool  # noqa: E402
from harness.tools.read import ReadTool  # noqa: E402
from harness.tools.write import WriteTool  # noqa: E402


def test_read_returns_contents_on_hit(tmp_path):
    (tmp_path / "a.txt").write_text("hello\nworld\n")
    out = ReadTool().execute({"path": "a.txt"}, LocalEnvironment(cwd=str(tmp_path)))
    assert out["returncode"] == 0
    assert out["output"] == "hello\nworld\n"
    assert out["exception_info"] is None


def test_read_missing_file_is_returncode_1_not_exception(tmp_path):
    out = ReadTool().execute({"path": "nope.txt"}, LocalEnvironment(cwd=str(tmp_path)))
    assert out["returncode"] == 1
    assert isinstance(out["output"], str) and out["output"]


def test_read_display_label():
    assert ReadTool().display_label({"path": "x/y.py"}) == "read x/y.py"


def test_write_creates_file(tmp_path):
    out = WriteTool().execute({"path": "new.txt", "content": "abc"}, LocalEnvironment(cwd=str(tmp_path)))
    assert out["returncode"] == 0
    assert (tmp_path / "new.txt").read_text() == "abc"


def test_write_overwrites_existing(tmp_path):
    (tmp_path / "f.txt").write_text("old")
    out = WriteTool().execute({"path": "f.txt", "content": "new"}, LocalEnvironment(cwd=str(tmp_path)))
    assert out["returncode"] == 0
    assert (tmp_path / "f.txt").read_text() == "new"


def test_edit_replaces_unique_match(tmp_path):
    (tmp_path / "c.py").write_text("return a - b\n")
    out = EditTool().execute({"path": "c.py", "old_string": "a - b", "new_string": "a + b"},
                             LocalEnvironment(cwd=str(tmp_path)))
    assert out["returncode"] == 0
    assert (tmp_path / "c.py").read_text() == "return a + b\n"


def test_edit_zero_match_is_returncode_1(tmp_path):
    (tmp_path / "c.py").write_text("x = 1\n")
    out = EditTool().execute({"path": "c.py", "old_string": "nope", "new_string": "y"},
                             LocalEnvironment(cwd=str(tmp_path)))
    assert out["returncode"] == 1
    assert (tmp_path / "c.py").read_text() == "x = 1\n"  # unchanged


def test_edit_multi_match_is_returncode_1_and_no_write(tmp_path):
    (tmp_path / "c.py").write_text("v = 1\nv = 1\n")
    out = EditTool().execute({"path": "c.py", "old_string": "v = 1", "new_string": "v = 2"},
                             LocalEnvironment(cwd=str(tmp_path)))
    assert out["returncode"] == 1
    assert (tmp_path / "c.py").read_text() == "v = 1\nv = 1\n"  # unchanged — ambiguous, no silent replace-all


class _Env:
    def __init__(self, cwd, roots=None):
        self.config = type("C", (), {"cwd": str(cwd)})()
        if roots is not None:
            self._allowed_roots = roots


def test_write_uses_resolved_path_override(tmp_path):
    target = tmp_path / "out.txt"
    env = _Env(tmp_path, roots=[tmp_path])
    out = WriteTool().execute({"path": "ignored", "content": "hi",
                               "__resolved_path": target}, env)
    assert out["returncode"] == 0
    assert target.read_text() == "hi"


def test_write_aborts_when_parent_escapes_roots(tmp_path):
    root = tmp_path / "proj"; root.mkdir()
    target = Path("/etc/should_not_write.txt")
    env = _Env(root, roots=[root])
    out = WriteTool().execute({"path": "x", "content": "x",
                               "__resolved_path": target}, env)
    assert out["returncode"] == 1
    assert "outside" in out["output"].lower()
    assert not target.exists()


def test_edit_uses_resolved_path_override(tmp_path):
    target = tmp_path / "f.txt"; target.write_text("alpha beta")
    env = _Env(tmp_path, roots=[tmp_path])
    out = EditTool().execute({"path": "ignored", "old_string": "beta",
                              "new_string": "gamma", "__resolved_path": target}, env)
    assert out["returncode"] == 0
    assert target.read_text() == "alpha gamma"


def test_read_uses_resolved_path_override(tmp_path):
    target = tmp_path / "r.txt"; target.write_text("payload")
    env = _Env(tmp_path, roots=[tmp_path])
    out = ReadTool().execute({"path": "ignored", "__resolved_path": target}, env)
    assert out["returncode"] == 0 and out["output"] == "payload"
