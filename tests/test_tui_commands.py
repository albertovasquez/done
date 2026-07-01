import sys

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


def test_persona_command_registered():
    names = {c.name for c in build_registry()}
    assert "persona" in names


def test_persona_command_opens_rail():
    # /persona opens the agents rail (view-only; switching is deferred to C2c).
    async def _run():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            drawer = app.query_one("#agent-drawer")
            assert drawer.display is False
            from harness.tui.commands import build_registry, resolve_command
            cmd = resolve_command(build_registry(), "persona")
            await cmd.handler(app, "")
            await pilot.pause()
            assert drawer.display is True
    asyncio.run(_run())


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


def test_registry_has_goal():
    assert "goal" in {c.name for c in build_registry()}


class _GoalApp:
    """Minimal app double for the /goal handler: records ext calls + seeds."""
    def __init__(self):
        self.calls = []
        self._active_goal_text = None
        class _Conn:
            def __init__(self, outer): self.outer = outer
            async def ext_method(self, method, params=None):
                self.outer.calls.append((method, params or {}))
                return {"ok": True}
        self._conn = _Conn(self)
    async def _seed_prompt(self, t): self.calls.append(("seed", t))
    def _notify_line(self, m): self.calls.append(("notify", m))


def test_goal_with_arg_arms_and_seeds():
    import asyncio
    reg = {c.name: c for c in build_registry()}
    app = _GoalApp()
    asyncio.run(reg["goal"].handler(app, "get tests green"))
    methods = [c[0] for c in app.calls]
    assert "harness/set_goal" in methods
    # the set_goal call carried the goal text
    setcall = next(c for c in app.calls if c[0] == "harness/set_goal")
    assert setcall[1]["text"] == "get tests green"
    assert any(c[0] == "seed" for c in app.calls)


def test_goal_clear_disarms():
    import asyncio
    reg = {c.name: c for c in build_registry()}
    app = _GoalApp()
    asyncio.run(reg["goal"].handler(app, "clear"))
    assert "harness/clear_goal" in [c[0] for c in app.calls]


def test_bare_goal_shows_state():
    import asyncio
    reg = {c.name: c for c in build_registry()}
    app = _GoalApp()
    asyncio.run(reg["goal"].handler(app, ""))     # no goal set yet
    notifies = [c for c in app.calls if c[0] == "notify"]
    assert notifies and "no goal" in notifies[0][1].lower()


