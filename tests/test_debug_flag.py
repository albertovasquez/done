from harness.debug_flag import resolve_debug


def test_flag_wins():
    assert resolve_debug(True, {}, None) is True


def test_env_enables():
    assert resolve_debug(False, {"HARNESS_DEBUG": "1"}, None) is True
    assert resolve_debug(False, {"HARNESS_DEBUG": "0"}, None) is False


def test_conf_enables():
    assert resolve_debug(False, {}, True) is True


def test_default_off():
    assert resolve_debug(False, {}, None) is False
    assert resolve_debug(False, {}, False) is False
