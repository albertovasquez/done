from harness.jobs.commands import parse_command


def test_parse_verbs():
    assert parse_command("disable weekly report cron") == ("disable", "weekly report cron")
    assert parse_command("remove customer import") == ("remove", "customer import")
    assert parse_command("enable nightly sync") == ("enable", "nightly sync")


def test_parse_rejects_unknown_and_deferred_run():
    assert parse_command("please do something") is None
    assert parse_command("") is None
    assert parse_command("run nightly sync") is None   # 'run' deferred (needs executor)
