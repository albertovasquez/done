import sys

sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

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
