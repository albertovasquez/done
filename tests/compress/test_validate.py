from harness.compress.validate import validate


def test_valid_when_urls_and_code_preserved():
    original = "See https://x.io and `code` and\n```\nblock\n```\n"
    compressed = "see https://x.io `code`\n```\nblock\n```\n"
    assert validate(original, compressed).is_valid


def test_invalid_when_url_dropped():
    original = "Read https://important.example/page now"
    compressed = "read now"
    r = validate(original, compressed)
    assert not r.is_valid
    assert any("http" in e.lower() or "url" in e.lower() for e in r.errors)


def test_invalid_when_code_block_changed():
    original = "```\nkeep me exactly\n```"
    compressed = "```\nkeep ME exactly\n```"
    assert not validate(original, compressed).is_valid
