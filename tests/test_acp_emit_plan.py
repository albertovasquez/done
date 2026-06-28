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


def test_parse_rejects_chained_shell_command():
    # `plan ... && <real command>` is NOT a pure plan: the agent chained a real
    # command onto the sentinel. Returning None runs the whole line as shell
    # instead of shredding its tokens into checklist rows.
    assert parse_plan_command('plan "Step:in_progress" && gh issue list') is None
    assert parse_plan_command('plan "Step:in_progress" | python3 -') is None
    assert parse_plan_command('plan "Step:in_progress"; ls') is None


def test_parse_rejects_heredoc_command():
    # the real bug: a multi-line `gh ... <<PY ... PY` reached the parser still
    # tokenised as a `plan` command and every word became a pending row.
    cmd = 'plan "Step:in_progress" && gh issue list --json number | python3 - <<PY\nimport json\nPY'
    assert parse_plan_command(cmd) is None


def test_parse_keeps_operators_inside_quoted_label():
    # an operator INSIDE a quoted label is part of the label, not shell control;
    # it must still parse (guards the existing "Push + PR" / "CI + merge" case).
    entries = parse_plan_command('plan "build && test:in_progress" "a | b:pending"')
    assert entries == [("build && test", "in_progress"), ("a | b", "pending")]


def test_parse_keeps_label_containing_semicolon_or_amp():
    # operator chars MID-label are part of the label; only a chained operator
    # token (standalone, heredoc, or trailing ;/&) marks a real command.
    entries = parse_plan_command('plan "fetch A; render B:completed" "x & y:pending"')
    assert entries == [("fetch A; render B", "completed"), ("x & y", "pending")]


def test_plan_update_builds_acp_object():
    upd = plan_update([("Push + PR", "in_progress"), ("CI + merge", "pending")])
    assert getattr(upd, "session_update", None) == "plan"
    assert [(e.content, e.status) for e in upd.entries] == [
        ("Push + PR", "in_progress"), ("CI + merge", "pending"),
    ]
