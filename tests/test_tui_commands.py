import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

import asyncio
from pathlib import Path

import pytest

from harness.tui.commands import build_registry, filter_commands
from harness.tui.app import HarnessTui
from harness.tui.widgets.select_modal import SelectModal, SelectOption
from textual.widgets import Input, ListView
from harness.tui.widgets.prompt_area import PromptArea

REPO = Path(__file__).resolve().parent.parent
# Running interpreter (portable across worktrees / any cwd), not a hardcoded
# REPO/.venv path which doesn't exist in a git worktree.
FAKE_CMD = [sys.executable, str(REPO / "tests/fake_agent.py")]
AGENT_CMD = [sys.executable, "-m", "harness.acp_main", "--model", "vibeproxy"]


def _vibeproxy_up() -> bool:
    try:
        import urllib.request
        with urllib.request.urlopen("http://localhost:8317/v1/models", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


needs_vibeproxy = pytest.mark.skipif(
    not _vibeproxy_up(),
    reason="VibeProxy not reachable at localhost:8317 — /models tests skipped",
)


# ---------------------------------------------------------------------------
# pure: command registry + filter
# ---------------------------------------------------------------------------

def test_registry_has_core_commands():
    names = {c.name for c in build_registry()}
    assert {"models", "exit", "help"} <= names


def test_quit_is_an_alias_of_exit_not_a_separate_entry():
    cmds = build_registry()
    names = [c.name for c in cmds]
    assert "quit" not in names, "quit must not be its own menu entry"
    exit_cmd = next(c for c in cmds if c.name == "exit")
    assert "quit" in exit_cmd.aliases, "quit must be an alias of exit"


def test_resolve_command_matches_name_and_alias():
    from harness.tui.commands import resolve_command
    cmds = build_registry()
    assert resolve_command(cmds, "exit").name == "exit"     # canonical name
    assert resolve_command(cmds, "quit").name == "exit"     # exact alias → exit
    assert resolve_command(cmds, "qu") is None              # alias is exact-match only
    assert resolve_command(cmds, "nope") is None


def test_filter_empty_returns_all():
    cmds = build_registry()
    assert filter_commands(cmds, "") == cmds


def test_filter_prefix_ranks_first():
    cmds = build_registry()
    out = filter_commands(cmds, "ex")
    assert out and out[0].name == "exit"


def test_filter_no_match_is_empty():
    assert filter_commands(build_registry(), "zzz") == []


# ---------------------------------------------------------------------------
# pilot: slash menu open / filter / close
# ---------------------------------------------------------------------------

def test_slash_menu_opens_filters_closes():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            inp = app.query_one("#landing-input", PromptArea)
            inp.focus()
            inp.value = "/"
            await pilot.pause(); await pilot.pause()
            assert app._slash is not None, "slash menu should open on '/'"

            inp.value = "/ex"
            await pilot.pause()
            hc = app._slash.highlighted_command()
            assert hc is not None and hc.name == "exit", f"filter '/ex' → {hc}"

            inp.value = ""
            await pilot.pause(); await pilot.pause()
            assert app._slash is None, "slash menu should close when '/' is cleared"

    asyncio.run(go())


def test_slash_exit_quits_app():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            inp = app.query_one("#landing-input", PromptArea)
            inp.focus()
            inp.value = "/exit"
            await pilot.pause()
            await app._run_slash("/exit")
            await pilot.pause()
        # reaching here means the run_test context exited cleanly (app.exit() fired)

    asyncio.run(go())


def test_slash_quit_alias_quits_app():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            inp = app.query_one("#landing-input", PromptArea)
            inp.focus()
            inp.value = "/quit"
            await pilot.pause()
            await app._run_slash("/quit")        # typed alias resolves to exit
            await pilot.pause()
            assert app._exit is True, "/quit must exit the app"
        # reaching here means the run_test context exited cleanly

    asyncio.run(go())


def test_models_in_mock_mode_shows_message_no_modal():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            await app.action_select_model()
            await pilot.pause()
            assert not isinstance(app.screen, SelectModal), (
                "mock mode must NOT open the model modal (no provider model list)")

    asyncio.run(go())


# ---------------------------------------------------------------------------
# pilot: SelectModal generic behavior (search + select), no network
# ---------------------------------------------------------------------------

@needs_vibeproxy
def test_models_real_switch_round_trip():
    """With VibeProxy up: /models fetches the live list, opens the modal, and
    picking a model hot-swaps the worker model via ext_method on the real agent."""
    async def go():
        app = HarnessTui(agent_cmd=AGENT_CMD, cwd=str(REPO), model="vibeproxy",
                         worker_model_id="gpt-5.4")
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app._session_id, "agent did not connect"
            await app.action_select_model()
            for _ in range(40):
                await pilot.pause()
                if isinstance(app.screen, SelectModal):
                    break
            assert isinstance(app.screen, SelectModal), "model modal did not open"
            n = len(app.screen.query_one("#select-list", ListView).children)
            assert n >= 5, f"expected several models from the provider, got {n}"
            app.screen.dismiss("claude-opus-4-8")
            for _ in range(40):
                await pilot.pause()
                if app._worker_model_id == "claude-opus-4-8":
                    break
            assert app._worker_model_id == "claude-opus-4-8", (
                f"worker model not hot-swapped: {app._worker_model_id}")

    asyncio.run(go())


def test_registry_includes_reload_and_clear():
    from harness.tui.commands import build_registry
    names = [c.name for c in build_registry()]
    assert "reload" in names
    assert "clear" in names

def test_reload_clear_handlers_delegate_to_app_actions():
    import asyncio
    from harness.tui.commands import build_registry

    class _App:
        def __init__(self): self.called = []
        async def action_reload(self): self.called.append("reload")
        async def action_clear(self): self.called.append("clear")

    reg = {c.name: c for c in build_registry()}
    app = _App()
    asyncio.run(reg["reload"].handler(app))
    asyncio.run(reg["clear"].handler(app))
    assert app.called == ["reload", "clear"]


def test_reload_clear_descriptions_match_new_behavior():
    from harness.tui.commands import build_registry
    reg = {c.name: c for c in build_registry()}
    assert reg["reload"].description == "Reload everything (restart the app)"
    assert reg["clear"].description == "Fresh conversation (restart the agent)"


def test_registry_has_yolo():
    names = {c.name for c in build_registry()}
    assert "yolo" in names


def test_yolo_handler_dispatches_on_arg():
    import asyncio
    from harness.tui.commands import build_registry

    class _App:
        def __init__(self): self.calls = []
        def action_toggle_yolo(self): self.calls.append("toggle")
        async def action_yolo_pin(self): self.calls.append("pin")
        async def action_yolo_unpin(self): self.calls.append("unpin")
        def _notify_line(self, m): self.calls.append(("notify", m))

    reg = {c.name: c for c in build_registry()}
    app = _App()
    asyncio.run(reg["yolo"].handler(app, ""))
    asyncio.run(reg["yolo"].handler(app, "pin"))
    asyncio.run(reg["yolo"].handler(app, "unpin"))
    assert app.calls[:3] == ["toggle", "pin", "unpin"]


def test_existing_handlers_accept_optional_arg():
    """Adding the arg param must not break the no-arg call convention."""
    import asyncio
    from harness.tui.commands import build_registry

    class _App:
        def __init__(self): self.called = []
        async def action_reload(self): self.called.append("reload")
        async def action_clear(self): self.called.append("clear")

    reg = {c.name: c for c in build_registry()}
    app = _App()
    asyncio.run(reg["reload"].handler(app))        # no arg — still valid
    asyncio.run(reg["reload"].handler(app, ""))    # with arg — also valid
    assert app.called == ["reload", "reload"]


def test_reconcile_yolo_corrects_failed_unpin():
    """The silent-bypass hazard: if /yolo unpin's write fails, the agent reports
    pinned=True and the chip must be corrected back to pinned + the user told."""
    from harness.tui.app import HarnessTui

    app = HarnessTui.__new__(HarnessTui)        # bypass __init__/Textual mount
    app._yolo = True
    app._yolo_pinned = False                    # optimistic (wrong) state
    notes: list[str] = []
    app._notify_line = lambda m: notes.append(m)
    app._refresh_yolo_chip = lambda: None

    # agent reports the write FAILED → still pinned on disk
    app._reconcile_yolo({"ok": False, "active": True, "pinned": True},
                        want_pinned=False, verb="unpin")
    assert app._yolo_pinned is True             # chip corrected to the truth
    assert notes and "did not persist" in notes[0]


def test_reconcile_yolo_no_response_warns():
    from harness.tui.app import HarnessTui

    app = HarnessTui.__new__(HarnessTui)
    app._yolo = True
    app._yolo_pinned = False
    notes: list[str] = []
    app._notify_line = lambda m: notes.append(m)
    app._refresh_yolo_chip = lambda: None

    app._reconcile_yolo(None, want_pinned=False, verb="unpin")
    assert notes and "agent unavailable" in notes[0]


def test_reconcile_yolo_success_is_quiet():
    from harness.tui.app import HarnessTui

    app = HarnessTui.__new__(HarnessTui)
    app._yolo = True
    app._yolo_pinned = True
    notes: list[str] = []
    app._notify_line = lambda m: notes.append(m)
    app._refresh_yolo_chip = lambda: None

    app._reconcile_yolo({"ok": True, "active": True, "pinned": True},
                        want_pinned=True, verb="pin")
    assert notes == []                          # success: no noise
    assert app._yolo_pinned is True


def test_select_modal_search_and_select():
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        chosen = {}
        async with app.run_test() as pilot:
            await pilot.pause()
            options = [SelectOption(id="a-one", label="Alpha One"),
                       SelectOption(id="b-two", label="Beta Two"),
                       SelectOption(id="c-three", label="Gamma Three")]

            def cb(value):
                chosen["v"] = value

            app.push_screen(
                SelectModal(title="Pick", options=options, current="b-two",
                            footer="esc cancel"),
                cb,
            )
            await pilot.pause()
            modal = app.screen
            assert isinstance(modal, SelectModal)
            # search narrows the list
            search = modal.query_one("#select-search", Input)
            search.value = "beta"
            await pilot.pause()
            lv = modal.query_one("#select-list", ListView)
            assert len(lv.children) == 1, "search 'beta' should leave one row"
            # submit the search → selects the highlighted (only) row
            modal._submit_search()
            await pilot.pause()
        assert chosen.get("v") == "b-two", f"expected b-two, got {chosen}"

    asyncio.run(go())
