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


def test_invalid_when_duplicate_url_dropped():
    original = "Visit https://x.io and again https://x.io now"
    compressed = "visit https://x.io now"
    assert not validate(original, compressed).is_valid


def test_url_trailing_period_not_part_of_url():
    # a sentence-ending period must not be treated as part of the URL
    original = "See https://example.com. Done."
    compressed = "see https://example.com done"
    assert validate(original, compressed).is_valid


def test_url_matched_whole_including_tld_and_path():
    # the validator must treat the FULL url as one token, not truncate at the first dot
    original = "Go to https://example.com/a/b now"
    compressed = "go https://example.com/a/b"
    assert validate(original, compressed).is_valid
    # and a corruption AFTER the first dot must be caught:
    corrupted = "go https://example.org/a/b"
    assert not validate(original, corrupted).is_valid


def test_url_with_query_string_preserved():
    original = "API https://api.example.com?q=1&x=2 here"
    compressed = "api https://api.example.com?q=1&x=2"
    assert validate(original, compressed).is_valid