def test_bare_loop_prefills_composer_on_real_app():
    """Integration: /loop with no arg prefills the real composer (proves the app
    has _prefill_composer wired with the right name, not just a fake)."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test() as pilot:
            await pilot.pause()
            inp = app.query_one("#landing-input", PromptArea)
            inp.focus()
            inp.value = "/loop"
            await pilot.pause()
            await app._run_slash("/loop")
            await pilot.pause()
            # composer now holds the template, ready for the user to complete
            assert "loop" in app._active_input().value.lower(), (
                f"bare /loop should prefill a template, got {app._active_input().value!r}")

    asyncio.run(go())


def test_registry_has_loop():
    names = {c.name for c in build_registry()}
    assert "loop" in names


def test_resolve_loop_by_name():
    from harness.tui.commands import resolve_command
    cmd = resolve_command(build_registry(), "loop")
    assert cmd is not None and cmd.name == "loop"


def test_loop_with_arg_seeds_a_create_loop_prompt():
    """/loop <text> submits a create-loop request through the normal gated chat
    flow (which drives the create_loop tool)."""
    import asyncio
    from harness.tui.commands import build_registry

    class _App:
        def __init__(self): self.seeded = []
        async def _seed_prompt(self, text): self.seeded.append(text)
        def _prefill_composer(self, text): self.seeded.append(("prefill", text))

    reg = {c.name: c for c in build_registry()}
    app = _App()
    asyncio.run(reg["loop"].handler(app, "watch the deploy and pace yourself"))
    assert len(app.seeded) == 1
    sent = app.seeded[0]
    assert isinstance(sent, str)                       # submitted, not prefilled
    # The seeded prompt must name a self-paced loop and carry the user's ask.
    assert "loop" in sent.lower()
    assert "watch the deploy and pace yourself" in sent


def test_loop_without_arg_prefills_a_template():
    """Bare /loop can't submit a meaningful request, so it prefills the composer
    with a template for the user to fill in (mirrors the CHAT_ABOUT_IT fallback)."""
    import asyncio
    from harness.tui.commands import build_registry

    class _App:
        def __init__(self): self.seeded = []
        async def _seed_prompt(self, text): self.seeded.append(("submit", text))
        def _prefill_composer(self, text): self.seeded.append(("prefill", text))

    reg = {c.name: c for c in build_registry()}
    app = _App()
    asyncio.run(reg["loop"].handler(app, ""))
    assert len(app.seeded) == 1
    kind, text = app.seeded[0]
    assert kind == "prefill"                            # prefilled, not submitted
    assert "loop" in text.lower()


def test_registry_has_compress_aware():
    names = {c.name for c in build_registry()}
    assert "compress-aware" in names


def test_resolve_compress_aware_by_name():
    from harness.tui.commands import resolve_command
    cmds = build_registry()
    cmd = resolve_command(cmds, "compress-aware")
    assert cmd is not None and cmd.name == "compress-aware"


def test_compress_aware_handler_dispatches_on_arg():
    import asyncio
    from harness.tui.commands import build_registry

    class _App:
        def __init__(self): self.calls = []
        def action_toggle_compress_aware(self): self.calls.append("toggle")
        def action_compress_aware_pin(self): self.calls.append("pin")
        def action_compress_aware_unpin(self): self.calls.append("unpin")
        def _notify_line(self, m): self.calls.append(("notify", m))

    reg = {c.name: c for c in build_registry()}
    app = _App()
    asyncio.run(reg["compress-aware"].handler(app, ""))
    asyncio.run(reg["compress-aware"].handler(app, "pin"))
    asyncio.run(reg["compress-aware"].handler(app, "unpin"))
    assert app.calls[:3] == ["toggle", "pin", "unpin"]


def test_compress_aware_handler_unknown_arg_notifies():
    import asyncio
    from harness.tui.commands import build_registry

    class _App:
        def __init__(self): self.calls = []
        def _notify_line(self, m): self.calls.append(("notify", m))

    reg = {c.name: c for c in build_registry()}
    app = _App()
    asyncio.run(reg["compress-aware"].handler(app, "badarg"))
    assert app.calls and app.calls[0][0] == "notify"
    assert "usage" in app.calls[0][1]


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


def test_select_modal_box_hugs_content_no_tall_gap():
    """Regression: the box must hug its content, not stretch to max-height.

    The header is a Vertical (defaults to height: 1fr); without an explicit
    `#select-header { height: auto }` rule it expands and inflates the auto box
    to its 80% cap, leaving a tall empty gap above the search/list. With 14 rows
    the box should be far shorter than the 80%-of-50 = 40 it used to be."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test(size=(80, 50)) as pilot:
            await pilot.pause()
            options = [SelectOption(id=f"m{i}", label=f"model-{i}") for i in range(14)]
            app.push_screen(SelectModal(title="Select model", options=options,
                                        current="m9", footer="esc cancel"))
            await pilot.pause()
            modal = app.screen
            box = modal.query_one("#select-box")
            header = modal.query_one("#select-header")
            # header hugs its content (title only here = 2 rows), not 1fr-stretched
            assert header.outer_size.height <= 4, (
                f"header should hug content, got {header.outer_size.height}")
            # box should fit content (~title+search+list+footer+chrome), well under
            # the old 40-row (80% of 50) blowup.
            assert box.outer_size.height <= 28, (
                f"box should hug content, got {box.outer_size.height} (was 40)")

    asyncio.run(go())


def test_select_modal_arrow_keys_navigate_while_search_focused():
    """Regression: ↑↓ must move the list selection even though the search Input
    holds focus on mount. Before the fix the keys were swallowed by the Input and
    you had to click a row to change the highlight."""
    async def go():
        app = HarnessTui(agent_cmd=FAKE_CMD, cwd=str(REPO), model="mock")
        async with app.run_test(size=(80, 50)) as pilot:
            await pilot.pause()
            options = [SelectOption(id=f"m{i}", label=f"model-{i}") for i in range(14)]
            app.push_screen(SelectModal(title="Select model", options=options,
                                        current="m9", footer="esc cancel"))
            await pilot.pause()
            modal = app.screen
            # focus starts on the search Input, not the list
            assert isinstance(app.focused, Input), (
                f"search Input should hold focus, got {type(app.focused).__name__}")
            lv = modal.query_one("#select-list", ListView)
            assert lv.index == 9, f"current 'm9' should be highlighted, got {lv.index}"
            await pilot.press("down")
            await pilot.pause()
            assert lv.index == 10, f"down should move to 10, got {lv.index}"
            await pilot.press("up")
            await pilot.press("up")
            await pilot.pause()
            assert lv.index == 8, f"two ups from 10 should land on 8, got {lv.index}"

    asyncio.run(go())
