import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")


def test_app_constructs_without_running():
    from harness.tui.app import HarnessTui, PermissionModal
    app = HarnessTui(agent_cmd=["x"], cwd=".", model="mock")
    assert app.agent_cmd == ["x"]
    assert app.cwd == "."
    assert app.model == "mock"
    assert PermissionModal  # importable
