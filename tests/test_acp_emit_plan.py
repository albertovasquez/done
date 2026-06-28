from harness.acp_emit import parse_plan_command, plan_update


def test_parse_returns_none_for_non_plan():
    assert parse_plan_command("ls -la") is None
    assert parse_plan_command("echo plan") is None        # 'plan' not the program
    assert parse_plan_command("planner --help") is None   # word boundary


def test_parse_basic_entries():
    entries = parse_plan_command('plan "Push + PR:in_progress" "CI + merge:pending"')
    assert entries == [("Push + PR", "in_progress"), ("CI + merge", "pending")]


def test_parse_defaults_status_to_pending():
    entries = parse_plan_command('plan "Just a step"')
    assert entries == [("Just a step", "pending")]


def test_parse_unknown_status_falls_back_to_pending():
    entries = parse_plan_command('plan "Step:bogus"')
    assert entries == [("Step", "pending")]


def test_parse_label_with_colon_keeps_last_as_status():
    # split on the LAST colon so labels may contain colons
    entries = parse_plan_command('plan "fix bug: the thing:completed"')
    assert entries == [("fix bug: the thing", "completed")]


def test_parse_empty_plan_returns_empty_list():
    assert parse_plan_command("plan") == []


def test_parse_tolerates_leading_dollar_and_whitespace():
    assert parse_plan_command('  $ plan "A:completed" ') == [("A", "completed")]


def test_parse_malformed_quotes_returns_none():
    # unbalanced quotes -> shlex raises -> treat as "not a plan command"
    assert parse_plan_command('plan "unterminated') is None


def test_plan_update_builds_acp_object():
    upd = plan_update([("Push + PR", "in_progress"), ("CI + merge", "pending")])
    assert getattr(upd, "session_update", None) == "plan"
    assert [(e.content, e.status) for e in upd.entries] == [
        ("Push + PR", "in_progress"), ("CI + merge", "pending"),
    ]
