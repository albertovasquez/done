"""Unit tests for the native-clipboard helper (harness/tui/clipboard.py).

Pure selection + subprocess logic, no Textual — the OSC 52 fallback lives in the
app and is exercised by the pilot tests."""

from harness.tui.clipboard import _native_copy_argv, native_copy


def test_argv_macos_uses_pbcopy():
    assert _native_copy_argv("darwin", env={}) == [["pbcopy"]]


def test_argv_linux_wayland_prefers_wl_copy():
    # WAYLAND_DISPLAY set ⇒ Wayland session ⇒ wl-copy ranks first.
    argv = _native_copy_argv("linux", env={"WAYLAND_DISPLAY": "wayland-0"})
    assert argv[0] == ["wl-copy"]
    # X11 tools remain as later fallbacks
    assert ["xclip", "-selection", "clipboard"] in argv
    assert ["xsel", "--clipboard", "--input"] in argv


def test_argv_linux_x11_prefers_xclip():
    argv = _native_copy_argv("linux", env={"DISPLAY": ":0"})
    assert argv[0] == ["xclip", "-selection", "clipboard"]
    assert ["wl-copy"] not in argv          # no Wayland session → don't offer wl-copy


def test_argv_unknown_platform_is_empty():
    assert _native_copy_argv("sunos", env={}) == []


def test_native_copy_pipes_text_to_first_working_tool():
    calls = []

    def fake_run(argv, text):
        calls.append((argv, text))
        # first candidate "missing" (raises), second succeeds
        if argv == ["wl-copy"]:
            raise FileNotFoundError(argv[0])
        return True

    ok = native_copy(
        "hello world", platform="linux",
        env={"WAYLAND_DISPLAY": "wayland-0"}, runner=fake_run)
    assert ok is True
    assert calls[0][0] == ["wl-copy"]                       # tried Wayland first
    assert calls[1][0] == ["xclip", "-selection", "clipboard"]  # fell through to X11
    assert calls[1][1] == "hello world"                     # text piped through


def test_native_copy_returns_false_when_no_tool_present():
    def always_missing(argv, text):
        raise FileNotFoundError(argv[0])

    ok = native_copy("x", platform="linux", env={"DISPLAY": ":0"},
                     runner=always_missing)
    assert ok is False


def test_native_copy_false_on_unknown_platform():
    ok = native_copy("x", platform="plan9", env={}, runner=lambda a, t: True)
    assert ok is False
