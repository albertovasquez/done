from harness.textgate import _meaningful, _trim, _HTML_COMMENT


def test_meaningful_blank_and_comment_only():
    assert _meaningful("real text") is True
    assert _meaningful("   \n  ") is False
    assert _meaningful("<!-- only a comment -->\n") is False
    assert _meaningful("# Heading") is True          # '#' is markdown, not a comment


def test_trim_caps_and_flags():
    assert _trim("abc", 10) == ("abc", False)
    assert _trim("abcdef", 3) == ("abc", True)


def test_html_comment_regex_is_dotall():
    assert _HTML_COMMENT.sub("", "<!--\nmulti\nline\n-->x") == "x"


def test_persona_reexports_for_backcompat():
    from harness.persona import _meaningful as pm, _trim as pt
    assert pm("x") is True and pt("xy", 1) == ("x", True)
