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


def test_rejects_non_number():
    env = _env()
    res = SetNextRunTool().execute({"delay_seconds": "soon"}, env)
    assert res["returncode"] == 1
    assert not hasattr(env, "_next_run_override")


def test_rejects_bool():
    # bool is an int subclass — must not be accepted as a delay.
    env = _env()
    res = SetNextRunTool().execute({"delay_seconds": True}, env)
    assert res["returncode"] == 1
    assert not hasattr(env, "_next_run_override")


def test_floors_positive_float():
    # A fractional delay is floored, not rejected (rejecting would pause the loop).
    env = _env()
    res = SetNextRunTool().execute({"delay_seconds": 30.7}, env)
    assert res["returncode"] == 0
    assert env._next_run_override == 30


def test_sub_second_float_rejected():
    # 0.5 floors to 0, which is not a valid (> 0) delay.
    env = _env()
    res = SetNextRunTool().execute({"delay_seconds": 0.5}, env)
    assert res["returncode"] == 1
    assert not hasattr(env, "_next_run_override")


def test_name_and_schema_shape():
    t = SetNextRunTool()
    assert t.name == "set_next_run"
    assert t.schema["function"]["name"] == "set_next_run"
