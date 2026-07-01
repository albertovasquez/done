import types
from harness.tools.set_next_run import SetNextRunTool


def _env():
    return types.SimpleNamespace()


def test_sets_override_on_env():
    env = _env()
    res = SetNextRunTool().execute({"delay_seconds": 300}, env)
    assert res["returncode"] == 0
    assert env._next_run_override == 300


def test_rejects_zero_and_negative():
    for bad in (0, -5):
        env = _env()
        res = SetNextRunTool().execute({"delay_seconds": bad}, env)
        assert res["returncode"] == 1
        assert not hasattr(env, "_next_run_override")


def test_rejects_non_int():
    env = _env()
    res = SetNextRunTool().execute({"delay_seconds": "soon"}, env)
    assert res["returncode"] == 1
    assert not hasattr(env, "_next_run_override")


def test_name_and_schema_shape():
    t = SetNextRunTool()
    assert t.name == "set_next_run"
    assert t.schema["function"]["name"] == "set_next_run"
