from harness.compress import rules


def test_rules_sha256_is_stable_and_changes_with_prompt():
    h1 = rules.rules_sha256()
    assert isinstance(h1, str) and len(h1) == 64  # sha256 hex
    # identical call → identical hash
    assert rules.rules_sha256() == h1


def test_compress_prompt_contains_original_and_rules():
    p = rules.build_compress_prompt("hello world")
    assert "hello world" in p
    assert "code block" in p.lower()  # the preserve-rules are present


def test_strip_wrapper_removes_outer_fence_only():
    wrapped = "```markdown\nbody `inline` here\n```"
    assert rules.strip_llm_wrapper(wrapped) == "body `inline` here"
    assert rules.strip_llm_wrapper("no fence") == "no fence"
