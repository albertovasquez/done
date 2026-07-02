from harness.prompt_hash import block_hashes, changed_blocks


def test_hashes_stable_and_distinct():
    h1 = block_hashes({"base": "A", "env": "B"})
    h2 = block_hashes({"base": "A", "env": "B"})
    assert h1 == h2
    assert h1["base"] != h1["env"]
    assert all(len(v) == 8 for v in h1.values())


def test_changed_blocks_names_only_the_diff():
    old = block_hashes({"base": "A", "env": "B", "memory": "M"})
    new = block_hashes({"base": "A", "env": "B2", "memory": "M"})
    assert changed_blocks(old, new) == ["env"]


def test_first_turn_reports_no_change():
    assert changed_blocks(None, block_hashes({"base": "A"})) == []


def test_added_and_removed_blocks_count_as_changed():
    old = block_hashes({"base": "A"})
    new = block_hashes({"base": "A", "memory": "M"})
    assert changed_blocks(old, new) == ["memory"]
    assert changed_blocks(new, old) == ["memory"]
