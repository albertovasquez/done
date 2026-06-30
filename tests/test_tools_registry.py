from harness.tools.registry import build_registry  # noqa: E402


def test_registry_returns_fresh_list_each_call():
    assert build_registry() is not build_registry()  # never a shared module-global


def test_registry_contains_bash_with_valid_schema():
    names = [t.name for t in build_registry()]
    assert "bash" in names
    bash = next(t for t in build_registry() if t.name == "bash")
    assert bash.schema["function"]["name"] == "bash"
    assert bash.display_label({"command": "ls -la"}) == "ls -la"


def test_every_tool_satisfies_the_protocol():
    for t in build_registry():
        assert isinstance(t.name, str) and t.name
        assert isinstance(t.schema, dict)
        assert callable(t.display_label)
        assert callable(t.execute)


def test_review_tool_registered_by_default():
    names = [t.name for t in build_registry()]
    assert "review" in names


def test_review_tool_excluded_for_workers():
    names = [t.name for t in build_registry(is_worker=True)]
    assert "review" not in names
