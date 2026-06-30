from harness.output_filters.dispatch import filter_output


def test_dispatch_identity_when_no_filter_matches():
    # No filters registered (Task 1) → output returned unchanged.
    out = "anything at all\nline 2\n"
    assert filter_output("git status", out, 0) == out


def test_dispatch_is_failopen_on_unknown_command():
    assert filter_output("totally-unknown-cmd --x", "raw", 1) == "raw"


from harness.acp_env import AcpEnvironment


def _env(**kw):
    # on_command is required; a no-op callback suffices for execute() tests.
    return AcpEnvironment(cwd=".", on_command=lambda *a: None, **kw)


def test_seam_noop_when_no_filter():
    env = _env()                                   # output_filter defaults to None
    out = env.execute({"command": "printf 'hello\\nworld\\n'"})
    assert out["output"] == "hello\nworld\n"       # byte-identical, unfiltered


def test_seam_applies_injected_filter():
    # Filter uppercases — proves the seam routes output through it.
    env = _env(output_filter=lambda cmd, o, rc: o.upper())
    out = env.execute({"command": "printf 'hello\\n'"})
    assert out["output"] == "HELLO\n"


def test_seam_stamps_savings_bytes_when_filter_shrinks():
    env = _env(output_filter=lambda cmd, o, rc: "x")   # shrinks
    out = env.execute({"command": "printf 'hello\\n'"})
    assert out["_raw_bytes"] == len("hello\n")
    assert out["_filtered_bytes"] == 1


def test_seam_no_savings_keys_without_filter():
    env = _env()
    out = env.execute({"command": "printf 'hi\\n'"})
    assert "_raw_bytes" not in out and "_filtered_bytes" not in out
