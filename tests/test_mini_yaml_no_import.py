import subprocess
import sys


def test_mini_yaml_path_does_not_import_minisweagent():
    code = (
        "import sys; sys.path.insert(0,'upstream/src'); sys.path.insert(0,'.');"
        "from harness import paths; p = paths.mini_yaml_path();"
        "assert p.is_file(), p;"
        "assert 'minisweagent' not in sys.modules, 'mini_yaml_path imported the engine';"
        "print('OK')"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "OK" in out.stdout
