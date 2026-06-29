from harness.tools.registry import build_registry


def _names(reg):
    return {t.name for t in reg}


def test_default_unchanged_is_full_set():
    # No toolset, not a worker => today's behavior (byte-identical no-op).
    names = _names(build_registry())
    assert {"bash", "read", "write", "edit", "create_job"} <= names


def test_toolset_filters_to_named_tools():
    reg = build_registry(toolset={"read", "bash"})
    assert _names(reg) == {"read", "bash"}


def test_worker_denies_subagent_even_if_requested():
    # A worker must never get subagent, even if the toolset names it.
    reg = build_registry(toolset={"read", "bash", "subagent"}, is_worker=True)
    assert "subagent" not in _names(reg)
    assert {"read", "bash"} <= _names(reg)


def test_normal_agent_has_subagent_tool():
    assert "subagent" in _names(build_registry())


def test_worker_never_has_subagent_tool():
    assert "subagent" not in _names(build_registry(is_worker=True))
