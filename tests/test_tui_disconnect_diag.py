"""Disconnect diagnostics: _format_exit rendering + the _send_prompt disconnect
handler surfacing exit cause and buffered stderr. Exercised directly on a bare
HarnessTui (no Textual app, no subprocess), mirroring test_tui_stderr_drain.py.
"""

import asyncio
import signal
import sys

sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

from harness.tui.app import HarnessTui


def _bare_app():
    return HarnessTui(agent_cmd=["x"], cwd=".", model="mock")


def test_format_exit_none_is_unknown():
    assert HarnessTui._format_exit(None) == "exit status unknown"


def test_format_exit_zero_is_clean_code():
    assert HarnessTui._format_exit(0) == "exited with code 0"


def test_format_exit_positive_code():
    assert HarnessTui._format_exit(3) == "exited with code 3"


def test_format_exit_signal_name():
    # -11 = killed by SIGSEGV
    assert HarnessTui._format_exit(-signal.SIGSEGV) == "killed by SIGSEGV"


def test_format_exit_unrecognized_signal_falls_back():
    # an absurd negative value with no Signals member must not raise
    out = HarnessTui._format_exit(-9999)
    assert out == "killed by signal 9999", out
